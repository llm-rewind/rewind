"""Bisection engine — finds the first step where two sessions diverged
and classifies *why* they diverged.

Cause inference is the differentiator. VCR-style cassette tools and
existing LLM observability platforms can show that two responses differ.
None of them tell you whether the difference was a model version bump,
a tweaked system prompt, a tool returning different data, or
unaccounted-for non-determinism in the model itself. Knowing the cause
is the difference between a useful debugging tool and another log diff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from rewind.engines.diff import DiffStatus, SessionDiff, StepDiff, diff_sessions
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Step


class DivergenceCause(StrEnum):
    """Categorisation of why two recorded steps differ.

    Ordering matters: earlier entries dominate when multiple causes
    apply (e.g. if both the model and the prompt changed, the model
    change is the more likely root cause to investigate first).
    """

    NONE = "none"
    STEP_COUNT = "step_count_differs"
    STEP_TYPE = "step_type_changed"
    MODEL_VERSION = "model_version_changed"
    TOOL_LIST_CHANGED = "tool_list_changed"
    PROMPT_DRIFT = "prompt_drift"
    TOOL_OUTPUT_DRIFT = "upstream_tool_output_drifted"
    LLM_NONDETERMINISM = "llm_nondeterminism"
    UNKNOWN = "unknown"


@dataclass
class BisectResult:
    session_id_a: str
    session_id_b: str
    diverged_at: int | None
    step_a: Step | None
    step_b: Step | None
    cause: DivergenceCause
    cause_detail: str
    total_steps_a: int
    total_steps_b: int

    @property
    def is_identical(self) -> bool:
        return self.diverged_at is None

    # Kept for backward compatibility with the original API.
    @property
    def model_changed(self) -> bool:
        return self.cause == DivergenceCause.MODEL_VERSION

    def summary(self) -> str:
        if self.is_identical:
            return f"Sessions {self.session_id_a[:8]} and {self.session_id_b[:8]} are identical."
        model_a = self.step_a.model if self.step_a else "?"
        model_b = self.step_b.model if self.step_b else "?"
        lines = [
            f"First divergence at step {self.diverged_at}",
            f"  Session A: {self.session_id_a[:8]}  model={model_a}",
            f"  Session B: {self.session_id_b[:8]}  model={model_b}",
            f"  Cause:    {self.cause.value}",
            f"  Detail:   {self.cause_detail}",
        ]
        return "\n".join(lines)


def bisect_sessions(
    db: RewindDB,
    blobs: BlobStore,
    session_id_a: str,
    session_id_b: str,
) -> BisectResult:
    """Find the first diverging step and classify the cause."""
    diff: SessionDiff = diff_sessions(db, blobs, session_id_a, session_id_b)

    steps_a = db.get_steps(session_id_a)
    steps_b = db.get_steps(session_id_b)

    first = diff.first_divergence
    if first is None:
        return BisectResult(
            session_id_a=session_id_a,
            session_id_b=session_id_b,
            diverged_at=None,
            step_a=None,
            step_b=None,
            cause=DivergenceCause.NONE,
            cause_detail="all steps match byte-for-byte",
            total_steps_a=len(steps_a),
            total_steps_b=len(steps_b),
        )

    cause, detail = _infer_cause(blobs, first, diff)

    return BisectResult(
        session_id_a=session_id_a,
        session_id_b=session_id_b,
        diverged_at=first.order_idx,
        step_a=first.step_a,
        step_b=first.step_b,
        cause=cause,
        cause_detail=detail,
        total_steps_a=len(steps_a),
        total_steps_b=len(steps_b),
    )


def _infer_cause(
    blobs: BlobStore, first: StepDiff, diff: SessionDiff
) -> tuple[DivergenceCause, str]:
    """Determine the most likely root cause of the divergence.

    The order of checks mirrors how a senior engineer would triage:
    structural changes first (step count, step type), then explicit
    config changes (model, tools, prompt), then upstream input drift
    (a prior tool returned different data), then suspected
    non-determinism as a last resort because it is the hardest to
    confirm without re-runs.
    """
    if first.status == DiffStatus.ADDED:
        return (
            DivergenceCause.STEP_COUNT,
            f"session B has an extra step at index {first.order_idx} that session A does not",
        )
    if first.status == DiffStatus.REMOVED:
        return (
            DivergenceCause.STEP_COUNT,
            f"session A has an extra step at index {first.order_idx} that session B does not",
        )

    step_a, step_b = first.step_a, first.step_b
    if step_a is None or step_b is None:
        return DivergenceCause.UNKNOWN, "missing step data"

    if step_a.type != step_b.type:
        return (
            DivergenceCause.STEP_TYPE,
            f"step type changed: {step_a.type!r} -> {step_b.type!r}",
        )

    if step_a.model != step_b.model:
        return (
            DivergenceCause.MODEL_VERSION,
            f"model changed: {step_a.model!r} -> {step_b.model!r}. "
            "Model upgrades are the highest-likelihood cause of behaviour shifts.",
        )

    # Load the request blobs to compare prompt and tool list.
    req_a = _load_blob(blobs, step_a.req_blob)
    req_b = _load_blob(blobs, step_b.req_blob)

    if req_a is not None and req_b is not None:
        body_a_raw = req_a.get("body") or {}
        body_b_raw = req_b.get("body") or {}
        body_a: dict[str, object] = body_a_raw if isinstance(body_a_raw, dict) else {}
        body_b: dict[str, object] = body_b_raw if isinstance(body_b_raw, dict) else {}

        tools_a = _normalize_tools(body_a.get("tools"))
        tools_b = _normalize_tools(body_b.get("tools"))
        if tools_a != tools_b:
            added = sorted(set(tools_b) - set(tools_a))
            removed = sorted(set(tools_a) - set(tools_b))
            parts = []
            if added:
                parts.append(f"added: {added}")
            if removed:
                parts.append(f"removed: {removed}")
            return (
                DivergenceCause.TOOL_LIST_CHANGED,
                "tool list changed; " + "; ".join(parts) if parts else "tool list changed",
            )

        # Has the prompt drifted? Compare messages + system + temperature.
        prompt_a = _prompt_signature(body_a)
        prompt_b = _prompt_signature(body_b)
        if prompt_a != prompt_b:
            return (
                DivergenceCause.PROMPT_DRIFT,
                "request body changed between runs (messages, system, or sampling params). "
                "Re-check whether the calling code rebuilt the prompt or pulled a new template.",
            )

        # Same model, same tools, same prompt, different response.
        # That leaves two possibilities: an upstream step (tool call or
        # parent LLM step) produced different inputs to this step, or the
        # model itself responded non-deterministically.
        prev_a = _previous_step(diff, first.order_idx, side="a")
        prev_b = _previous_step(diff, first.order_idx, side="b")
        if (
            prev_a is not None
            and prev_b is not None
            and prev_a.type == "tool_call"
            and prev_a.resp_blob != prev_b.resp_blob
        ):
            return (
                DivergenceCause.TOOL_OUTPUT_DRIFT,
                f"previous step ({prev_a.order_idx}, tool_call) returned different output. "
                "Likely root cause is upstream; bisect that step first.",
            )

        return (
            DivergenceCause.LLM_NONDETERMINISM,
            "identical request, different response. Either the model is "
            "non-deterministic at this temperature, or a hidden volatile field "
            "leaked into the prompt (timestamps, UUIDs, random ids).",
        )

    return DivergenceCause.UNKNOWN, "request bodies could not be loaded for comparison"


def _normalize_tools(tools: object) -> list[str]:
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for t in tools:
        if isinstance(t, dict):
            # OpenAI: {"function": {"name": ...}}; Anthropic: {"name": ...}
            fn = t.get("function") if isinstance(t.get("function"), dict) else None
            if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                names.append(fn["name"])
            elif isinstance(t.get("name"), str):
                names.append(t["name"])
    return sorted(names)


def _prompt_signature(body: dict[str, object]) -> str:
    """Stable representation of the parts of a request body that affect the
    model's response, excluding fields already known to be volatile."""
    sig = {
        "messages": body.get("messages"),
        "system": body.get("system"),
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "max_tokens": body.get("max_tokens"),
    }
    return json.dumps(sig, sort_keys=True, default=str)


def _previous_step(diff: SessionDiff, current_idx: int, *, side: str) -> Step | None:
    """Return the step immediately before `current_idx` on the requested side."""
    for d in diff.steps:
        if d.order_idx == current_idx - 1:
            return d.step_a if side == "a" else d.step_b
    return None


def _load_blob(blobs: BlobStore, blob_hash: str | None) -> dict[str, object] | None:
    if blob_hash is None:
        return None
    try:
        return json.loads(blobs.read(blob_hash))  # type: ignore[no-any-return]
    except Exception:
        return None
