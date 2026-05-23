"""Request normalization for match_key computation.

Implements ADR-004: strips volatile fields (tool_call_ids, headers) before hashing
so cassettes remain stable across retries, SDK version changes, and environment differences.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from rewind.constants import STRIP_HEADER_PREFIXES, STRIP_HEADERS


def normalize_request(method: str, path: str, body: bytes) -> str:
    """Compute match_key from method, path, and body. Returns SHA-256 hex digest.

    Headers excluded intentionally — contain auth tokens and SDK version noise.
    """
    canonical: dict[str, Any] = {
        "method": method.upper(),
        "path": path,
    }
    if body:
        try:
            body_json = json.loads(body)
            canonical["body"] = _normalize_body(body_json)
        except (json.JSONDecodeError, ValueError):
            canonical["body_raw"] = body.hex()

    canonical_str = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical_str.encode()).hexdigest()


def strip_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove sensitive/volatile headers before blob storage."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in STRIP_HEADERS
        and not any(k.lower().startswith(p) for p in STRIP_HEADER_PREFIXES)
    }


def _normalize_body(body: dict[str, Any]) -> dict[str, Any]:
    body = dict(body)

    if "messages" in body and isinstance(body["messages"], list):
        body["messages"] = [_normalize_message(m) for m in body["messages"]]

    if "tools" in body and isinstance(body["tools"], list):
        body["tools"] = sorted(
            body["tools"],
            key=lambda t: t.get("name", "") if isinstance(t, dict) else "",
        )

    return body


def _normalize_message(msg: Any) -> Any:
    if not isinstance(msg, dict):
        return msg
    msg = dict(msg)
    msg.pop("tool_call_id", None)
    if "tool_calls" in msg and isinstance(msg["tool_calls"], list):
        msg["tool_calls"] = [
            {k: v for k, v in tc.items() if k != "id"} if isinstance(tc, dict) else tc
            for tc in msg["tool_calls"]
        ]
    return msg
