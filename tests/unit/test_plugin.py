"""Unit tests for pytest-rewind cassette format (export/import roundtrip)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from pytest_rewind.cassette import (
    CASSETTE_VERSION,
    export_cassette,
    import_cassette,
    load_cassette_file,
    save_cassette_file,
)
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step


def _make_db_with_session(tmp_path: Path) -> tuple[RewindDB, BlobStore, str]:
    """Helper: create a populated DB+blobs, return (db, blobs, session_id)."""
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs_src")

    session = Session(agent_name="test-agent")
    db.save_session(session)

    req_blob = blobs.write(b'{"method":"POST","body":{"model":"claude-haiku"}}')
    resp_blob = blobs.write(b'{"status_code":200,"body":"Hello from rewind!"}')

    step = Step(
        session_id=session.id,
        order_idx=0,
        type="llm_call",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        match_key="abc123deadbeef",
        req_blob=req_blob,
        resp_blob=resp_blob,
        input_tok=10,
        output_tok=5,
        latency_ms=300,
    )
    db.save_step(step)

    return db, blobs, session.id


@pytest.mark.unit
def test_export_has_correct_version(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)
    assert cassette["version"] == CASSETTE_VERSION


@pytest.mark.unit
def test_export_embeds_all_blobs(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)
    steps = db.get_steps(sid)
    expected_hashes = {
        h for s in steps for h in (s.req_blob, s.resp_blob) if h is not None
    }
    assert set(cassette["blobs"].keys()) == expected_hashes


@pytest.mark.unit
def test_export_blobs_are_valid_base64(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)
    for b64_val in cassette["blobs"].values():
        base64.b64decode(b64_val)  # raises if invalid


@pytest.mark.unit
def test_import_roundtrip_steps(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    db2 = RewindDB(":memory:")
    blobs2 = BlobStore(tmp_path / "blobs_dst")
    session_id = import_cassette(cassette, db2, blobs2)

    assert session_id == sid
    steps2 = db2.get_steps(session_id)
    assert len(steps2) == 1
    assert steps2[0].match_key == "abc123deadbeef"
    assert steps2[0].input_tok == 10
    assert steps2[0].output_tok == 5


@pytest.mark.unit
def test_import_blobs_readable(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    db2 = RewindDB(":memory:")
    blobs2 = BlobStore(tmp_path / "blobs_dst")
    import_cassette(cassette, db2, blobs2)

    steps2 = db2.get_steps(sid)
    resp_hash = steps2[0].resp_blob
    assert resp_hash is not None
    content = blobs2.read(resp_hash)
    assert b"Hello from rewind!" in content


@pytest.mark.unit
def test_import_blob_hash_mismatch_raises(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    # Corrupt one blob entry — wrong hash key
    cassette["blobs"]["deadbeef" * 8] = base64.b64encode(b"tampered").decode()

    db2 = RewindDB(":memory:")
    blobs2 = BlobStore(tmp_path / "blobs_dst")
    with pytest.raises(ValueError, match="integrity check failed"):
        import_cassette(cassette, db2, blobs2)


@pytest.mark.unit
def test_cassette_file_roundtrip(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    rw_path = tmp_path / "test.rw"
    save_cassette_file(cassette, rw_path)

    loaded = load_cassette_file(rw_path)
    assert loaded["version"] == CASSETTE_VERSION
    assert loaded["session"]["id"] == sid
    assert len(loaded["steps"]) == 1


@pytest.mark.unit
def test_cassette_file_is_valid_json(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)

    rw_path = tmp_path / "test.rw"
    save_cassette_file(cassette, rw_path)

    raw = rw_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert "blobs" in parsed
    assert "steps" in parsed
    assert "session" in parsed


@pytest.mark.unit
def test_export_session_metadata_preserved(tmp_path: Path) -> None:
    db, blobs, sid = _make_db_with_session(tmp_path)
    cassette = export_cassette(db, blobs, sid)
    assert cassette["session"]["agent_name"] == "test-agent"
    assert cassette["session"]["id"] == sid


@pytest.mark.unit
def test_import_empty_cassette(tmp_path: Path) -> None:
    """Import a cassette with no steps and no blobs (edge case)."""
    from datetime import UTC, datetime

    session = Session(agent_name="empty-test")
    cassette = {
        "version": CASSETTE_VERSION,
        "session": {
            "id": session.id,
            "agent_name": session.agent_name,
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
    session_id = import_cassette(cassette, db, blobs)

    assert session_id == session.id
    assert db.count_steps(session_id) == 0
