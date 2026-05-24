"""End-to-end replay proof.

Pre-populates a cassette in DuckDB+blobs, starts mitmproxy with
ReplayAddon, then sends a real HTTPS request through httpx and asserts
that the recorded bytes come back without ever touching the upstream.

Also covers the strict-mode CassetteMissError path so we have proof the
guardrail "replay never silently calls real APIs" actually fires under
realistic load, not just unit-test mock conditions.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from rewind.proxy.normalize import normalize_request
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step
from tests.fixtures.local_llm_server import LocalLLMServer
from tests.fixtures.proxy_runner import find_free_port, running_replay_proxy

_MITM_CA_FILENAME = "mitmproxy-ca-cert.pem"


def _wait_for_mitm_ca(confdir: Path, timeout: float = 10.0) -> Path:
    deadline = time.monotonic() + timeout
    ca_path = confdir / _MITM_CA_FILENAME
    while time.monotonic() < deadline:
        if ca_path.exists() and ca_path.stat().st_size > 0:
            return ca_path
        time.sleep(0.05)
    raise TimeoutError(f"mitmproxy CA cert never appeared at {ca_path}")


def _prepare_cassette(
    db: RewindDB,
    blobs: BlobStore,
    *,
    path: str,
    request_body: dict[str, object],
    response_body: dict[str, object],
) -> str:
    """Insert a single recorded step and return the session id."""
    session = Session(agent_name="e2e-replay")
    db.save_session(session)

    raw_req_body = json.dumps(request_body).encode()
    match_key = normalize_request("POST", path, raw_req_body)

    req_payload = {
        "method": "POST",
        "path": path,
        "headers": {"content-type": "application/json"},
        "body": request_body,
    }
    resp_payload = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(response_body),
    }
    req_blob = blobs.write(json.dumps(req_payload, sort_keys=True).encode())
    resp_blob = blobs.write(json.dumps(resp_payload, sort_keys=True).encode())

    db.save_step(
        Step(
            session_id=session.id,
            order_idx=0,
            type="llm_call",
            provider="test",
            model="test-model-1",
            match_key=match_key,
            req_blob=req_blob,
            resp_blob=resp_blob,
            input_tok=7,
            output_tok=6,
        )
    )
    return session.id


@pytest.mark.integration
def test_replay_serves_recorded_bytes_through_real_proxy(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    confdir = tmp_path / "mitmconf"
    cert_dir = tmp_path / "server_certs"

    expected_response = {
        "id": "msg_replay_e2e_01",
        "model": "test-model-1",
        "content": [{"type": "text", "text": "replayed bytes only"}],
        "usage": {"input_tokens": 7, "output_tokens": 6},
    }

    request_body = {
        "model": "test-model-1",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "say hi"}],
    }

    session_id = _prepare_cassette(
        db,
        blobs,
        path="/v1/messages",
        request_body=request_body,
        response_body=expected_response,
    )

    proxy_port = find_free_port()

    # Server exists only so the proxy has a valid upstream to point at if it
    # ever tries to escape. If the test passes, no request should ever land
    # here, because ReplayAddon must short-circuit before upstream is dialed.
    with LocalLLMServer(cert_dir=cert_dir) as server:
        provider_hosts = {"localhost": "test"}
        with running_replay_proxy(
            db,
            blobs,
            session_id,
            port=proxy_port,
            confdir=confdir,
            provider_hosts=provider_hosts,
            permissive=False,  # strict, the production default
        ):
            mitm_ca = _wait_for_mitm_ca(confdir)
            with httpx.Client(
                proxy=f"http://127.0.0.1:{proxy_port}",
                verify=str(mitm_ca),
                timeout=10.0,
            ) as client:
                resp = client.post(
                    f"{server.url}/v1/messages",
                    json=request_body,
                )

    assert resp.status_code == 200
    body = resp.json()
    assert body == expected_response, "client must receive exactly the recorded bytes"
    # X-Test-Marker from LocalLLMServer would prove upstream was hit. Its
    # absence is positive evidence the proxy never dialed the local server.
    assert "X-Test-Marker" not in resp.headers


@pytest.mark.integration
def test_replay_strict_mode_returns_error_on_cassette_miss(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    confdir = tmp_path / "mitmconf"
    cert_dir = tmp_path / "server_certs"

    # Cassette session with a step matching a DIFFERENT request, so the
    # client's request below will miss.
    session_id = _prepare_cassette(
        db,
        blobs,
        path="/v1/messages",
        request_body={"model": "x", "messages": [{"role": "user", "content": "different"}]},
        response_body={"content": [{"type": "text", "text": "wrong"}]},
    )

    proxy_port = find_free_port()

    with LocalLLMServer(cert_dir=cert_dir) as server:
        with running_replay_proxy(
            db,
            blobs,
            session_id,
            port=proxy_port,
            confdir=confdir,
            provider_hosts={"localhost": "test"},
            permissive=False,
        ):
            mitm_ca = _wait_for_mitm_ca(confdir)
            with httpx.Client(
                proxy=f"http://127.0.0.1:{proxy_port}",
                verify=str(mitm_ca),
                timeout=10.0,
            ) as client:
                resp = client.post(
                    f"{server.url}/v1/messages",
                    json={"model": "y", "messages": [{"role": "user", "content": "unknown"}]},
                )

    # Strict mode must short-circuit with a 599 + structured error body
    # rather than fall through to the real upstream. The local server's
    # X-Test-Marker would have proved upstream was hit; its absence is the
    # invariant. The X-Rewind-Cassette-Miss header carries the match_key
    # so callers can correlate.
    assert resp.status_code == 599
    assert resp.headers.get("X-Rewind-Cassette-Miss")
    err = resp.json()
    assert err["error"] == "cassette_miss"
    assert err["session_id"] == session_id
    assert "X-Test-Marker" not in resp.headers
