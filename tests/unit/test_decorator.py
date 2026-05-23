"""Unit tests for @rewind.tool decorator — record, replay, and passthrough modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewind.exceptions import CassetteMissError
from rewind.sdk.decorator import _TOOL_CTX, ToolContext, _compute_input_hash, tool
from rewind.storage.blobs import BlobStore

# ---------------------------------------------------------------------------
# input_hash stability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_input_hash_stable_same_args() -> None:
    h1 = _compute_input_hash(("hello", 42), {"key": "val"})
    h2 = _compute_input_hash(("hello", 42), {"key": "val"})
    assert h1 == h2


@pytest.mark.unit
def test_input_hash_differs_different_args() -> None:
    h1 = _compute_input_hash(("hello",), {})
    h2 = _compute_input_hash(("world",), {})
    assert h1 != h2


@pytest.mark.unit
def test_input_hash_is_hex_string() -> None:
    h = _compute_input_hash((), {})
    assert len(h) == 64
    int(h, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# sync @rewind.tool — record mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_record_calls_function(tmp_path: Path) -> None:
    call_count = 0

    @tool
    def fn(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x * 2

    store = BlobStore(tmp_path)
    ctx = ToolContext(mode="record", blobs=store, session_id="s1")
    token = _TOOL_CTX.set(ctx)
    try:
        result = fn(5)
    finally:
        _TOOL_CTX.reset(token)

    assert result == 10
    assert call_count == 1
    assert len(ctx.new_calls) == 1


@pytest.mark.unit
def test_sync_record_stores_retrievable_blob(tmp_path: Path) -> None:
    @tool
    def fn(q: str) -> dict[str, str]:
        return {"answer": q.upper()}

    store = BlobStore(tmp_path)
    ctx = ToolContext(mode="record", blobs=store, session_id="s1")
    token = _TOOL_CTX.set(ctx)
    try:
        fn("hello")
    finally:
        _TOOL_CTX.reset(token)

    _, blob_hash = ctx.new_calls[0]
    stored = json.loads(store.read(blob_hash))
    assert stored == {"answer": "HELLO"}


# ---------------------------------------------------------------------------
# sync @rewind.tool — replay mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_replay_does_not_call_function(tmp_path: Path) -> None:
    call_count = 0

    @tool
    def fn(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x * 2

    store = BlobStore(tmp_path)
    blob_hash = store.write(json.dumps(42).encode())
    input_hash = _compute_input_hash((21,), {})

    ctx = ToolContext(
        mode="replay",
        blobs=store,
        session_id="s1",
        recorded={input_hash: blob_hash},
    )
    token = _TOOL_CTX.set(ctx)
    try:
        result = fn(21)
    finally:
        _TOOL_CTX.reset(token)

    assert result == 42
    assert call_count == 0


@pytest.mark.unit
def test_sync_replay_miss_raises(tmp_path: Path) -> None:
    @tool
    def fn(x: int) -> int:
        return x

    store = BlobStore(tmp_path)
    ctx = ToolContext(mode="replay", blobs=store, session_id="s1", recorded={})
    token = _TOOL_CTX.set(ctx)
    try:
        with pytest.raises(CassetteMissError):
            fn(99)
    finally:
        _TOOL_CTX.reset(token)


# ---------------------------------------------------------------------------
# sync @rewind.tool — passthrough (no context)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_passthrough_no_context() -> None:
    @tool
    def fn(x: int) -> int:
        return x + 1

    assert fn(5) == 6


# ---------------------------------------------------------------------------
# async @rewind.tool — record mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_async_record_calls_function(tmp_path: Path) -> None:
    call_count = 0

    @tool
    async def fn(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x * 3

    store = BlobStore(tmp_path)
    ctx = ToolContext(mode="record", blobs=store, session_id="s1")
    token = _TOOL_CTX.set(ctx)
    try:
        result = await fn(4)
    finally:
        _TOOL_CTX.reset(token)

    assert result == 12
    assert call_count == 1
    assert len(ctx.new_calls) == 1


# ---------------------------------------------------------------------------
# async @rewind.tool — replay mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_async_replay_does_not_call_function(tmp_path: Path) -> None:
    call_count = 0

    @tool
    async def fn(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x * 3

    store = BlobStore(tmp_path)
    blob_hash = store.write(json.dumps(99).encode())
    input_hash = _compute_input_hash((7,), {})

    ctx = ToolContext(
        mode="replay",
        blobs=store,
        session_id="s1",
        recorded={input_hash: blob_hash},
    )
    token = _TOOL_CTX.set(ctx)
    try:
        result = await fn(7)
    finally:
        _TOOL_CTX.reset(token)

    assert result == 99
    assert call_count == 0


@pytest.mark.unit
async def test_async_replay_miss_raises(tmp_path: Path) -> None:
    @tool
    async def fn(x: int) -> int:
        return x

    store = BlobStore(tmp_path)
    ctx = ToolContext(mode="replay", blobs=store, session_id="s1", recorded={})
    token = _TOOL_CTX.set(ctx)
    try:
        with pytest.raises(CassetteMissError):
            await fn(1)
    finally:
        _TOOL_CTX.reset(token)


@pytest.mark.unit
async def test_async_passthrough_no_context() -> None:
    @tool
    async def fn(x: int) -> int:
        return x + 10

    assert await fn(5) == 15
