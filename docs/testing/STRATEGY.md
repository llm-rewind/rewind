# Testing Strategy

## Core Rule: No Live LLM API Calls in Tests

All tests use committed cassettes. The test suite must pass with no API keys configured.
CI enforces this: cassette miss in test mode raises `CassetteMissError`, never silently passthroughs.

**Why:** Live API calls introduce cost, flakiness, and non-determinism. Cassettes make tests fast, free, and reproducible.

## Test Structure

```
tests/
├── unit/           # pure logic, no I/O, no cassettes, no mitmproxy
├── integration/    # cassette-based; exercise full record→replay cycle
└── cassettes/      # committed to git; never auto-regenerated in CI
```

## Cassette Lifecycle

1. **Record (local only):** `REWIND_RECORD_MODE=record pytest -m record` — calls real APIs, saves cassettes
2. **Commit:** Cassettes go in `tests/cassettes/`, committed to git
3. **CI:** `pytest` runs replay mode only; cassette miss = test fail

Never regenerate cassettes in CI. Never set `passthrough=True` in test configurations.

## Unit Tests (no cassettes required)

Cover:
- `normalize.py` — `match_key` computation, `tool_call_id` stripping, header filtering, tool sort order
- `blobs.py` — SHA-256 computation, hash verification, tampered blob detection
- `bisect.py` — divergence algorithm correctness
- `diff.py` — step diff output

## Integration Tests (cassette-based)

Cover:
- Full record → store → replay cycle produces identical output
- Streaming: SSE chunk sequence preserved on replay
- Cassette miss: raises `CassetteMissError` in STRICT mode
- Passthrough: `--permissive` flag calls live API and logs warning
- Multi-step: `order_idx` ordering maintained across replay
- Auth headers: verify `Authorization` absent from stored cassette blob

## What Tests Must Validate

- Correct behavior, not just absence of exceptions
- Edge cases: empty messages, parallel tool calls, retry (same request twice), streaming vs non-streaming
- Error paths: tampered blob (SHA-256 mismatch), missing cassette, malformed SSE, corrupt DuckDB
- Security invariants: auth headers not present in stored cassettes

## Anti-Hallucination in Tests

- Never assert fabricated API response formats — use real recorded cassettes
- Known-good cassettes must be verified against real provider before committing
- If provider API format changes, update cassettes by re-recording locally

## Completion Criteria

Work is done when:
- `pytest` passes (all cassettes committed, no live calls)
- `ruff check src/ tests/` passes
- `mypy src/ --strict` passes
- New behavior has at least one integration test with committed cassette
