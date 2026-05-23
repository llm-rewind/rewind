"""Unit tests for SSE chunk parsing and reconstruction."""

from __future__ import annotations

import pytest

from rewind.proxy.streaming import (
    has_done_sentinel,
    is_sse_response,
    parse_sse_chunks,
    reconstruct_sse_body,
)

_SAMPLE_SSE = (
    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
    b"data: [DONE]\n\n"
)


@pytest.mark.unit
def test_parse_produces_one_chunk_per_event() -> None:
    chunks = parse_sse_chunks(_SAMPLE_SSE)
    assert len(chunks) == 3


@pytest.mark.unit
def test_parse_chunk_has_data_and_delay() -> None:
    chunks = parse_sse_chunks(_SAMPLE_SSE)
    for c in chunks:
        assert "data" in c
        assert "delay_ms" in c
        assert c["delay_ms"] == 0


@pytest.mark.unit
def test_reconstruct_roundtrip() -> None:
    chunks = parse_sse_chunks(_SAMPLE_SSE)
    reconstructed = reconstruct_sse_body(chunks)
    assert reconstructed == _SAMPLE_SSE


@pytest.mark.unit
def test_parse_empty_body() -> None:
    assert parse_sse_chunks(b"") == []


@pytest.mark.unit
def test_parse_single_chunk_no_trailing_newline() -> None:
    raw = b"data: hello\n"
    chunks = parse_sse_chunks(raw)
    assert len(chunks) == 1
    assert "hello" in chunks[0]["data"]


@pytest.mark.unit
def test_has_done_sentinel_true() -> None:
    chunks = parse_sse_chunks(_SAMPLE_SSE)
    assert has_done_sentinel(chunks)


@pytest.mark.unit
def test_has_done_sentinel_false() -> None:
    raw = b'data: {"content":"hi"}\n\n'
    chunks = parse_sse_chunks(raw)
    assert not has_done_sentinel(chunks)


@pytest.mark.unit
def test_done_sentinel_is_last() -> None:
    chunks = parse_sse_chunks(_SAMPLE_SSE)
    done_indices = [i for i, c in enumerate(chunks) if "data: [DONE]" in c["data"]]
    assert done_indices == [len(chunks) - 1]


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"content-type": "text/event-stream"}, True),
        ({"content-type": "text/event-stream; charset=utf-8"}, True),
        ({"content-type": "application/json"}, False),
        ({}, False),
    ],
)
def test_is_sse_response(headers: dict[str, str], expected: bool) -> None:
    assert is_sse_response(headers) is expected


@pytest.mark.unit
def test_reconstruct_empty_chunks() -> None:
    assert reconstruct_sse_body([]) == b""


@pytest.mark.unit
def test_parse_anthropic_style_sse() -> None:
    raw = (
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
        b"event: message_stop\n"
        b'data: {"type":"message_stop"}\n\n'
    )
    chunks = parse_sse_chunks(raw)
    assert len(chunks) == 2
    reconstructed = reconstruct_sse_body(chunks)
    assert reconstructed == raw
