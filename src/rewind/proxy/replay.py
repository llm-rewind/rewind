"""ReplayAddon — serves recorded cassette responses without calling real LLMs.

Cassette miss: CassetteMissError (strict mode) or passthrough warning (permissive mode).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque

from mitmproxy import http

from rewind.constants import PROVIDER_HOSTS
from rewind.exceptions import CassetteMissError
from rewind.proxy.normalize import normalize_request
from rewind.proxy.streaming import reconstruct_sse_body
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Step

_LOG = logging.getLogger(__name__)


class ReplayAddon:
    def __init__(
        self,
        db: RewindDB,
        blobs: BlobStore,
        session_id: str,
        *,
        permissive: bool = False,
    ) -> None:
        self._blobs = blobs
        self._session_id = session_id
        self._permissive = permissive
        # match_key → ordered queue of steps (order_idx ASC)
        self._cassette: dict[str, deque[Step]] = defaultdict(deque)
        self._load_cassette(db)

    def _load_cassette(self, db: RewindDB) -> None:
        """Preload steps into match_key-keyed deques, ordered by order_idx."""
        for step in db.get_steps(self._session_id):
            if step.match_key is not None and step.resp_blob is not None:
                self._cassette[step.match_key].append(step)

    def request(self, flow: http.HTTPFlow) -> None:
        """Intercept LLM API requests and serve recorded responses."""
        if PROVIDER_HOSTS.get(flow.request.host) is None:
            return

        req_body = flow.request.content or b""
        match_key = normalize_request(flow.request.method, flow.request.path, req_body)

        queue = self._cassette.get(match_key)
        if not queue:
            if self._permissive:
                _LOG.warning(
                    "Cassette miss for match_key %s... — passing through (permissive mode)",
                    match_key[:8],
                )
                return
            raise CassetteMissError(match_key, self._session_id)

        step = queue.popleft()
        assert step.resp_blob is not None  # guaranteed by _load_cassette filter

        resp_bytes = self._blobs.read(step.resp_blob)  # SHA-256 verified on every read
        resp_data = json.loads(resp_bytes)

        headers: dict[str, str] = resp_data.get("headers", {})
        if step.is_streaming and "chunks" in resp_data:
            body_bytes = reconstruct_sse_body(resp_data["chunks"])
        else:
            body_bytes = resp_data.get("body", "").encode("utf-8")

        flow.response = http.Response.make(
            status_code=int(resp_data.get("status_code", 200)),
            content=body_bytes,
            headers=headers,
        )
