"""End-to-end recording proof.

Pushes a real HTTPS request from httpx through a real mitmproxy
DumpMaster running RecordAddon, lands at a self-signed local HTTPS test
server, and asserts that the captured step matches the wire bytes.

This is the test that proves the recording loop actually works as a
system. Unit tests with MagicMock flows do not.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session
from tests.fixtures.local_llm_server import LocalLLMServer
from tests.fixtures.proxy_runner import find_free_port, running_record_proxy

# mitmproxy generates its CA into <confdir>/mitmproxy-ca-cert.pem on first run.
# Tests trust that cert so httpx accepts the proxy's MITM-presented cert chain.
_MITM_CA_FILENAME = "mitmproxy-ca-cert.pem"


def _wait_for_mitm_ca(confdir: Path, timeout: float = 10.0) -> Path:
    deadline = time.monotonic() + timeout
    ca_path = confdir / _MITM_CA_FILENAME
    while time.monotonic() < deadline:
        if ca_path.exists() and ca_path.stat().st_size > 0:
            return ca_path
        time.sleep(0.05)
    raise TimeoutError(f"mitmproxy CA cert never appeared at {ca_path}")


@pytest.mark.integration
def test_record_captures_real_http_request(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    confdir = tmp_path / "mitmconf"
    cert_dir = tmp_path / "server_certs"

    session = Session(agent_name="e2e-record")
    db.save_session(session)

    proxy_port = find_free_port()
    request_body = {
        "model": "test-model-1",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "say hi"}],
    }

    with LocalLLMServer(cert_dir=cert_dir) as server:
        provider_hosts = {"localhost": "test"}
        with running_record_proxy(
            db,
            blobs,
            session.id,
            port=proxy_port,
            confdir=confdir,
            provider_hosts=provider_hosts,
        ):
            mitm_ca = _wait_for_mitm_ca(confdir)
            with httpx.Client(
                proxy=f"http://127.0.0.1:{proxy_port}",
                verify=str(mitm_ca),
                timeout=10.0,
            ) as client:
                resp = client.post(
                    f"{server.url}/v1/messages",
                    headers={
                        "Authorization": "Bearer sk-secret-must-be-stripped",
                        "x-api-key": "must-also-be-stripped",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["content"][0]["text"] == "hello from local test server"

    # The thread-mode mitmproxy hands the captured step to RecordAddon's
    # response() hook before shutdown returns. Give the addon a moment to
    # flush to DuckDB if needed.
    for _ in range(50):
        if db.count_steps(session.id) >= 1:
            break
        time.sleep(0.02)

    steps = db.get_steps(session.id)
    assert len(steps) == 1, "RecordAddon should have captured one step"
    step = steps[0]

    assert step.provider == "test"
    assert step.match_key is not None and len(step.match_key) == 64
    assert step.req_blob is not None
    assert step.resp_blob is not None
    assert step.input_tok == 7  # from the local server's default response
    assert step.output_tok == 6

    # Auth headers must be stripped before any blob is written.
    req_payload = json.loads(blobs.read(step.req_blob))
    stored_header_keys = {k.lower() for k in req_payload["headers"]}
    assert "authorization" not in stored_header_keys
    assert "x-api-key" not in stored_header_keys
    # Sanity: non-sensitive header still present.
    assert "content-type" in stored_header_keys

    # Stored response body matches what the client actually received.
    resp_payload = json.loads(blobs.read(step.resp_blob))
    assert resp_payload["status_code"] == 200
    assert "hello from local test server" in resp_payload["body"]
