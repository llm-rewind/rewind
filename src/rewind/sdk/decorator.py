"""@rewind.session and @rewind.tool decorators.

Alternative to HTTP proxy mode — pure Python, zero setup, no CA cert needed.
Required for non-HTTP tool calls (pure Python functions).
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import uuid
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar, overload

from rewind.constants import BLOB_DIR
from rewind.exceptions import CassetteMissError
from rewind.storage.blobs import BlobStore

_P = ParamSpec("_P")
_R = TypeVar("_R")
_AsyncFn = TypeVar("_AsyncFn", bound=Callable[..., Coroutine[Any, Any, Any]])


@dataclass
class ToolContext:
    """Active recording/replay context propagated to @rewind.tool calls via ContextVar."""

    mode: str  # "record" | "replay" | "passthrough"
    blobs: BlobStore
    session_id: str
    recorded: dict[str, str] = field(default_factory=dict)  # input_hash → blob_hash
    new_calls: list[tuple[str, str]] = field(default_factory=list)  # (input_hash, blob_hash)


_TOOL_CTX: ContextVar[ToolContext | None] = ContextVar("rewind_tool_ctx", default=None)


def current_session() -> ToolContext | None:
    """Return the active ToolContext, or None if outside a @rewind.session."""
    return _TOOL_CTX.get()


def _compute_input_hash(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Stable SHA-256 fingerprint of a function call's arguments."""
    payload = {"args": list(args), "kwargs": kwargs}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@overload
def tool(fn: Callable[_P, Coroutine[Any, Any, _R]]) -> Callable[_P, Coroutine[Any, Any, _R]]: ...
@overload
def tool(fn: Callable[_P, _R]) -> Callable[_P, _R]: ...
def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Python function for Rewind tool recording/replay."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def _async(*args: Any, **kwargs: Any) -> Any:
            ctx = _TOOL_CTX.get()
            if ctx is None or ctx.mode == "passthrough":
                return await fn(*args, **kwargs)
            input_hash = _compute_input_hash(args, kwargs)
            if ctx.mode == "replay":
                blob_hash = ctx.recorded.get(input_hash)
                if blob_hash is None:
                    raise CassetteMissError(input_hash, ctx.session_id)
                return json.loads(ctx.blobs.read(blob_hash))
            result = await fn(*args, **kwargs)
            serialized = json.dumps(result, default=str).encode()
            blob_hash = ctx.blobs.write(serialized)
            ctx.new_calls.append((input_hash, blob_hash))
            return result

        return _async

    @functools.wraps(fn)
    def _sync(*args: Any, **kwargs: Any) -> Any:
        ctx = _TOOL_CTX.get()
        if ctx is None or ctx.mode == "passthrough":
            return fn(*args, **kwargs)
        input_hash = _compute_input_hash(args, kwargs)
        if ctx.mode == "replay":
            blob_hash = ctx.recorded.get(input_hash)
            if blob_hash is None:
                raise CassetteMissError(input_hash, ctx.session_id)
            return json.loads(ctx.blobs.read(blob_hash))
        result = fn(*args, **kwargs)
        serialized = json.dumps(result, default=str).encode()
        blob_hash = ctx.blobs.write(serialized)
        ctx.new_calls.append((input_hash, blob_hash))
        return result

    return _sync


def session(
    *,
    name: str,
    mode: str = "passthrough",
    blobs: BlobStore | None = None,
) -> Callable[[_AsyncFn], _AsyncFn]:
    """Decorator for agent entry points — establishes a tool recording/replay session."""

    def decorator(fn: _AsyncFn) -> _AsyncFn:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            store = blobs if blobs is not None else BlobStore(BLOB_DIR)
            ctx = ToolContext(
                mode=mode,
                blobs=store,
                session_id=str(uuid.uuid4()),
            )
            token = _TOOL_CTX.set(ctx)
            try:
                return await fn(*args, **kwargs)
            finally:
                _TOOL_CTX.reset(token)

        return wrapper  # type: ignore[return-value]

    return decorator
