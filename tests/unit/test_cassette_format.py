"""Unit tests for rewind.engines.cassette — export/import format, security invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewind.engines.cassette import export_cassette, import_cassette, save_cassette_file
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step


def _populated_db(tmp_path: Path) -> tuple[RewindDB, BlobStore, str]:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")

    session = Session(agent_name="agent-x")
    db.save_session(session)

    # Simulate a stripped request (no auth headers)
    req_bytes = json.dumps(
        {"method": "POST", "headers": {}, "body": {"model": "gpt-4o", "messages": []}}
    ).encode()
    resp_bytes = json.dumps({"status_code": 200, "headers": {}, "body": '{"choices":[]}'}).encode()

    req_blob = blobs.write(req_bytes)
    resp_blob = blobs.write(resp_bytes)

    step = Step(
        session_id=session.id,
        order_idx=0,
        type="llm_call",
        provider="openai",
        model="gpt-4o",
        match_key="deadbeef12345678",
        req_blob=req_blob,
        resp_blob=resp_blob,
        input_tok=20,
        output_tok=10,
        latency_ms=150,
    )
    db.save_step(step)
    return db, blobs, session.id


@pytest.mark.unit
def test_roundtrip_preserves_all_steps(tmp_path: Path) -> None:
    db, blobs, sid = _populated_db(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    db2 = RewindDB(":memory:")
    blobs2 = BlobStore(tmp_path / "blobs2")
    session_id = import_cassette(cassette, db2, blobs2)

    steps = db2.get_steps(session_id)
    assert len(steps) == 1
    assert steps[0].match_key == "deadbeef12345678"
    assert steps[0].input_tok == 20
    assert steps[0].output_tok == 10


@pytest.mark.unit
def test_roundtrip_blobs_integrity(tmp_path: Path) -> None:
    db, blobs, sid = _populated_db(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    db2 = RewindDB(":memory:")
    blobs2 = BlobStore(tmp_path / "blobs2")
    import_cassette(cassette, db2, blobs2)

    steps = db2.get_steps(sid)
    resp_hash = steps[0].resp_blob
    assert resp_hash is not None
    raw = blobs2.read(resp_hash)
    assert b'"status_code": 200' in raw


@pytest.mark.unit
def test_security_no_authorization_in_export(tmp_path: Path) -> None:
    """Auth headers must never appear in exported cassette content."""
    db, blobs, sid = _populated_db(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    exported_str = json.dumps(cassette)
    assert "Authorization" not in exported_str
    assert "x-api-key" not in exported_str


@pytest.mark.unit
def test_export_raises_if_auth_header_in_blob(tmp_path: Path) -> None:
    """If auth header slips into a blob, export must raise (defense-in-depth)."""
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")

    session = Session(agent_name="bad-agent")
    db.save_session(session)

    # Intentionally write a blob with an Authorization header (shouldn't happen in prod)
    bad_blob = blobs.write(b'{"headers": {"Authorization": "Bearer sk-secret"}}')
    step = Step(
        session_id=session.id,
        order_idx=0,
        type="llm_call",
        req_blob=bad_blob,
    )
    db.save_step(step)

    with pytest.raises(ValueError, match="Authorization"):
        export_cassette(db, blobs, session.id)


@pytest.mark.unit
def test_import_tampered_blob_raises(tmp_path: Path) -> None:
    """Blob with wrong hash key in cassette must be rejected on import."""
    import base64

    db, blobs, sid = _populated_db(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    # Inject a blob entry with a fabricated hash (content ≠ hash)
    cassette["blobs"]["0" * 64] = base64.b64encode(b"this is not the right content").decode()

    db2 = RewindDB(":memory:")
    blobs2 = BlobStore(tmp_path / "blobs2")
    with pytest.raises(ValueError, match="integrity check failed"):
        import_cassette(cassette, db2, blobs2)


@pytest.mark.unit
def test_file_save_load_roundtrip(tmp_path: Path) -> None:
    db, blobs, sid = _populated_db(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    rw_path = tmp_path / "session.rw"
    save_cassette_file(cassette, rw_path)
    assert rw_path.exists()

    raw = rw_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["session"]["id"] == sid
    assert len(parsed["steps"]) == 1
    assert len(parsed["blobs"]) == 2


@pytest.mark.unit
def test_import_empty_cassette_succeeds(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    session = Session(agent_name="empty")
    cassette = {
        "version": 1,
        "session": {
            "id": session.id,
            "agent_name": "empty",
            "git_hash": None,
            "command": None,
            "started_at": datetime.now(UTC).isoformat(),
            "ended_at": None,
            "total_cost_usd": 0.0,
            "metadata": {},
        },
        "steps": [],
        "blobs": {},
    }

    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    sid = import_cassette(cassette, db, blobs)
    assert db.count_steps(sid) == 0
