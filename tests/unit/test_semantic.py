"""Unit tests for semantic mutation.

Zero live LLM calls: a deterministic stub stands in for the model. Covers
provider-shape text extraction/replacement, opt-in gating in
generate_mutations, end-to-end materialisation of a drifted cassette, and the
no-key error path of the real GeminiFlashMutator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewind.engines.mutate import MutationKind, generate_mutations
from rewind.engines.semantic import (
    GeminiFlashMutator,
    SemanticMutator,
    extract_assistant_text,
    set_assistant_text,
)
from rewind.exceptions import SemanticMutatorError
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step


class _StubMutator:
    """Deterministic, offline stand-in for the real model."""

    def rewrite(self, text: str) -> str:
        return f"WRONG[{text}]"


def test_stub_satisfies_protocol() -> None:
    assert isinstance(_StubMutator(), SemanticMutator)


# --- provider shape extraction / replacement --------------------------------

_OPENAI = {"choices": [{"message": {"role": "assistant", "content": "the sky is blue"}}]}
_ANTHROPIC = {"content": [{"type": "text", "text": "the sky is blue"}], "role": "assistant"}
_GEMINI = {"candidates": [{"content": {"parts": [{"text": "the sky is blue"}], "role": "model"}}]}


@pytest.mark.unit
@pytest.mark.parametrize("body", [_OPENAI, _ANTHROPIC, _GEMINI])
def test_extract_assistant_text_all_providers(body: dict[str, object]) -> None:
    assert extract_assistant_text(body) == "the sky is blue"


@pytest.mark.unit
@pytest.mark.parametrize("body", [_OPENAI, _ANTHROPIC, _GEMINI])
def test_set_assistant_text_roundtrip(body: dict[str, object]) -> None:
    updated = set_assistant_text(body, "the sky is green")
    assert extract_assistant_text(updated) == "the sky is green"
    # original untouched (deep copy)
    assert extract_assistant_text(body) == "the sky is blue"


@pytest.mark.unit
def test_extract_returns_none_for_unknown_shape() -> None:
    assert extract_assistant_text({"foo": "bar"}) is None
    assert extract_assistant_text({"choices": []}) is None


# --- generation gating ------------------------------------------------------


def _session_with_provider_resp(
    tmp_path: Path, provider_body: dict[str, object]
) -> tuple[RewindDB, BlobStore, str]:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    session = Session(agent_name="base", command='["python","agent.py"]')
    db.save_session(session)
    req_blob = blobs.write(json.dumps({"method": "POST", "body": {}}).encode())
    wrapped = {"status_code": 200, "headers": {}, "body": json.dumps(provider_body)}
    resp_blob = blobs.write(json.dumps(wrapped).encode())
    db.save_step(
        Step(
            session_id=session.id,
            order_idx=0,
            type="llm_call",
            provider="anthropic",
            model="claude-haiku-4-5",
            match_key="k-0",
            req_blob=req_blob,
            resp_blob=resp_blob,
        )
    )
    return db, blobs, session.id


@pytest.mark.unit
def test_semantic_not_emitted_without_mutator(tmp_path: Path) -> None:
    db, blobs, sid = _session_with_provider_resp(tmp_path, _ANTHROPIC)
    kinds = {m.kind for m in generate_mutations(db, sid)}
    assert MutationKind.SEMANTIC_DRIFT not in kinds


@pytest.mark.unit
def test_semantic_emitted_with_mutator(tmp_path: Path) -> None:
    db, blobs, sid = _session_with_provider_resp(tmp_path, _ANTHROPIC)
    muts = list(generate_mutations(db, sid, blobs=blobs, semantic_mutator=_StubMutator()))
    semantic = [m for m in muts if m.kind == MutationKind.SEMANTIC_DRIFT]
    assert len(semantic) == 1
    assert semantic[0].target_order_idx == 0


@pytest.mark.unit
def test_semantic_skipped_for_non_provider_body(tmp_path: Path) -> None:
    # A response whose body is a plain string, not a provider JSON object.
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    session = Session(agent_name="base")
    db.save_session(session)
    resp_blob = blobs.write(
        json.dumps({"status_code": 200, "headers": {}, "body": "just text"}).encode()
    )
    db.save_step(
        Step(
            session_id=session.id,
            order_idx=0,
            type="llm_call",
            match_key="k-0",
            resp_blob=resp_blob,
        )
    )
    muts = list(generate_mutations(db, session.id, blobs=blobs, semantic_mutator=_StubMutator()))
    assert not [m for m in muts if m.kind == MutationKind.SEMANTIC_DRIFT]


@pytest.mark.unit
def test_generate_requires_blobs_when_semantic(tmp_path: Path) -> None:
    db, blobs, sid = _session_with_provider_resp(tmp_path, _ANTHROPIC)
    with pytest.raises(ValueError, match="blobs is required"):
        list(generate_mutations(db, sid, semantic_mutator=_StubMutator()))


# --- materialisation --------------------------------------------------------


@pytest.mark.unit
def test_semantic_mutation_drifts_assistant_text(tmp_path: Path) -> None:
    db, blobs, sid = _session_with_provider_resp(tmp_path, _ANTHROPIC)
    m = next(
        x
        for x in generate_mutations(db, sid, blobs=blobs, semantic_mutator=_StubMutator())
        if x.kind == MutationKind.SEMANTIC_DRIFT
    )
    new_id = m.apply(db, blobs, sid)
    step = db.get_steps(new_id)[0]
    assert step.resp_blob is not None
    payload = json.loads(blobs.read(step.resp_blob))
    body = json.loads(payload["body"])
    assert extract_assistant_text(body) == "WRONG[the sky is blue]"
    # original session untouched
    orig_step = db.get_steps(sid)[0]
    orig_body = json.loads(json.loads(blobs.read(orig_step.resp_blob or ""))["body"])
    assert extract_assistant_text(orig_body) == "the sky is blue"


# --- real mutator, no network -----------------------------------------------


@pytest.mark.unit
def test_gemini_mutator_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("REWIND_API_KEY", raising=False)
    with pytest.raises(SemanticMutatorError, match="no API key"):
        GeminiFlashMutator()


@pytest.mark.unit
def test_gemini_mutator_never_puts_key_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A rewrite failure must never echo the key. Simulate the worst case — an
    # httpx error whose own message embeds the secret — and assert the raised
    # SemanticMutatorError carries neither the secret nor a chained cause that
    # would. No network: httpx.post is patched to raise before any connection.
    import httpx

    secret = "AIzaTESTSECRETKEY1234567890"
    monkeypatch.setenv("GEMINI_API_KEY", secret)

    def boom(*args: object, **kwargs: object) -> object:
        raise httpx.ConnectError(f"connection to ...?key={secret} failed")

    monkeypatch.setattr(httpx, "post", boom)
    mut = GeminiFlashMutator()
    with pytest.raises(SemanticMutatorError) as ei:
        mut.rewrite("hello")
    assert secret not in str(ei.value)
    assert ei.value.__cause__ is None  # `raise ... from None` — no leaking chain
