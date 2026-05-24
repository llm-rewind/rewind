"""mitmproxy DumpMaster lifecycle management.

Embeds mitmproxy as a library (not a subprocess) using the DumpMaster + addon API.
See ADR-001 for why HTTP proxy over SDK monkey-patching.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rewind.constants import DEFAULT_PROXY_PORT, PROXY_HOST, REWIND_DIR
from rewind.proxy.record import RecordAddon
from rewind.proxy.replay import ReplayAddon
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB


async def run_record_proxy(
    db: RewindDB,
    blobs: BlobStore,
    session_id: str,
    *,
    host: str = PROXY_HOST,
    port: int = DEFAULT_PROXY_PORT,
    confdir: Path = REWIND_DIR,
    provider_hosts: dict[str, str] | None = None,
    ssl_insecure: bool = False,
    _stop: asyncio.Event | None = None,
) -> None:
    """Run mitmproxy with RecordAddon. Blocks until _stop is set or KeyboardInterrupt.

    `ssl_insecure` is intended for tests that point upstream at a server with
    a self-signed certificate. Never set it in production.
    """
    from mitmproxy.options import Options
    from mitmproxy.tools.dump import DumpMaster

    opts = Options(
        listen_host=host,
        listen_port=port,
        confdir=str(confdir),
        ssl_insecure=ssl_insecure,
    )
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(  # type: ignore[no-untyped-call]
        RecordAddon(db, blobs, session_id, provider_hosts=provider_hosts)
    )

    if _stop is not None:

        async def _waiter() -> None:
            await _stop.wait()
            master.shutdown()  # type: ignore[no-untyped-call]

        asyncio.create_task(_waiter())

    try:
        await master.run()
    except KeyboardInterrupt:
        pass
    finally:
        master.shutdown()  # type: ignore[no-untyped-call]


async def run_replay_proxy(
    db: RewindDB,
    blobs: BlobStore,
    session_id: str,
    *,
    host: str = PROXY_HOST,
    port: int = DEFAULT_PROXY_PORT,
    confdir: Path = REWIND_DIR,
    permissive: bool = False,
    provider_hosts: dict[str, str] | None = None,
    ssl_insecure: bool = False,
    _stop: asyncio.Event | None = None,
) -> None:
    """Run mitmproxy with ReplayAddon. Blocks until _stop is set or KeyboardInterrupt.

    Replay generally does not contact upstream, so `ssl_insecure` is rarely
    relevant in replay mode unless a cassette miss in permissive mode falls
    through to a self-signed upstream during testing.
    """
    from mitmproxy.options import Options
    from mitmproxy.tools.dump import DumpMaster

    opts = Options(
        listen_host=host,
        listen_port=port,
        confdir=str(confdir),
        ssl_insecure=ssl_insecure,
    )
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(  # type: ignore[no-untyped-call]
        ReplayAddon(db, blobs, session_id, permissive=permissive, provider_hosts=provider_hosts)
    )

    if _stop is not None:

        async def _waiter() -> None:
            await _stop.wait()
            master.shutdown()  # type: ignore[no-untyped-call]

        asyncio.create_task(_waiter())

    try:
        await master.run()
    except KeyboardInterrupt:
        pass
    finally:
        master.shutdown()  # type: ignore[no-untyped-call]
