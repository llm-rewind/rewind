"""Helpers for running mitmproxy in a background thread inside tests.

mitmproxy is asyncio-based but tests are synchronous. The proxy needs its
own event loop in a dedicated thread so the test body can drive httpx
requests against it without nested-loop issues.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rewind.proxy.record import RecordAddon
from rewind.proxy.replay import ReplayAddon
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB


def find_free_port() -> int:
    """Return a free TCP port. There is a small race between this and the
    next bind, which is unavoidable in tests but never matters in practice."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    """Block until the proxy is accepting connections, or raise TimeoutError."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"Proxy at {host}:{port} did not bind within {timeout}s")


@contextmanager
def running_record_proxy(
    db: RewindDB,
    blobs: BlobStore,
    session_id: str,
    *,
    port: int,
    confdir: Path,
    provider_hosts: dict[str, str],
) -> Iterator[None]:
    """Start mitmproxy with RecordAddon in a daemon thread. Yields once bound."""
    with _running_proxy(
        addon_factory=lambda: RecordAddon(db, blobs, session_id, provider_hosts=provider_hosts),
        port=port,
        confdir=confdir,
    ):
        yield


@contextmanager
def running_replay_proxy(
    db: RewindDB,
    blobs: BlobStore,
    session_id: str,
    *,
    port: int,
    confdir: Path,
    provider_hosts: dict[str, str],
    permissive: bool = False,
) -> Iterator[None]:
    """Start mitmproxy with ReplayAddon in a daemon thread. Yields once bound."""
    with _running_proxy(
        addon_factory=lambda: ReplayAddon(
            db, blobs, session_id, permissive=permissive, provider_hosts=provider_hosts
        ),
        port=port,
        confdir=confdir,
    ):
        yield


@contextmanager
def _running_proxy(*, addon_factory: Any, port: int, confdir: Path) -> Iterator[None]:
    confdir.mkdir(parents=True, exist_ok=True)

    master_ref: list[Any] = []
    ready = threading.Event()
    startup_error: list[BaseException] = []

    def _run() -> None:
        try:
            from mitmproxy.options import Options
            from mitmproxy.tools.dump import DumpMaster

            async def main() -> None:
                opts = Options(
                    listen_host="127.0.0.1",
                    listen_port=port,
                    confdir=str(confdir),
                    # Local test server uses a self-signed cert; mitmproxy
                    # must skip upstream cert validation to forward to it.
                    ssl_insecure=True,
                )
                master = DumpMaster(opts, with_termlog=False, with_dumper=False)
                master.addons.add(addon_factory())
                master_ref.append(master)
                ready.set()
                try:
                    await master.run()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    pass

            asyncio.run(main())
        except BaseException as e:  # pragma: no cover - thread-side error reporting
            startup_error.append(e)
            ready.set()

    t = threading.Thread(target=_run, daemon=True, name=f"proxy-{port}")
    t.start()

    if not ready.wait(timeout=10.0):
        raise TimeoutError("mitmproxy thread failed to signal ready within 10s")
    if startup_error:
        raise startup_error[0]

    wait_for_port("127.0.0.1", port, timeout=10.0)
    try:
        yield
    finally:
        if master_ref:
            try:
                master_ref[0].shutdown()
            except Exception:
                pass
        t.join(timeout=5.0)
