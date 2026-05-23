"""SSE (Server-Sent Events) chunk parsing and reconstruction for streaming LLM responses."""

from __future__ import annotations

from typing import Any


def is_sse_response(headers: dict[str, str]) -> bool:
    """True if Content-Type indicates Server-Sent Events."""
    return "text/event-stream" in headers.get("content-type", "").lower()


def parse_sse_chunks(raw: bytes) -> list[dict[str, Any]]:
    """Split raw SSE response body into individual event chunks (one per blank-line boundary)."""
    text = raw.decode("utf-8", errors="replace")
    chunks: list[dict[str, Any]] = []
    current: list[str] = []

    for line in text.splitlines(keepends=True):
        current.append(line)
        if line.rstrip("\r\n") == "" and current:
            chunks.append({"data": "".join(current), "delay_ms": 0})
            current = []

    if current:
        chunks.append({"data": "".join(current), "delay_ms": 0})

    return chunks


def reconstruct_sse_body(chunks: list[dict[str, Any]]) -> bytes:
    """Reconstruct raw SSE bytes from stored chunk list."""
    return "".join(c["data"] for c in chunks).encode("utf-8")


def has_done_sentinel(chunks: list[dict[str, Any]]) -> bool:
    """True if any chunk contains the SSE stream terminator."""
    return any("data: [DONE]" in c.get("data", "") for c in chunks)
