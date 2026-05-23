"""Unit tests for DuckDB schema (db.py) and blob store (blobs.py)."""

from __future__ import annotations

from datetime import UTC

import pytest

from rewind.exceptions import BlobTamperedError
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step

# --- BlobStore tests ---


@pytest.mark.unit
def test_blob_write_read_roundtrip(tmp_path: pytest.TempdirFactory) -> None:
    store = BlobStore(tmp_path)
    data = b"hello rewind"
    digest = store.write(data)
    assert store.read(digest) == data


@pytest.mark.unit
def test_blob_write_returns_sha256(tmp_path: pytest.TempdirFactory) -> None:
    import hashlib

    store = BlobStore(tmp_path)
    data = b"test content"
    digest = store.write(data)
    assert digest == hashlib.sha256(data).hexdigest()


@pytest.mark.unit
def test_blob_exists(tmp_path: pytest.TempdirFactory) -> None:
    store = BlobStore(tmp_path)
    data = b"exists test"
    assert not store.exists("0" * 64)
    digest = store.write(data)
    assert store.exists(digest)


@pytest.mark.unit
def test_blob_deduplication(tmp_path: pytest.TempdirFactory) -> None:
    store = BlobStore(tmp_path)
    data = b"same content"
    digest1 = store.write(data)
    digest2 = store.write(data)
    assert digest1 == digest2
    # Only one file on disk
    files = list(tmp_path.rglob("*"))
    blob_files = [f for f in files if f.is_file()]
    assert len(blob_files) == 1


@pytest.mark.unit
def test_blob_tampered_raises(tmp_path: pytest.TempdirFactory) -> None:
    store = BlobStore(tmp_path)
    data = b"original content"
    digest = store.write(data)

    # Corrupt the blob file on disk
    import zstandard as zstd

    blob_path = store._path_for(digest)
    corrupted = zstd.ZstdCompressor().compress(b"tampered content")
    blob_path.write_bytes(corrupted)

    with pytest.raises(BlobTamperedError):
        store.read(digest)


@pytest.mark.unit
def test_blob_large_payload(tmp_path: pytest.TempdirFactory) -> None:
    store = BlobStore(tmp_path)
    data = b"x" * 100_000  # 100KB
    digest = store.write(data)
    assert store.read(digest) == data


@pytest.mark.unit
def test_blob_sharding(tmp_path: pytest.TempdirFactory) -> None:
    store = BlobStore(tmp_path)
    data = b"shard test"
    digest = store.write(data)
    # File should be at base/digest[:2]/digest
    expected_path = tmp_path / digest[:2] / digest
    assert expected_path.exists()


# --- RewindDB tests ---


@pytest.mark.unit
def test_db_save_and_get_session() -> None:
    db = RewindDB(":memory:")
    session = Session(agent_name="test_agent")
    db.save_session(session)

    result = db.get_session(session.id)
    assert result is not None
    assert result.id == session.id
    assert result.agent_name == "test_agent"


@pytest.mark.unit
def test_db_get_session_prefix_match() -> None:
    db = RewindDB(":memory:")
    session = Session(agent_name="prefix_test")
    db.save_session(session)

    prefix = session.id[:8]
    result = db.get_session(prefix)
    assert result is not None
    assert result.id == session.id


@pytest.mark.unit
def test_db_get_session_not_found() -> None:
    db = RewindDB(":memory:")
    result = db.get_session("nonexistent-id")
    assert result is None


@pytest.mark.unit
def test_db_list_sessions_empty() -> None:
    db = RewindDB(":memory:")
    assert db.list_sessions() == []


@pytest.mark.unit
def test_db_list_sessions_ordered_by_time() -> None:
    from datetime import datetime, timedelta

    db = RewindDB(":memory:")
    now = datetime.now(UTC)

    s1 = Session(agent_name="first", started_at=now - timedelta(seconds=10))
    s2 = Session(agent_name="second", started_at=now)
    db.save_session(s1)
    db.save_session(s2)

    results = db.list_sessions()
    assert results[0].agent_name == "second"  # most recent first
    assert results[1].agent_name == "first"


@pytest.mark.unit
def test_db_save_and_get_steps() -> None:
    db = RewindDB(":memory:")
    session = Session(agent_name="step_test")
    db.save_session(session)

    step = Step(
        session_id=session.id,
        order_idx=0,
        type="llm_call",
        provider="anthropic",
        model="claude-sonnet-4-6",
        match_key="abc123",
        input_tok=100,
        output_tok=50,
    )
    db.save_step(step)

    steps = db.get_steps(session.id)
    assert len(steps) == 1
    assert steps[0].id == step.id
    assert steps[0].model == "claude-sonnet-4-6"
    assert steps[0].input_tok == 100


@pytest.mark.unit
def test_db_get_steps_ordered_by_order_idx() -> None:
    db = RewindDB(":memory:")
    session = Session(agent_name="order_test")
    db.save_session(session)

    for i in [2, 0, 1]:
        db.save_step(Step(session_id=session.id, order_idx=i, type="llm_call"))

    steps = db.get_steps(session.id)
    assert [s.order_idx for s in steps] == [0, 1, 2]


@pytest.mark.unit
def test_db_steps_empty_for_unknown_session() -> None:
    db = RewindDB(":memory:")
    assert db.get_steps("no-such-session") == []


@pytest.mark.unit
def test_db_session_upsert_updates_ended_at() -> None:
    from datetime import datetime

    db = RewindDB(":memory:")
    session = Session(agent_name="upsert_test")
    db.save_session(session)

    ended = datetime.now(UTC)
    session.ended_at = ended
    session.total_cost_usd = 1.23
    db.save_session(session)

    result = db.get_session(session.id)
    assert result is not None
    assert result.total_cost_usd == pytest.approx(1.23)
