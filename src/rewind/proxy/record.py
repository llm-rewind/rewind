"""RecordAddon — mitmproxy addon that intercepts LLM API calls and saves them as cassette steps."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from mitmproxy import http

from rewind.constants import PROVIDER_HOSTS
from rewind.proxy.normalize import normalize_request, strip_headers
from rewind.proxy.streaming import is_sse_response, parse_sse_chunks
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Step


def _parse_token_counts(body: bytes) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from provider response JSON."""
    try:
        usage = json.loads(body).get("usage", {})
        input_tok = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        output_tok = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        return int(input_tok), int(output_tok)
    except (json.JSONDecodeError, ValueError, AttributeError):
        return 0, 0


def _extract_model(body: bytes) -> str | None:
    try:
        val = json.loads(body).get("model")
        return str(val) if val is not None else None
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None


def _is_streaming(body: bytes) -> bool:
    try:
        return bool(json.loads(body).get("stream", False))
    except (json.JSONDecodeError, ValueError):
        return False


class RecordAddon:
    def __init__(self, db: RewindDB, blobs: BlobStore, session_id: str) -> None:
        self._db = db
        self._blobs = blobs
        self._session_id = session_id
        self._order_idx = 0
        self._pending: dict[str, tuple[Step, float]] = {}

    def request(self, flow: http.HTTPFlow) -> None:
        provider = PROVIDER_HOSTS.get(flow.request.host)
        if provider is None:
            return

        req_body = flow.request.content or b""
        match_key = normalize_request(flow.request.method, flow.request.path, req_body)

        req_payload: dict[str, Any] = {
            "method": flow.request.method,
            "path": flow.request.path,
            "headers": strip_headers(dict(flow.request.headers)),
        }
        try:
            req_payload["body"] = json.loads(req_body) if req_body else None
        except (json.JSONDecodeError, ValueError):
            req_payload["body_raw"] = req_body.decode("utf-8", errors="replace")

        req_blob = self._blobs.write(json.dumps(req_payload, sort_keys=True).encode())

        step = Step(
            session_id=self._session_id,
            order_idx=self._order_idx,
            type="llm_call",
            provider=provider,
            model=_extract_model(req_body),
            match_key=match_key,
            req_blob=req_blob,
            started_at=datetime.now(UTC),
            is_streaming=_is_streaming(req_body),
        )
        self._order_idx += 1
        self._pending[flow.id] = (step, time.monotonic())

    def response(self, flow: http.HTTPFlow) -> None:
        if flow.id not in self._pending or flow.response is None:
            return

        step, t0 = self._pending.pop(flow.id)
        step.latency_ms = int((time.monotonic() - t0) * 1000)

        resp_body = flow.response.content or b""
        clean_resp_headers = strip_headers(dict(flow.response.headers))
        resp_payload: dict[str, Any] = {
            "status_code": flow.response.status_code,
            "headers": clean_resp_headers,
            "body": resp_body.decode("utf-8", errors="replace"),
        }
        if is_sse_response(clean_resp_headers):
            resp_payload["chunks"] = parse_sse_chunks(resp_body)
            step.is_streaming = True
        step.resp_blob = self._blobs.write(
            json.dumps(resp_payload, sort_keys=True).encode()
        )
        step.input_tok, step.output_tok = _parse_token_counts(resp_body)

        self._db.save_step(step)
