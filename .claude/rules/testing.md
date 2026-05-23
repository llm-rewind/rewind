---
paths:
  - "tests/**"
  - "pytest_rewind/**"
---

# Testing Rules

## Never Call Real LLM APIs

Tests must never call `api.openai.com`, `api.anthropic.com`, or any real LLM provider.
Use committed cassettes in `tests/cassettes/`.

If a cassette doesn't exist: record locally with `REWIND_RECORD_MODE=record`, verify the cassette captures real behavior, then commit.

Never add `passthrough=True` in test configuration. Never set `REWIND_MODE=permissive` in tests.

## Cassette Miss = Hard Fail

In test mode, cassette miss must raise `CassetteMissError`. This is the correct behavior — do not suppress or work around it.

## What Tests Must Cover

- Correct behavior, not just absence of exceptions
- `match_key` normalization: `tool_call_id` stripped, headers filtered, tools sorted
- Error paths: tampered blob (`BlobTamperedError`), missing cassette, malformed SSE stream, `order_idx` ordering
- Security invariant: stored cassette blob does not contain `Authorization` or `x-api-key`

## No Test Fabrication

Never assert on fabricated response formats. Use real cassettes recorded against actual providers.
If provider API format changes, update cassettes by re-recording locally, then commit.

## Parametrize Edge Cases

Use `pytest.mark.parametrize` for normalization edge cases:
- Empty messages list
- Messages with no tool calls
- Messages with multiple parallel tool calls (same request hash, sequence match)
- Streaming vs non-streaming for same logical request
