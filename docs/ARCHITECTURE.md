# Rewind Architecture

## Overview

Rewind records every LLM API call made by an AI agent, stores responses as content-addressed cassettes, and replays them deterministically — enabling root-cause analysis of any production failure with zero LLM cost.

## System Layers

```
┌─────────────────────────────────────────────────────────┐
│  CAPTURE (dual-mode)                                    │
│  Mode A: HTTP MITM Proxy  ← primary, any lang/framework │
│  Mode B: SDK Decorator    ← Python convenience          │
├─────────────────────────────────────────────────────────┤
│  STORAGE (content-addressed)                            │
│  DuckDB metadata + blob filesystem                      │
├─────────────────────────────────────────────────────────┤
│  REPLAY ENGINE ← core innovation                        │
│  Match incoming request → serve recorded bytes verbatim │
├─────────────────────────────────────────────────────────┤
│  BISECT ENGINE                                          │
│  Find exact step where run-A diverged from run-B        │
└─────────────────────────────────────────────────────────┘
```

## Layer 1: Capture

### Mode A — HTTP MITM Proxy (primary)

Engine: `mitmproxy` embedded via `DumpMaster` + addon API.

Intercepts HTTPS to:
- `api.openai.com`
- `api.anthropic.com`
- `generativelanguage.googleapis.com`

No certificate pinning in `openai-python` or `anthropic-python` (both use `httpx` with system cert validation). MITM works without special bypasses.

CA cert installed once via `rewind init` → `~/.rewind/ca.pem`.

SDK configuration required:
```bash
export HTTPS_PROXY=127.0.0.1:8080
export SSL_CERT_FILE=~/.rewind/ca.pem
```

Or use `rewind record <command>` wrapper which sets these automatically.

Language-agnostic: works with Python, Node.js, Go, or any HTTP client.

### Mode B — SDK Decorator (Python convenience, no proxy setup)

```python
import rewind

@rewind.session(name="customer_support")
async def run_agent(query: str) -> str:
    ...

@rewind.tool  # required for non-HTTP (pure Python) tool calls
def search_db(query: str) -> list[dict]:
    ...
```

Patches `openai.AsyncOpenAI` and `anthropic.Anthropic` at the transport layer.
Does not intercept tool calls that make HTTP requests (proxy handles those).

## Layer 2: Storage

### Metadata — DuckDB (`~/.rewind/db.duckdb`)

```sql
CREATE TABLE sessions (
    id              VARCHAR PRIMARY KEY,
    agent_name      VARCHAR,
    git_hash        VARCHAR,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    total_cost_usd  FLOAT,
    metadata        JSON
);

CREATE TABLE steps (
    id           VARCHAR PRIMARY KEY,
    session_id   VARCHAR REFERENCES sessions(id),
    parent_id    VARCHAR,        -- multi-agent tree (OTel-style spans)
    order_idx    INTEGER,        -- deterministic replay ordering
    type         VARCHAR,        -- llm_call | tool_call | event
    provider     VARCHAR,        -- openai | anthropic | gemini
    model        VARCHAR,
    match_key    VARCHAR,        -- request fingerprint (see ADR-004)
    req_blob     VARCHAR,        -- SHA-256 → blobs/
    resp_blob    VARCHAR,        -- SHA-256 → blobs/
    input_tok    INTEGER,
    output_tok   INTEGER,
    latency_ms   INTEGER,
    started_at   TIMESTAMPTZ,
    is_streaming BOOLEAN
);
```

Schema changes are **High Risk** (require migration, cassette compatibility check).

### Blobs — Content-Addressed Filesystem (`~/.rewind/blobs/<sha256[:2]>/<sha256>`)

- Immutable. Write-once, SHA-256 verified on every read.
- zstd compressed (~70-80% reduction on JSON payloads).
- Deduplicates: identical system prompts stored once across thousands of runs.
- LLM responses (<100KB): stored inline. Audio/images: stored as filesystem path reference.

See ADR-003.

## Layer 3: Replay Engine

```
1. Load session steps from DuckDB ordered by order_idx
2. Start mitmproxy in REPLAY mode
3. Incoming LLM request → compute match_key (see Normalization)
4. Look up step by match_key
5. Serve resp_blob bytes verbatim (zero LLM cost)
   - Streaming: replay SSE chunks via asyncio generator in recorded sequence
   - Non-streaming: return JSON directly
6. Tool calls (@rewind.tool): match by input_hash, return recorded output
7. Cassette miss:
   - STRICT mode (default):  raise CassetteMissError
   - PERMISSIVE mode (--permissive flag): passthrough to real API, log warning
```

## Layer 4: Bisection Engine

```
Input:  session_a (good run), session_b (bad run)

1. Walk steps in parallel by order_idx
2. Find first step where resp_blob differs
3. Report:
   - Step number and type
   - Model (may have changed between runs)
   - Diff of good_output vs bad_output
4. Infer likely cause: model version change, prompt drift, tool output change
```

## Request Normalization

`match_key = SHA-256(canonical_json)` — inspired by Docker cagent's tool_call_id normalization.

Included in match_key:
```python
{
    "model": str,
    "messages": messages_with_tool_call_ids_stripped,
    "tools": tools_sorted_by_name,
    "temperature": float | None,
    "max_tokens": int | None,
    "system": str | None,   # Anthropic
}
```

Stripped before hashing (volatile, not semantic):
- `tool_call_id` values (random per run, e.g. `call_abc123`)
- `stream` flag, `user` field, `n` field

Stripped from cassette storage entirely (secrets):
- `Authorization`, `x-api-key`
- `x-request-id`, `idempotency-key`
- All `x-stainless-*` headers (openai-python SDK telemetry)
- `cf-ray`, `cf-cache-status`, `set-cookie`

See ADR-004 and `src/rewind/proxy/normalize.py`.

## Streaming (SSE)

OpenAI and Anthropic use Server-Sent Events for streaming.

Record: capture raw SSE lines as ordered JSON array:
```json
[
  {"data": "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n", "delay_ms": 41},
  {"data": "data: [DONE]\n\n", "delay_ms": 12}
]
```

Replay: stream via asyncio generator. `--instant` flag strips delays (CI/testing use).

## Multi-Agent Support

- Each agent run → one `session_id`
- Parent-child agent calls tracked via `parent_id` (OTel-style span tree)
- Bisection walks full agent tree

## Project Layout

```
src/rewind/
├── cli.py                 # click entry points
├── constants.py           # no magic values elsewhere
├── proxy/
│   ├── addon.py           # mitmproxy DumpMaster lifecycle
│   ├── record.py          # RecordAddon: intercept + save
│   ├── replay.py          # ReplayAddon: match + serve
│   └── normalize.py       # match_key + header stripping (ADR-004)
├── storage/
│   ├── db.py              # DuckDB schema + queries
│   └── blobs.py           # content-addressed filesystem
├── engines/
│   ├── replay.py          # orchestrates replay run
│   ├── diff.py            # step-by-step session diff
│   └── bisect.py          # divergence finder
├── sdk/
│   ├── decorator.py       # @rewind.session, @rewind.tool
│   └── patches.py         # openai/anthropic SDK patches
└── ui/
    └── display.py         # rich tables + diffs

tests/
├── unit/                  # pure logic, no I/O
├── integration/           # cassette-based
└── cassettes/             # committed to git

pytest_rewind/
└── plugin.py              # pip install pytest-rewind
```

## Key Dependencies

| Package | Version | Purpose | ADR |
|---------|---------|---------|-----|
| mitmproxy | ^11 | MITM proxy engine | ADR-001 |
| duckdb | ^1.2 | embedded analytics DB | ADR-002 |
| zstandard | ^0.23 | blob compression | ADR-003 |
| click | ^8.1 | CLI |  |
| rich | ^13 | terminal UI |  |
| pydantic | ^2 | config + data models |  |

## Inspiration / Prior Art

| Source | What We Took |
|--------|-------------|
| VCR.py (2.5k★) | Cassette pattern, request matching strategy |
| Docker cagent | `tool_call_id` normalization, cassette format |
| Mozilla rr | Bisection mental model |
| mcp-recorder | Transport-agnostic JSON-RPC recording pattern |
| vcr-langchain (82★) | Proof LLM cassette concept works |
| pytest-recording | pytest plugin cassette path conventions |
