"""Mutation testing for AI agents.

The novel piece. VCR-style cassettes prove an agent reproduces a single
recorded path. Mutation testing systematically perturbs the recording
and re-runs the agent against each perturbation to surface where the
agent quietly does the wrong thing when the world is slightly off.

This is Stryker for LLM agents. The mutations target the failure modes
that actually bite production AI systems:

- the model returns an empty completion (max_tokens hit, refusal)
- a tool returns malformed or empty output
- the provider returns a 429 or 500 mid-run
- the response gets truncated halfway through
- a step is missing entirely (silently dropped LLM call)

Each mutation runs as its own replay session. If the agent's
observable output is unchanged, the agent is robust to that fault. If
it changes or the process crashes, mutate reports it as a fragility.

The CLI surface is `rewind mutate`. The engine here is pure: it takes
a cassette and yields mutated copies. The CLI handles orchestration.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from rewind.engines.semantic import (
    SemanticMutator,
    extract_assistant_text,
    set_assistant_text,
)
from rewind.exceptions import BlobTamperedError
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step

_LOG = logging.getLogger(__name__)


class MutationKind(StrEnum):
    DROP_STEP = "drop_step"
    EMPTY_RESPONSE = "empty_response"
    TRUNCATE_RESPONSE = "truncate_response"
    ERROR_RESPONSE = "error_response"
    PROVIDER_500 = "provider_500"
    SEMANTIC_DRIFT = "semantic_drift"


@dataclass
class Mutation:
    """One mutation. `apply` materialises the new cassette by writing
    fresh DB rows and blob entries, returning the new session id."""

    kind: MutationKind
    target_order_idx: int  # which step is mutated
    description: str
    _materialiser: object  # callable: (db, blobs, base_session_id) -> str

    def apply(self, db: RewindDB, blobs: BlobStore, base_session_id: str) -> str:
        return self._materialiser(db, blobs, base_session_id)  # type: ignore[operator,no-any-return]


@dataclass
class MutationResult:
    mutation: Mutation
    mutated_session_id: str
    # The CLI fills these in after running the agent against the mutated cassette.
    exit_code: int | None = None
    stdout: str | None = None
    crashed: bool = False
    output_changed: bool = False


def generate_mutations(
    db: RewindDB,
    session_id: str,
    *,
    blobs: BlobStore | None = None,
    semantic_mutator: SemanticMutator | None = None,
) -> Iterator[Mutation]:
    """Walk the recorded session and yield every mutation worth running.

    The five syntactic mutations are always emitted. Semantic-drift mutations
    are emitted only when ``semantic_mutator`` is supplied (it requires a live
    model, so it is opt-in via ``rewind mutate --semantic``). They are emitted
    only for steps whose response actually contains extractable assistant text,
    so every yielded mutation is genuinely applicable.
    """
    if semantic_mutator is not None and blobs is None:
        raise ValueError("blobs is required when semantic_mutator is provided")

    steps = db.get_steps(session_id)
    if not steps:
        return

    for step in steps:
        if step.resp_blob is None:
            continue

        idx = step.order_idx
        yield _drop_step_mutation(idx)
        yield _empty_response_mutation(idx)
        yield _truncate_response_mutation(idx)
        yield _error_response_mutation(idx)
        yield _provider_500_mutation(idx)

        if (
            semantic_mutator is not None
            and blobs is not None
            and _step_has_assistant_text(blobs, step)
        ):
            yield _semantic_drift_mutation(idx, semantic_mutator)


def _step_has_assistant_text(blobs: BlobStore, step: Step) -> bool:
    """True if the step's response blob holds editable assistant text."""
    if step.resp_blob is None:
        return False
    try:
        payload = json.loads(blobs.read(step.resp_blob))
        body = json.loads(payload["body"]) if isinstance(payload.get("body"), str) else None
    except (BlobTamperedError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        return False
    return isinstance(body, dict) and extract_assistant_text(body) is not None


# ---------------------------------------------------------------------------
# Mutation factories
# ---------------------------------------------------------------------------


def _drop_step_mutation(idx: int) -> Mutation:
    def apply(db: RewindDB, blobs: BlobStore, base_id: str) -> str:
        return _materialise(
            db,
            blobs,
            base_id,
            mutate_fn=lambda steps: [s for s in steps if s.order_idx != idx],
            suffix=f"drop-{idx}",
        )

    return Mutation(
        kind=MutationKind.DROP_STEP,
        target_order_idx=idx,
        description=f"step {idx} removed entirely (simulates dropped LLM call)",
        _materialiser=apply,
    )


def _empty_response_mutation(idx: int) -> Mutation:
    def apply(db: RewindDB, blobs: BlobStore, base_id: str) -> str:
        return _materialise(
            db,
            blobs,
            base_id,
            mutate_fn=lambda steps: _rewrite_resp(blobs, steps, idx, _empty_payload),
            suffix=f"empty-{idx}",
        )

    return Mutation(
        kind=MutationKind.EMPTY_RESPONSE,
        target_order_idx=idx,
        description=f"step {idx} response replaced with empty body (refusal/max_tokens)",
        _materialiser=apply,
    )


def _truncate_response_mutation(idx: int) -> Mutation:
    def apply(db: RewindDB, blobs: BlobStore, base_id: str) -> str:
        return _materialise(
            db,
            blobs,
            base_id,
            mutate_fn=lambda steps: _rewrite_resp(blobs, steps, idx, _truncate_payload),
            suffix=f"trunc-{idx}",
        )

    return Mutation(
        kind=MutationKind.TRUNCATE_RESPONSE,
        target_order_idx=idx,
        description=f"step {idx} response truncated to first half (max_tokens cutoff)",
        _materialiser=apply,
    )


def _error_response_mutation(idx: int) -> Mutation:
    def apply(db: RewindDB, blobs: BlobStore, base_id: str) -> str:
        return _materialise(
            db,
            blobs,
            base_id,
            mutate_fn=lambda steps: _rewrite_resp(blobs, steps, idx, _rate_limit_payload),
            suffix=f"429-{idx}",
        )

    return Mutation(
        kind=MutationKind.ERROR_RESPONSE,
        target_order_idx=idx,
        description=f"step {idx} returns 429 rate-limit error",
        _materialiser=apply,
    )


def _provider_500_mutation(idx: int) -> Mutation:
    def apply(db: RewindDB, blobs: BlobStore, base_id: str) -> str:
        return _materialise(
            db,
            blobs,
            base_id,
            mutate_fn=lambda steps: _rewrite_resp(blobs, steps, idx, _server_error_payload),
            suffix=f"500-{idx}",
        )

    return Mutation(
        kind=MutationKind.PROVIDER_500,
        target_order_idx=idx,
        description=f"step {idx} returns 500 server error",
        _materialiser=apply,
    )


def _semantic_drift_mutation(idx: int, mutator: SemanticMutator) -> Mutation:
    def apply(db: RewindDB, blobs: BlobStore, base_id: str) -> str:
        # The mutator (a live model) runs here, at apply time — not during
        # generation — so no network call happens until a mutation is actually
        # materialised for a run.
        return _materialise(
            db,
            blobs,
            base_id,
            mutate_fn=lambda steps: _rewrite_resp(
                blobs, steps, idx, lambda payload: _semantic_payload(payload, mutator)
            ),
            suffix=f"semantic-{idx}",
        )

    return Mutation(
        kind=MutationKind.SEMANTIC_DRIFT,
        target_order_idx=idx,
        description=(
            f"step {idx} assistant text rewritten to a plausible-but-wrong variant "
            "(adversarial semantic drift)"
        ),
        _materialiser=apply,
    )


# ---------------------------------------------------------------------------
# Payload rewrites
# ---------------------------------------------------------------------------


def _semantic_payload(original: dict[str, object], mutator: SemanticMutator) -> dict[str, object]:
    """Rewrite the assistant text inside a wrapped response payload.

    `original` is the stored response blob shape: {"status_code", "headers",
    "body": "<provider json as string>"}. We parse the body, drift its
    assistant text via the mutator, and re-serialise. If the body is not a
    recognised provider shape the payload is returned unchanged.
    """
    body_str = original.get("body")
    if not isinstance(body_str, str):
        return original
    try:
        provider_body = json.loads(body_str)
    except (json.JSONDecodeError, ValueError):
        return original
    if not isinstance(provider_body, dict):
        return original

    text = extract_assistant_text(provider_body)
    if text is None:
        return original

    drifted = mutator.rewrite(text)
    new_provider_body = set_assistant_text(provider_body, drifted)

    payload = copy.deepcopy(original)
    payload["body"] = json.dumps(new_provider_body)
    return payload


def _empty_payload(original: dict[str, object]) -> dict[str, object]:
    payload = copy.deepcopy(original)
    payload["body"] = json.dumps({"content": [], "choices": []})
    return payload


def _truncate_payload(original: dict[str, object]) -> dict[str, object]:
    payload = copy.deepcopy(original)
    body = payload.get("body", "")
    if isinstance(body, str) and len(body) > 4:
        payload["body"] = body[: max(1, len(body) // 2)]
    return payload


def _rate_limit_payload(original: dict[str, object]) -> dict[str, object]:
    return {
        "status_code": 429,
        "headers": {"content-type": "application/json", "retry-after": "5"},
        "body": json.dumps(
            {"error": {"code": 429, "message": "Rate limit exceeded (mutated by rewind mutate)"}}
        ),
    }


def _server_error_payload(original: dict[str, object]) -> dict[str, object]:
    return {
        "status_code": 500,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"error": {"code": 500, "message": "Internal server error (mutated)"}}),
    }


# ---------------------------------------------------------------------------
# Materialisation: clone session, apply mutate_fn, write to DB
# ---------------------------------------------------------------------------


def _materialise(
    db: RewindDB,
    blobs: BlobStore,
    base_session_id: str,
    *,
    mutate_fn: object,
    suffix: str,
) -> str:
    """Copy a base session to a new session id, applying mutate_fn to the
    step list before saving. Returns the new session id."""
    base = db.get_session(base_session_id)
    if base is None:
        raise ValueError(f"session not found: {base_session_id}")
    original_steps = db.get_steps(base_session_id)

    new_id = str(uuid.uuid4())
    new_session = Session(
        id=new_id,
        agent_name=f"{base.agent_name}:mutated:{suffix}",
        git_hash=base.git_hash,
        command=base.command,
        metadata={**base.metadata, "rewind_mutation": suffix, "base_session_id": base_session_id},
    )
    db.save_session(new_session)

    mutated = mutate_fn(original_steps)  # type: ignore[operator]
    for step in mutated:
        # New step id, new session id, but everything else (including blob hashes) preserved.
        cloned = step.model_copy(update={"id": str(uuid.uuid4()), "session_id": new_id})
        db.save_step(cloned)

    return new_id


def _rewrite_resp(
    blobs: BlobStore,
    steps: list[Step],
    target_idx: int,
    rewrite_fn: object,
) -> list[Step]:
    """Return a new step list where the target step's resp_blob is replaced
    by a freshly written blob holding the rewritten payload."""
    out: list[Step] = []
    for step in steps:
        if step.order_idx != target_idx or step.resp_blob is None:
            out.append(step)
            continue
        try:
            original = json.loads(blobs.read(step.resp_blob))
        except (BlobTamperedError, json.JSONDecodeError, ValueError) as e:
            # A blob we cannot read is itself a finding worth surfacing; the
            # caller sees the unmutated step and a logged warning rather
            # than a silent skip that would hide corruption.
            _LOG.warning(
                "mutate: skipping step %d, could not read resp blob (%s): %s",
                step.order_idx,
                type(e).__name__,
                e,
            )
            out.append(step)
            continue
        new_payload = rewrite_fn(original)  # type: ignore[operator]
        new_blob = blobs.write(json.dumps(new_payload, sort_keys=True).encode())
        out.append(step.model_copy(update={"resp_blob": new_blob}))
    return out


def delete_mutated_sessions(db: RewindDB, base_session_id: str) -> int:
    """Delete every mutated session derived from base_session_id.

    `rewind mutate` materialises each mutation as its own session so the
    runs are independently inspectable. Without cleanup they accumulate
    forever in `rewind list`. Call this after a mutation run when the
    individual sessions are no longer interesting.
    Returns the count of sessions removed.
    """
    removed = 0
    for sess in db.list_sessions(limit=10_000):
        if sess.metadata.get("base_session_id") == base_session_id:
            db.delete_session(sess.id)
            removed += 1
    return removed
