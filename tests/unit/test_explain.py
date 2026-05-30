"""Unit tests for the causal explanation engine (``rewind explain``).

These exercise the layer explain adds on top of bisect: separating
propagated divergences (changed inputs) from independent ones (identical
request, different response), and the heuristic confidence score.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rewind.engines.bisect import DivergenceCause
from rewind.engines.explain import explain_sessions
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step


def _add_llm_step(
    db: RewindDB,
    blobs: BlobStore,
    session_id: str,
    *,
    order_idx: int,
    model: str = "gpt-4o",
    req_body: dict[str, Any] | None = None,
    resp_body: str = "default response",
    step_type: str = "llm_call",
) -> Step:
    req_payload = {"method": "POST", "path": "/v1/chat", "body": req_body or {"model": model}}
    resp_payload = {"status_code": 200, "headers": {}, "body": resp_body}
    req_blob = blobs.write(json.dumps(req_payload, sort_keys=True).encode())
    resp_blob = blobs.write(json.dumps(resp_payload, sort_keys=True).encode())
    step = Step(
        session_id=session_id,
        order_idx=order_idx,
        type=step_type,
        provider="openai",
        model=model,
        match_key=f"k-{order_idx}",
        req_blob=req_blob,
        resp_blob=resp_blob,
    )
    db.save_step(step)
    return step


def _two_sessions(db: RewindDB) -> tuple[Session, Session]:
    a = Session(agent_name="a")
    b = Session(agent_name="b")
    db.save_session(a)
    db.save_session(b)
    return a, b


@pytest.mark.unit
def test_identical_sessions_have_nothing_to_explain(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    _add_llm_step(db, blobs, a.id, order_idx=0, req_body=body, resp_body="r")
    _add_llm_step(db, blobs, b.id, order_idx=0, req_body=body, resp_body="r")
    result = explain_sessions(db, blobs, a.id, b.id)
    assert result.is_identical
    assert result.root is None
    assert result.confidence == 1.0
    assert "nothing to explain" in result.summary()


@pytest.mark.unit
def test_model_change_root_with_no_downstream(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    _add_llm_step(db, blobs, a.id, order_idx=0, model="gpt-4o", resp_body="A")
    _add_llm_step(db, blobs, b.id, order_idx=0, model="gpt-4o-2026-05", resp_body="B")
    result = explain_sessions(db, blobs, a.id, b.id)
    assert result.root is not None
    assert result.root.cause == DivergenceCause.MODEL_VERSION
    assert result.root.order_idx == 0
    assert not result.propagated
    assert not result.independent
    assert result.confidence == 0.90  # base, no propagation, no independent


@pytest.mark.unit
def test_root_propagates_to_downstream_with_changed_inputs(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    # Step 0: the root — model version changed.
    _add_llm_step(db, blobs, a.id, order_idx=0, model="gpt-4o", resp_body="A0")
    _add_llm_step(db, blobs, b.id, order_idx=0, model="gpt-4o-2026-05", resp_body="B0")
    # Step 1: same model both sides, but the request differs (upstream output
    # fed in), so this is a propagated divergence, not a new root.
    _add_llm_step(
        db, blobs, a.id, order_idx=1, req_body={"model": "gpt-4o", "in": "A0"}, resp_body="A1"
    )
    _add_llm_step(
        db, blobs, b.id, order_idx=1, req_body={"model": "gpt-4o", "in": "B0"}, resp_body="B1"
    )
    result = explain_sessions(db, blobs, a.id, b.id)
    assert result.root is not None
    assert result.root.cause == DivergenceCause.MODEL_VERSION
    assert [n.order_idx for n in result.propagated] == [1]
    assert result.propagated[0].request_changed is True
    assert not result.independent
    assert result.confidence == 0.95  # 0.90 base + 0.05 clean-chain bonus, capped


@pytest.mark.unit
def test_independent_divergence_lowers_confidence(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    # Step 0: root — model change.
    _add_llm_step(db, blobs, a.id, order_idx=0, model="gpt-4o", resp_body="A0")
    _add_llm_step(db, blobs, b.id, order_idx=0, model="gpt-4o-2026-05", resp_body="B0")
    # Step 1: IDENTICAL request both sides, different response. Not explained
    # by the root — an independent divergence (second root or non-determinism).
    same_req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "same"}]}
    _add_llm_step(db, blobs, a.id, order_idx=1, req_body=same_req, resp_body="A1")
    _add_llm_step(db, blobs, b.id, order_idx=1, req_body=same_req, resp_body="B1")
    result = explain_sessions(db, blobs, a.id, b.id)
    assert result.root is not None
    assert [n.order_idx for n in result.independent] == [1]
    assert result.independent[0].request_changed is False
    assert not result.propagated
    assert result.confidence == round(0.90 * 0.70, 2)  # 0.63


@pytest.mark.unit
def test_extra_downstream_step_is_propagated_structural(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    # Step 0: root — model change.
    _add_llm_step(db, blobs, a.id, order_idx=0, model="gpt-4o", resp_body="A0")
    _add_llm_step(db, blobs, b.id, order_idx=0, model="gpt-4o-2026-05", resp_body="B0")
    # Step 1: exists only in session B (the new model made an extra call).
    _add_llm_step(db, blobs, b.id, order_idx=1, resp_body="B1")
    result = explain_sessions(db, blobs, a.id, b.id)
    assert [n.order_idx for n in result.propagated] == [1]
    assert "only in session B" in result.propagated[0].detail
    assert result.confidence == 0.95


@pytest.mark.unit
def test_structural_root_when_first_divergence_is_extra_step(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    _add_llm_step(db, blobs, a.id, order_idx=0, req_body=body, resp_body="r")
    _add_llm_step(db, blobs, b.id, order_idx=0, req_body=body, resp_body="r")
    _add_llm_step(db, blobs, b.id, order_idx=1, resp_body="extra")
    result = explain_sessions(db, blobs, a.id, b.id)
    assert result.root is not None
    assert result.root.cause == DivergenceCause.STEP_COUNT
    assert result.root.order_idx == 1
    assert result.confidence == 0.70  # base for structural, no further divergence


@pytest.mark.unit
def test_summary_renders_root_and_confidence(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path)
    a, b = _two_sessions(db)
    _add_llm_step(db, blobs, a.id, order_idx=0, model="gpt-4o", resp_body="A")
    _add_llm_step(db, blobs, b.id, order_idx=0, model="gpt-4o-2026-05", resp_body="B")
    summary = explain_sessions(db, blobs, a.id, b.id).summary()
    assert "Root cause: step 0" in summary
    assert "model_version_changed" in summary
    assert "Confidence:" in summary
    assert "heuristic" in summary
