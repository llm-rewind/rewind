"""Unit tests for request normalization (ADR-004)."""

from __future__ import annotations

import json

import pytest

from rewind.proxy.normalize import normalize_request, strip_headers

# --- normalize_request ---


@pytest.mark.unit
def test_normalize_same_body_same_key() -> None:
    body = json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}).encode()
    k1 = normalize_request("POST", "/v1/chat/completions", body)
    k2 = normalize_request("POST", "/v1/chat/completions", body)
    assert k1 == k2


@pytest.mark.unit
def test_normalize_returns_sha256_hex() -> None:
    body = b""
    key = normalize_request("POST", "/v1/chat/completions", body)
    assert len(key) == 64
    int(key, 16)  # raises if not hex


@pytest.mark.unit
def test_normalize_strips_tool_call_id() -> None:
    """tool_call_id is volatile — same logical request should produce same key."""
    make_body = lambda tid: json.dumps({  # noqa: E731
        "model": "gpt-4o",
        "messages": [{"role": "tool", "content": "result", "tool_call_id": tid}],
    }).encode()
    k1 = normalize_request("POST", "/v1/chat/completions", make_body("abc-111"))
    k2 = normalize_request("POST", "/v1/chat/completions", make_body("xyz-999"))
    assert k1 == k2


@pytest.mark.unit
def test_normalize_strips_tool_call_ids_in_tool_calls() -> None:
    """tool_calls[].id is volatile and must be stripped."""
    make_body = lambda tid: json.dumps({  # noqa: E731
        "model": "gpt-4o",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": tid, "type": "function", "function": {"name": "f", "arguments": "{}"}},
                ],
            }
        ],
    }).encode()
    k1 = normalize_request("POST", "/v1/chat/completions", make_body("call_111"))
    k2 = normalize_request("POST", "/v1/chat/completions", make_body("call_999"))
    assert k1 == k2


@pytest.mark.unit
def test_normalize_tools_sorted_by_name() -> None:
    """Tool order must not affect match_key."""
    tools_ab = [{"name": "a", "description": "first"}, {"name": "b", "description": "second"}]
    tools_ba = [{"name": "b", "description": "second"}, {"name": "a", "description": "first"}]
    k1 = normalize_request("POST", "/v1/chat/completions", json.dumps({"tools": tools_ab}).encode())
    k2 = normalize_request("POST", "/v1/chat/completions", json.dumps({"tools": tools_ba}).encode())
    assert k1 == k2


@pytest.mark.unit
def test_normalize_different_content_different_key() -> None:
    b1 = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
    b2 = json.dumps({"messages": [{"role": "user", "content": "goodbye"}]}).encode()
    assert normalize_request("POST", "/v1/chat/completions", b1) != normalize_request(
        "POST", "/v1/chat/completions", b2
    )


@pytest.mark.unit
def test_normalize_different_path_different_key() -> None:
    body = json.dumps({"model": "claude-3"}).encode()
    k1 = normalize_request("POST", "/v1/chat/completions", body)
    k2 = normalize_request("POST", "/v1/messages", body)
    assert k1 != k2


@pytest.mark.unit
def test_normalize_empty_body() -> None:
    key = normalize_request("POST", "/v1/chat/completions", b"")
    assert len(key) == 64


@pytest.mark.unit
def test_normalize_non_json_body_uses_hex() -> None:
    raw = b"\xff\xfe binary data"
    key = normalize_request("POST", "/v1/chat/completions", raw)
    assert len(key) == 64


@pytest.mark.unit
@pytest.mark.parametrize("messages,expected_equal", [
    # Empty messages list
    ([], True),
    # Messages with no tool calls — tool_call_id absent, no change
    ([{"role": "user", "content": "hi"}], True),
])
def test_normalize_edge_cases_stable(messages: list, expected_equal: bool) -> None:
    body = json.dumps({"messages": messages}).encode()
    k1 = normalize_request("POST", "/v1/chat/completions", body)
    k2 = normalize_request("POST", "/v1/chat/completions", body)
    assert (k1 == k2) == expected_equal


# --- strip_headers ---


@pytest.mark.unit
def test_strip_headers_removes_auth() -> None:
    headers = {
        "Authorization": "Bearer sk-secret",
        "x-api-key": "secret-key",
        "Content-Type": "application/json",
    }
    result = strip_headers(headers)
    assert "Authorization" not in result
    assert "x-api-key" not in result
    assert "Content-Type" in result


@pytest.mark.unit
def test_strip_headers_removes_stainless_prefix() -> None:
    headers = {
        "x-stainless-lang": "python",
        "x-stainless-version": "1.0",
        "content-type": "application/json",
    }
    result = strip_headers(headers)
    assert "x-stainless-lang" not in result
    assert "x-stainless-version" not in result
    assert "content-type" in result


@pytest.mark.unit
def test_strip_headers_case_insensitive() -> None:
    headers = {"AUTHORIZATION": "Bearer sk-abc", "X-API-KEY": "key"}
    result = strip_headers(headers)
    assert not result  # both stripped


@pytest.mark.unit
def test_strip_headers_no_auth_passthrough() -> None:
    headers = {"content-type": "application/json", "accept": "*/*"}
    result = strip_headers(headers)
    assert result == headers
