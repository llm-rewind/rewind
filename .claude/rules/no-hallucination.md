---
paths:
  - "src/**"
  - "tests/**"
  - "docs/**"
---

# Anti-Hallucination Rules

## Provider API Formats

Never assume OpenAI or Anthropic request/response formats. Verify against:
- OpenAI: https://platform.openai.com/docs/api-reference
- Anthropic: https://docs.anthropic.com/en/api/messages

SSE chunk format, tool_call structure, and streaming termination (`data: [DONE]`) change across model versions.
Verify format before implementing. If uncertain, record a real cassette and inspect it.

## Cassette and Storage Schema

Never add fields to cassette format or DuckDB schema without:
1. Checking existing cassettes remain readable (backward compatibility)
2. Adding schema migration logic in `src/rewind/storage/db.py`
3. Flagging as Medium or High Risk in the PR

## match_key Claims

Never claim a request field is "safe to strip" from `match_key` without verifying it does not affect LLM semantics.
The approved strip list is in `ADR-004` and `src/rewind/proxy/normalize.py`. Changes require updating the ADR.

## External Library Behavior

Never claim mitmproxy, DuckDB, or zstandard behavior without citing their docs or source.
If uncertain whether an API does what you expect: say so, then verify with a minimal test.

## SHA-256 and Blob Integrity

Never claim a blob is safe to use without verifying its hash. Never propose skipping the integrity check for performance.
The integrity check is not optional; it is a security and correctness guarantee.

## Benchmark and Performance Claims

Never claim performance numbers (replay latency, compression ratio, storage size) without measurement.
Label all performance estimates explicitly as "estimated" or "approximate."
