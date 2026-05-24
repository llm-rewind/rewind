"""Unit tests for the mutation engine.

Each mutation kind is tested for: it generates without error, it
materialises a new session id, the new session has the expected step
count or response payload shape, and the original session is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewind.engines.mutate import (
    MutationKind,
    generate_mutations,
)
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step


def _populate(tmp_path: Path) -> tuple[RewindDB, BlobStore, str]:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    session = Session(agent_name="base", command='["python","agent.py"]')
    db.save_session(session)
    for i in range(3):
        req_blob = blobs.write(json.dumps({"method": "POST", "body": {"i": i}}).encode())
        resp_blob = blobs.write(
            json.dumps({"status_code": 200, "headers": {}, "body": f"response-{i}"}).encode()
        )
        db.save_step(
            Step(
                session_id=session.id,
                order_idx=i,
                type="llm_call",
                provider="openai",
                model="gpt-4o",
                match_key=f"k-{i}",
                req_blob=req_blob,
                resp_blob=resp_blob,
            )
        )
    return db, blobs, session.id


@pytest.mark.unit
def test_generate_mutations_emits_five_per_step(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    muts = list(generate_mutations(db, sid))
    # 3 steps * 5 mutation kinds = 15
    assert len(muts) == 15
    kinds = {m.kind for m in muts}
    assert kinds == {
        MutationKind.DROP_STEP,
        MutationKind.EMPTY_RESPONSE,
        MutationKind.TRUNCATE_RESPONSE,
        MutationKind.ERROR_RESPONSE,
        MutationKind.PROVIDER_500,
    }


@pytest.mark.unit
def test_drop_step_mutation_removes_target(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    drop_first = next(
        m
        for m in generate_mutations(db, sid)
        if m.kind == MutationKind.DROP_STEP and m.target_order_idx == 1
    )
    new_id = drop_first.apply(db, blobs, sid)
    mutated_steps = db.get_steps(new_id)
    assert len(mutated_steps) == 2
    assert {s.order_idx for s in mutated_steps} == {0, 2}
    # Original session untouched.
    assert len(db.get_steps(sid)) == 3


@pytest.mark.unit
def test_empty_response_mutation_replaces_body(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    m = next(
        x
        for x in generate_mutations(db, sid)
        if x.kind == MutationKind.EMPTY_RESPONSE and x.target_order_idx == 0
    )
    new_id = m.apply(db, blobs, sid)
    step = db.get_steps(new_id)[0]
    assert step.resp_blob is not None
    payload = json.loads(blobs.read(step.resp_blob))
    body = json.loads(payload["body"])
    assert body == {"content": [], "choices": []}


@pytest.mark.unit
def test_truncate_response_mutation_shortens_body(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    m = next(
        x
        for x in generate_mutations(db, sid)
        if x.kind == MutationKind.TRUNCATE_RESPONSE and x.target_order_idx == 0
    )
    new_id = m.apply(db, blobs, sid)
    step = db.get_steps(new_id)[0]
    payload = json.loads(blobs.read(step.resp_blob or ""))
    original_body = "response-0"
    assert payload["body"] != original_body
    assert len(payload["body"]) < len(original_body)


@pytest.mark.unit
def test_error_response_mutation_returns_429(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    m = next(
        x
        for x in generate_mutations(db, sid)
        if x.kind == MutationKind.ERROR_RESPONSE and x.target_order_idx == 2
    )
    new_id = m.apply(db, blobs, sid)
    step = db.get_steps(new_id)[2]
    payload = json.loads(blobs.read(step.resp_blob or ""))
    assert payload["status_code"] == 429


@pytest.mark.unit
def test_provider_500_mutation_returns_500(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    m = next(
        x
        for x in generate_mutations(db, sid)
        if x.kind == MutationKind.PROVIDER_500 and x.target_order_idx == 0
    )
    new_id = m.apply(db, blobs, sid)
    step = db.get_steps(new_id)[0]
    payload = json.loads(blobs.read(step.resp_blob or ""))
    assert payload["status_code"] == 500


@pytest.mark.unit
def test_mutated_session_metadata_records_base_and_kind(tmp_path: Path) -> None:
    db, blobs, sid = _populate(tmp_path)
    m = next(generate_mutations(db, sid))
    new_id = m.apply(db, blobs, sid)
    sess = db.get_session(new_id)
    assert sess is not None
    assert sess.metadata.get("base_session_id") == sid
    assert "rewind_mutation" in sess.metadata


@pytest.mark.unit
def test_generate_mutations_skips_steps_without_resp_blob(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    BlobStore(tmp_path / "blobs")
    session = Session(agent_name="partial")
    db.save_session(session)
    db.save_step(
        Step(session_id=session.id, order_idx=0, type="llm_call", match_key="k", resp_blob=None)
    )
    muts = list(generate_mutations(db, session.id))
    assert muts == []


@pytest.mark.unit
def test_generate_mutations_empty_session(tmp_path: Path) -> None:
    db = RewindDB(":memory:")
    BlobStore(tmp_path / "blobs")
    session = Session(agent_name="empty")
    db.save_session(session)
    assert list(generate_mutations(db, session.id)) == []
