"""Causal explanation engine — powers ``rewind explain``.

``bisect`` answers "where did two runs first diverge, and why". ``explain``
answers the follow-up question a debugger actually has next: given that
first divergence, *what did it cause downstream*, and how confident are we
that it is the **root** rather than a symptom of something earlier?

The engine reuses bisect's cause inference for the originating divergence,
then walks the rest of the trace to separate two kinds of later divergence:

* **propagated** — the step's request inputs changed (``req_blob`` differs),
  because the divergent upstream output flowed into it. These corroborate
  the root: the story hangs together as a single chain.
* **independent** — identical request, different response. That is either a
  second, unrelated root cause or model non-determinism, and it muddies the
  claim that the first divergence explains everything.

Output is a ranked causal story with an explicit, *heuristic* confidence
score (never reported as certainty — it is an inference, not a measurement).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rewind.engines.bisect import DivergenceCause, _infer_cause
from rewind.engines.diff import DiffStatus, SessionDiff, StepDiff, diff_sessions
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Step

# --- Confidence model (heuristic) -------------------------------------------
# Per-cause base confidence that the first divergence is the true root cause.
# Explicit config changes (model, prompt, tools) are highly diagnostic;
# suspected non-determinism is the least so because it cannot be confirmed
# without re-runs. These weights live here, not in constants.py, because they
# are keyed by DivergenceCause (constants.py importing engines would cycle).
_BASE_CONFIDENCE: dict[DivergenceCause, float] = {
    DivergenceCause.NONE: 1.0,
    DivergenceCause.MODEL_VERSION: 0.90,
    DivergenceCause.PROMPT_DRIFT: 0.85,
    DivergenceCause.TOOL_LIST_CHANGED: 0.80,
    DivergenceCause.TOOL_OUTPUT_DRIFT: 0.75,
    DivergenceCause.STEP_TYPE: 0.70,
    DivergenceCause.STEP_COUNT: 0.70,
    DivergenceCause.LLM_NONDETERMINISM: 0.40,
    DivergenceCause.UNKNOWN: 0.30,
}
# A clean propagation chain (no unexplained divergences) corroborates the root.
_PROPAGATION_BONUS = 0.05
# Competing independent divergences mean the root may not be the whole story.
_INDEPENDENT_PENALTY = 0.70
# Never claim certainty for an inferred cause.
_MAX_CONFIDENCE = 0.95


@dataclass
class RootCause:
    """The originating divergence and its inferred cause."""

    order_idx: int
    step_a: Step | None
    step_b: Step | None
    cause: DivergenceCause
    cause_detail: str


@dataclass
class DivergenceNode:
    """A divergence downstream of the root, classified by how it relates to it."""

    order_idx: int
    step_a: Step | None
    step_b: Step | None
    request_changed: bool
    detail: str


@dataclass
class ExplainResult:
    session_id_a: str
    session_id_b: str
    root: RootCause | None
    propagated: list[DivergenceNode] = field(default_factory=list)
    independent: list[DivergenceNode] = field(default_factory=list)
    total_steps_a: int = 0
    total_steps_b: int = 0
    confidence: float = 1.0

    @property
    def is_identical(self) -> bool:
        return self.root is None

    def _root_line(self) -> str:
        assert self.root is not None
        step = self.root.step_b or self.root.step_a
        stype = step.type if step else "?"
        provider = step.provider if (step and step.provider) else "?"
        return f"Root cause: step {self.root.order_idx} ({stype}, {provider})"

    def summary(self) -> str:
        if self.is_identical:
            return (
                f"Sessions {self.session_id_a[:8]} and {self.session_id_b[:8]} are "
                "identical — nothing to explain."
            )
        assert self.root is not None
        lines = [
            self._root_line(),
            f"  {self.root.cause.value}: {self.root.cause_detail}",
        ]
        if self.propagated:
            lines.append("Propagated to:")
            for node in self.propagated:
                step = node.step_b or node.step_a
                stype = step.type if step else "?"
                lines.append(f"  step {node.order_idx} {stype} ({node.detail})")
        if self.independent:
            lines.append("Independent divergences (not explained by the root):")
            for node in self.independent:
                step = node.step_b or node.step_a
                stype = step.type if step else "?"
                lines.append(f"  step {node.order_idx} {stype} ({node.detail})")
        lines.append(f"Confidence: {self.confidence:.2f} (heuristic estimate)")
        return "\n".join(lines)


def explain_sessions(
    db: RewindDB,
    blobs: BlobStore,
    session_id_a: str,
    session_id_b: str,
) -> ExplainResult:
    """Build a causal story: root divergence, what it propagated to, confidence.

    Reuses ``diff_sessions`` for the step-by-step comparison and bisect's
    ``_infer_cause`` for classifying the originating divergence, then layers
    propagation analysis on top.
    """
    diff: SessionDiff = diff_sessions(db, blobs, session_id_a, session_id_b)
    steps_a = db.get_steps(session_id_a)
    steps_b = db.get_steps(session_id_b)

    divergences = [s for s in diff.steps if s.status != DiffStatus.MATCH]
    if not divergences:
        return ExplainResult(
            session_id_a=session_id_a,
            session_id_b=session_id_b,
            root=None,
            total_steps_a=len(steps_a),
            total_steps_b=len(steps_b),
            confidence=1.0,
        )

    first = divergences[0]
    cause, detail = _infer_cause(blobs, first, diff)
    root = RootCause(
        order_idx=first.order_idx,
        step_a=first.step_a,
        step_b=first.step_b,
        cause=cause,
        cause_detail=detail,
    )

    propagated: list[DivergenceNode] = []
    independent: list[DivergenceNode] = []
    for d in divergences[1:]:
        changed = _request_changed(d)
        node = DivergenceNode(
            order_idx=d.order_idx,
            step_a=d.step_a,
            step_b=d.step_b,
            request_changed=changed,
            detail=_describe(d, changed),
        )
        if changed:
            propagated.append(node)
        else:
            independent.append(node)

    confidence = _confidence(cause, n_propagated=len(propagated), n_independent=len(independent))

    return ExplainResult(
        session_id_a=session_id_a,
        session_id_b=session_id_b,
        root=root,
        propagated=propagated,
        independent=independent,
        total_steps_a=len(steps_a),
        total_steps_b=len(steps_b),
        confidence=confidence,
    )


def _request_changed(d: StepDiff) -> bool:
    """True if this step's request inputs differ between the two runs.

    A structural difference (step present in only one run) counts as a
    changed input: the run shape itself shifted, which only happens because
    something upstream did. A content divergence with an unchanged request
    blob is *not* an input change — that is the independent / non-determinism
    case.
    """
    if d.step_a is None or d.step_b is None:
        return True
    return d.step_a.req_blob != d.step_b.req_blob


def _describe(d: StepDiff, request_changed: bool) -> str:
    if d.status == DiffStatus.ADDED:
        return "step exists only in session B"
    if d.status == DiffStatus.REMOVED:
        return "step exists only in session A"
    if request_changed:
        return "different inputs — upstream output flowed in"
    return "identical request, different response"


def _confidence(cause: DivergenceCause, *, n_propagated: int, n_independent: int) -> float:
    """Heuristic confidence that the first divergence is the root cause.

    Not a measurement. A clean propagation chain nudges confidence up; any
    unexplained independent divergence pulls it down, because the root no
    longer accounts for everything that changed.
    """
    base = _BASE_CONFIDENCE.get(cause, _BASE_CONFIDENCE[DivergenceCause.UNKNOWN])
    if n_independent > 0:
        base *= _INDEPENDENT_PENALTY
    elif n_propagated > 0:
        base = min(base + _PROPAGATION_BONUS, _MAX_CONFIDENCE)
    return round(min(base, _MAX_CONFIDENCE), 2)
