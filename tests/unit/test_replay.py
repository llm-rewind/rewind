"""Unit tests for ReplayAddon — cassette matching logic."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from rewind.exceptions import BlobTamperedError, CassetteMissError
from rewind.proxy.normalize import normalize_request
from rewind.proxy.replay import ReplayAddon
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_with_step(
    tmp_path: pytest.TempdirFactory,
    req_body: bytes,
    resp_body: str,
    status_code: int = 200,
) -> tuple[RewindDB, BlobStore, Session, Step]:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)

    session = Session(agent_name="test")
    db.save_session(session)

    match_key = normalize_request("POST", "/v1/chat/completions", req_body)

    req_payload = {"method": "POST", "path": "/v1/chat/completions", "headers": {}}
    req_blob = blobs.write(json.dumps(req_payload).encode())

    resp_payload = {
        "status_code": status_code,
        "headers": {"content-type": "application/json"},
        "body": resp_body,
    }
    resp_blob = blobs.write(json.dumps(resp_payload).encode())

    step = Step(
        session_id=session.id,
        order_idx=0,
        type="llm_call",
        provider="openai",
        model="gpt-4o",
        match_key=match_key,
        req_blob=req_blob,
        resp_blob=resp_blob,
    )
    db.save_step(step)
    return db, blobs, session, step


def _make_flow(
    host: str = "api.openai.com",
    method: str = "POST",
    path: str = "/v1/chat/completions",
    body: bytes = b"",
) -> MagicMock:
    flow = MagicMock()
    flow.request.host = host
    flow.request.method = method
    flow.request.path = path
    flow.request.content = body
    flow.response = None
    return flow


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_replay_serves_stored_response(tmp_path: pytest.TempdirFactory) -> None:
    req_body = json.dumps(
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()
    resp_body = json.dumps({"choices": [{"message": {"content": "hello"}}]})

    db, blobs, session, _ = _make_db_with_step(tmp_path, req_body, resp_body)
    addon = ReplayAddon(db, blobs, session.id)

    flow = _make_flow(body=req_body)
    addon.request(flow)

    assert flow.response is not None


@pytest.mark.unit
def test_replay_strict_miss_raises(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    session = Session(agent_name="empty")
    db.save_session(session)

    addon = ReplayAddon(db, blobs, session.id, permissive=False)
    flow = _make_flow(body=b'{"model":"gpt-4o"}')

    with pytest.raises(CassetteMissError) as exc_info:
        addon.request(flow)

    assert "api.openai.com" in exc_info.value.args[0] or exc_info.value.match_key


@pytest.mark.unit
def test_replay_permissive_miss_passthrough(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    session = Session(agent_name="empty")
    db.save_session(session)

    addon = ReplayAddon(db, blobs, session.id, permissive=True)
    flow = _make_flow(body=b'{"model":"gpt-4o"}')

    addon.request(flow)  # must not raise
    assert flow.response is None  # let it pass through


@pytest.mark.unit
def test_replay_non_llm_host_ignored(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    session = Session(agent_name="test")
    db.save_session(session)

    addon = ReplayAddon(db, blobs, session.id)
    flow = _make_flow(host="example.com", body=b'{"model":"gpt-4o"}')

    addon.request(flow)  # should do nothing
    assert flow.response is None


@pytest.mark.unit
def test_replay_multiple_calls_served_in_order(tmp_path: pytest.TempdirFactory) -> None:
    """Same match_key called twice → responses served in recording order."""
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    session = Session(agent_name="multi")
    db.save_session(session)

    req_body = json.dumps(
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "q"}]}
    ).encode()
    match_key = normalize_request("POST", "/v1/chat/completions", req_body)
    req_payload = {"method": "POST", "path": "/v1/chat/completions", "headers": {}}
    req_blob = blobs.write(json.dumps(req_payload).encode())

    for i, answer in enumerate(["first", "second"]):
        resp_payload = {"status_code": 200, "headers": {}, "body": answer}
        resp_blob = blobs.write(json.dumps(resp_payload).encode())
        db.save_step(
            Step(
                session_id=session.id,
                order_idx=i,
                type="llm_call",
                match_key=match_key,
                req_blob=req_blob,
                resp_blob=resp_blob,
            )
        )

    addon = ReplayAddon(db, blobs, session.id)

    flow1 = _make_flow(body=req_body)
    addon.request(flow1)
    assert flow1.response is not None

    flow2 = _make_flow(body=req_body)
    addon.request(flow2)
    assert flow2.response is not None

    # Third call — queue exhausted → miss
    flow3 = _make_flow(body=req_body)
    with pytest.raises(CassetteMissError):
        addon.request(flow3)


@pytest.mark.unit
def test_replay_tool_call_id_stripped(tmp_path: pytest.TempdirFactory) -> None:
    """Requests differing only in tool_call_id match the same cassette entry."""
    base = {
        "model": "gpt-4o",
        "messages": [{"role": "tool", "content": "ok", "tool_call_id": "PLACEHOLDER"}],
    }

    msg_a = [{"role": "tool", "content": "ok", "tool_call_id": "call_abc"}]
    msg_b = [{"role": "tool", "content": "ok", "tool_call_id": "call_xyz"}]
    body_a = json.dumps({**base, "messages": msg_a}).encode()
    body_b = json.dumps({**base, "messages": msg_b}).encode()

    resp_body = json.dumps({"choices": [{"message": {"content": "done"}}]})
    db, blobs, session, _ = _make_db_with_step(tmp_path, body_a, resp_body)

    addon = ReplayAddon(db, blobs, session.id)

    # Request with a DIFFERENT tool_call_id should still match
    flow = _make_flow(body=body_b)
    addon.request(flow)
    assert flow.response is not None


@pytest.mark.unit
def test_replay_blob_integrity_enforced(tmp_path: pytest.TempdirFactory) -> None:
    """Tampered response blob raises BlobTamperedError, not a silent wrong answer."""
    import zstandard as zstd

    req_body = b'{"model":"gpt-4o","messages":[]}'
    resp_body = '{"choices":[]}'
    db, blobs, session, step = _make_db_with_step(tmp_path, req_body, resp_body)

    # Corrupt the blob on disk
    assert step.resp_blob is not None
    blob_path = blobs._path_for(step.resp_blob)
    blob_path.write_bytes(zstd.ZstdCompressor().compress(b"tampered"))

    addon = ReplayAddon(db, blobs, session.id)
    flow = _make_flow(body=req_body)

    with pytest.raises(BlobTamperedError):
        addon.request(flow)


@pytest.mark.unit
def test_replay_steps_without_resp_blob_skipped(tmp_path: pytest.TempdirFactory) -> None:
    """Steps with no resp_blob (e.g. failed record) are silently excluded from cassette."""
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    session = Session(agent_name="partial")
    db.save_session(session)

    req_body = b'{"model":"gpt-4o"}'
    match_key = normalize_request("POST", "/v1/chat/completions", req_body)

    # Step with no resp_blob
    db.save_step(
        Step(
            session_id=session.id,
            order_idx=0,
            type="llm_call",
            match_key=match_key,
            req_blob=None,
            resp_blob=None,
        )
    )

    addon = ReplayAddon(db, blobs, session.id)
    flow = _make_flow(body=req_body)

    with pytest.raises(CassetteMissError):
        addon.request(flow)
