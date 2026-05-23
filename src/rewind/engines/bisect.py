"""Bisection engine — finds exact step where run-A diverged from run-B."""

from __future__ import annotations

from dataclasses import dataclass

from rewind.engines.diff import SessionDiff, diff_sessions
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Step


@dataclass
class BisectResult:
    session_id_a: str
    session_id_b: str
    diverged_at: int | None  # order_idx of first divergence, None if identical
    step_a: Step | None  # step from session A at divergence point
    step_b: Step | None  # step from session B at divergence point
    model_changed: bool  # True if model differs between the two steps
    total_steps_a: int
    total_steps_b: int

    @property
    def is_identical(self) -> bool:
        return self.diverged_at is None

    def summary(self) -> str:
        if self.is_identical:
            return f"Sessions {self.session_id_a[:8]} and {self.session_id_b[:8]} are identical."
        model_a = self.step_a.model if self.step_a else "?"
        model_b = self.step_b.model if self.step_b else "?"
        lines = [
            f"First divergence at step {self.diverged_at}",
            f"  Session A: {self.session_id_a[:8]} — model={model_a}",
            f"  Session B: {self.session_id_b[:8]} — model={model_b}",
        ]
        if self.model_changed:
            lines.append("  ⚠ Model changed between sessions — likely cause of divergence.")
        return "\n".join(lines)


def bisect_sessions(
    db: RewindDB,
    blobs: BlobStore,
    session_id_a: str,
    session_id_b: str,
) -> BisectResult:
    """Find the first step index where sessions A and B diverge."""
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
            model_changed=False,
            total_steps_a=len(steps_a),
            total_steps_b=len(steps_b),
        )

    model_changed = (
        first.step_a is not None
        and first.step_b is not None
        and first.step_a.model != first.step_b.model
    )

    return BisectResult(
        session_id_a=session_id_a,
        session_id_b=session_id_b,
        diverged_at=first.order_idx,
        step_a=first.step_a,
        step_b=first.step_b,
        model_changed=model_changed,
        total_steps_a=len(steps_a),
        total_steps_b=len(steps_b),
    )
