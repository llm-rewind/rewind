"""Cassette export/import — .rw file format for portable session sharing.

.rw files are self-contained JSON: session + steps + all blobs (base64-encoded).
One file provides full replay capability without external DB or blob store.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step

CASSETTE_VERSION = 1

# Auth header keys that must NEVER appear in exported cassettes (security invariant)
_BANNED_EXPORT_STRINGS = ("Authorization", "x-api-key")


def export_cassette(db: RewindDB, blobs: BlobStore, session_id: str) -> dict[str, Any]:
    """Build a self-contained cassette dict from a session in DB+blobs.

    Raises ValueError if auth headers are found in blob content (security invariant).
    """
    session = db.get_session(session_id)
    steps = db.get_steps(session_id)

    blob_hashes: set[str] = set()
    for step in steps:
        if step.req_blob:
            blob_hashes.add(step.req_blob)
        if step.resp_blob:
            blob_hashes.add(step.resp_blob)

    # Embed original (decompressed) bytes as base64 — hash is of original content
    embedded: dict[str, str] = {}
    for h in blob_hashes:
        raw = blobs.read(h)  # SHA-256 verified on read; returns original bytes
        _check_no_auth(raw, h)
        embedded[h] = base64.b64encode(raw).decode("ascii")

    return {
        "version": CASSETTE_VERSION,
        "session": _session_to_dict(session),
        "steps": [_step_to_dict(s) for s in steps],
        "blobs": embedded,
    }


def import_cassette(data: dict[str, Any], db: RewindDB, blobs: BlobStore) -> str:
    """Load a cassette dict into DB+blobs. Returns session_id."""
    for blob_hash, b64_content in data.get("blobs", {}).items():
        raw = base64.b64decode(b64_content)
        written_hash = blobs.write(raw)
        if written_hash != blob_hash:
            raise ValueError(
                f"Cassette blob integrity check failed: "
                f"expected {blob_hash[:8]}..., got {written_hash[:8]}..."
            )

    session = _dict_to_session(data.get("session", {}))
    db.save_session(session)

    for step_dict in data.get("steps", []):
        db.save_step(_dict_to_step(step_dict))

    return session.id


def load_cassette_file(path: Path) -> dict[str, Any]:
    """Read a .rw cassette file from disk."""
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_cassette_file(data: dict[str, Any], path: Path) -> None:
    """Write a cassette dict to a .rw file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _check_no_auth(raw: bytes, blob_hash: str) -> None:
    """Raise if blob content contains auth strings — defense-in-depth security check."""
    text = raw.decode("utf-8", errors="replace")
    for banned in _BANNED_EXPORT_STRINGS:
        if banned in text:
            raise ValueError(
                f"Blob {blob_hash[:8]}... contains '{banned}' — "
                "auth headers must be stripped before recording (see ADR-004)"
            )


def _session_to_dict(session: Session | None) -> dict[str, Any]:
    if session is None:
        return {}
    return {
        "id": session.id,
        "agent_name": session.agent_name,
        "git_hash": session.git_hash,
        "command": session.command,
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "total_cost_usd": session.total_cost_usd,
        "metadata": session.metadata,
    }


def _dict_to_session(d: dict[str, Any]) -> Session:
    return Session(
        id=d["id"],
        agent_name=d.get("agent_name", "unknown"),
        git_hash=d.get("git_hash"),
        command=d.get("command"),
        started_at=(
            datetime.fromisoformat(d["started_at"]) if d.get("started_at") else datetime.now(UTC)
        ),
        ended_at=(datetime.fromisoformat(d["ended_at"]) if d.get("ended_at") else None),
        total_cost_usd=float(d.get("total_cost_usd", 0.0)),
        metadata=d.get("metadata", {}),
    )


def _step_to_dict(step: Step) -> dict[str, Any]:
    return {
        "id": step.id,
        "session_id": step.session_id,
        "parent_id": step.parent_id,
        "order_idx": step.order_idx,
        "type": step.type,
        "provider": step.provider,
        "model": step.model,
        "match_key": step.match_key,
        "req_blob": step.req_blob,
        "resp_blob": step.resp_blob,
        "input_tok": step.input_tok,
        "output_tok": step.output_tok,
        "latency_ms": step.latency_ms,
        "started_at": step.started_at.isoformat(),
        "is_streaming": step.is_streaming,
    }


def _dict_to_step(d: dict[str, Any]) -> Step:
    return Step(
        id=d["id"],
        session_id=d["session_id"],
        parent_id=d.get("parent_id"),
        order_idx=d["order_idx"],
        type=d["type"],
        provider=d.get("provider"),
        model=d.get("model"),
        match_key=d.get("match_key"),
        req_blob=d.get("req_blob"),
        resp_blob=d.get("resp_blob"),
        input_tok=int(d.get("input_tok", 0)),
        output_tok=int(d.get("output_tok", 0)),
        latency_ms=int(d.get("latency_ms", 0)),
        started_at=(
            datetime.fromisoformat(d["started_at"]) if d.get("started_at") else datetime.now(UTC)
        ),
        is_streaming=bool(d.get("is_streaming", False)),
    )
