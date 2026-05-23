"""Unit tests for diff and bisect engines."""

from __future__ import annotations

import json

import pytest

from rewind.engines.bisect import bisect_sessions
from rewind.engines.diff import DiffStatus, diff_sessions
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(db: RewindDB, name: str) -> Session:
    s = Session(agent_name=name)
    db.save_session(s)
    return s


def _add_step(
    db: RewindDB,
    blobs: BlobStore,
    session_id: str,
    order_idx: int,
    resp_body: str,
    model: str = "gpt-4o",
) -> Step:
    resp_payload = {"status_code": 200, "headers": {}, "body": resp_body}
    resp_blob = blobs.write(json.dumps(resp_payload).encode())
    req_blob = blobs.write(b'{"method":"POST"}')
    step = Step(
        session_id=session_id,
        order_idx=order_idx,
        type="llm_call",
        provider="openai",
        model=model,
        match_key=f"key-{order_idx}",
        req_blob=req_blob,
        resp_blob=resp_blob,
    )
    db.save_step(step)
    return step


# ---------------------------------------------------------------------------
# diff_sessions tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_diff_identical_sessions(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    # Same resp_blob (same content → same hash via content-addressed store)
    _add_step(db, blobs, a.id, 0, "hello")
    _add_step(db, blobs, b.id, 0, "hello")

    result = diff_sessions(db, blobs, a.id, b.id)
    assert result.is_identical
    assert len(result.steps) == 1
    assert result.steps[0].status == DiffStatus.MATCH
    assert result.first_divergence is None


@pytest.mark.unit
def test_diff_detects_divergence(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "same")
    _add_step(db, blobs, b.id, 0, "same")
    _add_step(db, blobs, a.id, 1, "response-A")
    _add_step(db, blobs, b.id, 1, "response-B")

    result = diff_sessions(db, blobs, a.id, b.id)
    assert not result.is_identical
    assert result.steps[0].status == DiffStatus.MATCH
    assert result.steps[1].status == DiffStatus.DIVERGED
    assert result.first_divergence is not None
    assert result.first_divergence.order_idx == 1


@pytest.mark.unit
def test_diff_added_steps(tmp_path: pytest.TempdirFactory) -> None:
    """Session B has more steps than A → extras are ADDED."""
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "x")
    _add_step(db, blobs, b.id, 0, "x")
    _add_step(db, blobs, b.id, 1, "extra")

    result = diff_sessions(db, blobs, a.id, b.id)
    assert result.steps[1].status == DiffStatus.ADDED
    assert result.steps[1].step_a is None
    assert result.steps[1].step_b is not None


@pytest.mark.unit
def test_diff_removed_steps(tmp_path: pytest.TempdirFactory) -> None:
    """Session A has more steps than B → extras are REMOVED."""
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "x")
    _add_step(db, blobs, a.id, 1, "extra")
    _add_step(db, blobs, b.id, 0, "x")

    result = diff_sessions(db, blobs, a.id, b.id)
    assert result.steps[1].status == DiffStatus.REMOVED
    assert result.steps[1].step_b is None


@pytest.mark.unit
def test_diff_empty_sessions(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    result = diff_sessions(db, blobs, a.id, b.id)
    assert result.is_identical
    assert result.steps == []


@pytest.mark.unit
def test_diff_diverged_contains_resp_data(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "answer-A")
    _add_step(db, blobs, b.id, 0, "answer-B")

    result = diff_sessions(db, blobs, a.id, b.id)
    sd = result.steps[0]
    assert sd.status == DiffStatus.DIVERGED
    assert sd.resp_a is not None
    assert sd.resp_b is not None
    assert sd.resp_a["body"] == "answer-A"
    assert sd.resp_b["body"] == "answer-B"


# ---------------------------------------------------------------------------
# bisect_sessions tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bisect_identical(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "same")
    _add_step(db, blobs, b.id, 0, "same")

    result = bisect_sessions(db, blobs, a.id, b.id)
    assert result.is_identical
    assert result.diverged_at is None


@pytest.mark.unit
def test_bisect_finds_first_divergence(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    for i in range(3):
        _add_step(db, blobs, a.id, i, "same" if i < 2 else "diff-A")
        _add_step(db, blobs, b.id, i, "same" if i < 2 else "diff-B")

    result = bisect_sessions(db, blobs, a.id, b.id)
    assert result.diverged_at == 2
    assert not result.model_changed


@pytest.mark.unit
def test_bisect_detects_model_change(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "resp-A", model="gpt-4o")
    _add_step(db, blobs, b.id, 0, "resp-B", model="gpt-4o-mini")

    result = bisect_sessions(db, blobs, a.id, b.id)
    assert result.model_changed
    assert result.diverged_at == 0


@pytest.mark.unit
def test_bisect_summary_not_empty(tmp_path: pytest.TempdirFactory) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a = _make_session(db, "a")
    b = _make_session(db, "b")

    _add_step(db, blobs, a.id, 0, "A")
    _add_step(db, blobs, b.id, 0, "B")

    result = bisect_sessions(db, blobs, a.id, b.id)
    summary = result.summary()
    assert "divergence" in summary.lower() or "step" in summary.lower()
