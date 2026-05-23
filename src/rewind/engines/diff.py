"""Step-by-step session diff — compares two sessions and finds where they diverge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Step


class DiffStatus(StrEnum):
    MATCH = "match"
    DIVERGED = "diverged"   # same order_idx, different resp_blob content
    ADDED = "added"         # exists in B but not A (session B longer)
    REMOVED = "removed"     # exists in A but not B (session A longer)


@dataclass
class StepDiff:
    order_idx: int
    status: DiffStatus
    step_a: Step | None
    step_b: Step | None
    # populated only when status == DIVERGED
    resp_a: dict[str, Any] | None = None
    resp_b: dict[str, Any] | None = None


@dataclass
class SessionDiff:
    session_id_a: str
    session_id_b: str
    steps: list[StepDiff]

    @property
    def first_divergence(self) -> StepDiff | None:
        return next(
            (s for s in self.steps if s.status != DiffStatus.MATCH), None
        )

    @property
    def is_identical(self) -> bool:
        return all(s.status == DiffStatus.MATCH for s in self.steps)


def diff_sessions(
    db: RewindDB,
    blobs: BlobStore,
    session_id_a: str,
    session_id_b: str,
) -> SessionDiff:
    """Compare two sessions step by step. Returns full diff with divergence info."""
    steps_a = db.get_steps(session_id_a)
    steps_b = db.get_steps(session_id_b)

    diffs: list[StepDiff] = []
    max_len = max(len(steps_a), len(steps_b))

    for i in range(max_len):
        a = steps_a[i] if i < len(steps_a) else None
        b = steps_b[i] if i < len(steps_b) else None

        if a is None:
            diffs.append(StepDiff(order_idx=i, status=DiffStatus.ADDED, step_a=None, step_b=b))
            continue
        if b is None:
            diffs.append(StepDiff(order_idx=i, status=DiffStatus.REMOVED, step_a=a, step_b=None))
            continue

        if a.resp_blob == b.resp_blob:
            diffs.append(StepDiff(order_idx=i, status=DiffStatus.MATCH, step_a=a, step_b=b))
        else:
            resp_a = _load_resp(blobs, a.resp_blob)
            resp_b = _load_resp(blobs, b.resp_blob)
            diffs.append(
                StepDiff(
                    order_idx=i,
                    status=DiffStatus.DIVERGED,
                    step_a=a,
                    step_b=b,
                    resp_a=resp_a,
                    resp_b=resp_b,
                )
            )

    return SessionDiff(session_id_a=session_id_a, session_id_b=session_id_b, steps=diffs)


def _load_resp(blobs: BlobStore, blob_hash: str | None) -> dict[str, Any] | None:
    if blob_hash is None:
        return None
    try:
        return json.loads(blobs.read(blob_hash))  # type: ignore[no-any-return]
    except Exception:
        return None
