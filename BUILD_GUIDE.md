# Rewind — Build Execution Playbook

Follow this document blindly. Every prompt is copy-paste ready.
Every checkpoint has exact commands to run. Every gate must pass before moving forward.

---

## How Context Works

Claude Code loses memory between sessions. Every new session needs a **starter prompt**.
Clear context (start new session) at every CHECKPOINT PASSED marker.
Never clear mid-task — you'll lose the thread.

**Rule: one sub-task per session. Finish it. Pass the checkpoint. Clear. Repeat.**

---

## Project Status Tracker

Update this manually as you progress. Use it to know where you are.

```
[ ] PHASE 0 — Environment Setup
[ ] PHASE 1.1 — Project Scaffold
[ ] PHASE 1.2 — DuckDB Schema + Blob Store
[ ] PHASE 1.3 — mitmproxy RecordAddon
[ ] PHASE 1.4 — CLI: rewind init + rewind record
[ ] PHASE 1.5 — CLI: rewind list + rewind inspect
[ ] PHASE 1.6 — Phase 1 Gate (unit tests + lint + types)
[ ] PHASE 2.1 — match_key Normalization
[ ] PHASE 2.2 — ReplayAddon + CassetteMissError
[ ] PHASE 2.3 — SSE Streaming Replay
[ ] PHASE 2.4 — CLI: rewind replay + rewind diff
[ ] PHASE 2.5 — @rewind.tool Decorator
[ ] PHASE 2.6 — pytest-rewind Plugin
[ ] PHASE 2.7 — Phase 2 Gate (integration tests + commit cassettes)
[ ] PHASE 3.1 — Bisection Engine
[ ] PHASE 3.2 — CLI: rewind bisect
[ ] PHASE 3.3 — Export / Import Cassettes
[ ] PHASE 3.4 — rewind stats (Cost Analytics)
[ ] PHASE 3.5 — Phase 3 Gate (full E2E test)
[ ] PHASE 3.6 — Launch Prep
```

---

## PHASE 0 — One-Time Environment Setup

Do this once. Never again.

### Commands to run yourself (not Claude)

```bash
# Install Python 3.11+
python --version   # must show 3.11 or higher

# Install pipx for tool isolation
pip install pipx
pipx ensurepath

# Create virtualenv for the project
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux

# Verify pip works
pip --version
```

### Checkpoint: PHASE 0

```bash
python --version     # 3.11+
pip --version        # any recent version
```

Both pass → mark PHASE 0 done. Move to PHASE 1.1.

---

## PHASE 1.1 — Project Scaffold

### Starter Prompt (paste this at session start)

```
I'm building Rewind — an open source AI agent record & replay debugger.
Like Mozilla rr but for LLM agents. Framework-agnostic HTTP MITM proxy approach.

Stack: Python 3.11+, mitmproxy ^11, DuckDB ^1.2, zstandard ^0.23, click ^8.1, rich ^13, pydantic ^2.

Read CLAUDE.md first, then docs/ARCHITECTURE.md.

Current phase: PHASE 1.1 — Project Scaffold
Task: Create the complete project scaffold. No implementation logic yet — just the structure.

Create:
- pyproject.toml (with all dependencies + dev deps: pytest, ruff, mypy, pytest-asyncio)
- src/rewind/__init__.py
- src/rewind/constants.py (all config values: default proxy port 8080, db path ~/.rewind/db.duckdb, blob dir ~/.rewind/blobs, CA cert path ~/.rewind/ca.pem, CA key path ~/.rewind/ca.key)
- src/rewind/exceptions.py (CassetteMissError, BlobTamperedError, RewindError base)
- src/rewind/cli.py (empty click group with: init, record, replay, list, inspect, diff, bisect, export, import, stats — all as stubs that print "not implemented yet")
- src/rewind/proxy/__init__.py, record.py, replay.py, normalize.py, addon.py (empty modules with docstrings)
- src/rewind/storage/__init__.py, db.py, blobs.py (empty modules with docstrings)
- src/rewind/engines/__init__.py, replay.py, diff.py, bisect.py (empty modules)
- src/rewind/sdk/__init__.py, decorator.py, patches.py (empty modules)
- src/rewind/ui/__init__.py, display.py (empty module)
- tests/__init__.py, tests/unit/__init__.py, tests/integration/__init__.py
- tests/conftest.py (empty for now)
- pytest.ini or pyproject.toml pytest config (marks: unit, integration, record)
- .gitignore (Python standard + .rewind/ local data dir)
- ruff.toml or ruff config in pyproject.toml (line-length 100, target py311)
- mypy.ini or mypy config (strict mode)

Do not implement any logic. Stubs only. Make `pip install -e ".[dev]"` work and `rewind --help` show all commands.
```

### Checkpoint: PHASE 1.1

```bash
pip install -e ".[dev]"
rewind --help              # shows all commands
rewind init                # prints "not implemented yet"
ruff check src/ tests/     # zero errors
mypy src/ --strict         # zero errors (stubs are fine)
pytest                     # 0 tests collected (no tests yet), no errors
```

All pass → mark PHASE 1.1 done. Clear context. Move to PHASE 1.2.

---

## PHASE 1.2 — DuckDB Schema + Blob Store

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Stack: Python 3.11+, mitmproxy, DuckDB, zstandard, click, rich, pydantic.

Read CLAUDE.md and docs/ARCHITECTURE.md and docs/adr/ADR-002-duckdb-for-metadata.md and docs/adr/ADR-003-content-addressed-blobs.md.

Current phase: PHASE 1.2 — DuckDB Schema + Blob Store
Scaffold already exists. All modules are stubs.

Implement:

1. src/rewind/storage/db.py
   - RewindDB class wrapping duckdb connection
   - create_schema() — creates sessions and steps tables (schema in docs/ARCHITECTURE.md)
   - get_or_create() classmethod returning singleton for default path
   - save_session(session: Session) -> None
   - save_step(step: Step) -> None
   - get_session(id: str) -> Session | None
   - list_sessions(limit: int = 50) -> list[Session]
   - get_steps(session_id: str) -> list[Step] ordered by order_idx
   - Pydantic models: Session, Step (fields in ARCHITECTURE.md)

2. src/rewind/storage/blobs.py
   - BlobStore class
   - write(data: bytes) -> str (returns sha256 hex, compresses with zstd, writes to ~/.rewind/blobs/<hash[:2]>/<hash>)
   - read(hash: str) -> bytes (decompresses, verifies sha256 — raises BlobTamperedError if mismatch)
   - exists(hash: str) -> bool
   - Path sharding: first 2 hex chars as subdir (prevents inode exhaustion)

3. tests/unit/test_storage.py
   - Test BlobStore: write + read roundtrip, tampered blob raises BlobTamperedError, exists(), deduplication (same content = same hash)
   - Test RewindDB: save session, get session, save steps, get steps ordered by order_idx
   - Use tmp_path pytest fixture for isolation (not ~/.rewind)

No mitmproxy yet. No CLI changes. Storage layer only.
```

### Checkpoint: PHASE 1.2

```bash
pytest tests/unit/test_storage.py -v    # all pass
ruff check src/ tests/                  # zero errors
mypy src/ --strict                      # zero errors
```

All pass → mark PHASE 1.2 done. Clear context. Move to PHASE 1.3.

---

## PHASE 1.3 — mitmproxy RecordAddon

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Stack: Python 3.11+, mitmproxy ^11, DuckDB, zstandard, click, rich, pydantic.

Read CLAUDE.md and docs/ARCHITECTURE.md and docs/adr/ADR-001-http-proxy-over-sdk-patching.md.

Current phase: PHASE 1.3 — mitmproxy RecordAddon
Storage layer (db.py + blobs.py) is complete and tested.

Implement:

1. src/rewind/proxy/normalize.py
   - compute_match_key(raw_body: bytes, provider: str) -> str
   - Implements ADR-004 normalization exactly: strips tool_call_ids, sorts tools by name, excludes stream/user/n
   - strip_sensitive_headers(headers: dict) -> dict — removes Authorization, x-api-key, x-request-id, idempotency-key, all x-stainless-*, cf-ray, cf-cache-status, set-cookie
   - PROVIDER_HOSTS: dict mapping hostnames to provider names (openai, anthropic, gemini)

2. src/rewind/proxy/record.py
   - RecordAddon class (mitmproxy addon)
   - Hooks: request(flow), response(flow)
   - Only processes flows where flow.request.host in PROVIDER_HOSTS
   - On response: strip sensitive headers, save req blob, save resp blob, save step to DB
   - Handles streaming (SSE): accumulates chunks, saves complete response
   - session_id injected at construction

3. src/rewind/proxy/addon.py
   - start_proxy(port: int, addon, mode: Literal["record","replay"]) -> asyncio.Task
   - Uses mitmproxy DumpMaster + Options (listen_host="127.0.0.1", listen_port=port)
   - Returns task so CLI can await it

4. tests/unit/test_normalize.py
   - Test compute_match_key: tool_call_id stripped, same hash with different tool_call_ids
   - Test compute_match_key: tools sorted regardless of input order
   - Test strip_sensitive_headers: all listed headers removed, safe headers preserved
   - Test PROVIDER_HOSTS mapping

Do NOT test the actual proxy network code — too complex for unit tests. Just normalize.py.
```

### Checkpoint: PHASE 1.3

```bash
pytest tests/unit/ -v      # all pass including new normalize tests
ruff check src/ tests/      # zero errors
mypy src/ --strict          # zero errors
```

All pass → mark PHASE 1.3 done. Clear context. Move to PHASE 1.4.

---

## PHASE 1.4 — CLI: rewind init + rewind record

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Stack: Python 3.11+, mitmproxy, DuckDB, click, rich, pydantic.

Read CLAUDE.md and docs/ARCHITECTURE.md.

Current phase: PHASE 1.4 — CLI: rewind init + rewind record
Storage (db.py, blobs.py), normalize.py, RecordAddon, and addon.py are complete.

Implement:

1. `rewind init` command in src/rewind/cli.py
   - Generates CA key + cert using cryptography library (add to pyproject.toml deps)
   - 4096-bit RSA, self-signed, 365-day validity, CN="Rewind Local CA"
   - Saves to ~/.rewind/ca.key (chmod 600) and ~/.rewind/ca.pem
   - Creates ~/.rewind/db.duckdb via RewindDB.get_or_create()
   - Creates ~/.rewind/blobs/ directory
   - Prints next steps: "Set HTTPS_PROXY=127.0.0.1:8080 and SSL_CERT_FILE=~/.rewind/ca.pem, then run: rewind record <your command>"
   - Rich output: green checkmarks for each step, clear error if already initialized

2. `rewind record <command>` command
   - Accepts command as variadic args: `rewind record python agent.py`
   - Creates new Session, saves to DB
   - Starts mitmproxy with RecordAddon in background thread (not async — use threading for CLI simplicity)
   - Sets HTTPS_PROXY and SSL_CERT_FILE env vars on subprocess
   - Runs the command as subprocess, streams stdout/stderr to terminal
   - On exit: stops proxy, prints session summary (id, steps recorded, total cost, duration)
   - Rich progress: spinner while running, summary table on completion

3. src/rewind/ui/display.py
   - print_session_summary(session: Session, steps: list[Step]) — rich table
   - print_init_success() — styled success output

CA private key must never appear in any log output. Test this explicitly.
```

### Checkpoint: PHASE 1.4

```bash
rewind init                          # creates ~/.rewind/ca.key, ca.pem, db.duckdb
ls ~/.rewind/                        # ca.key, ca.pem, db.duckdb, blobs/
stat ~/.rewind/ca.key                # permissions: 600
rewind record echo "hello"           # runs, shows session summary (0 LLM steps — that's fine)
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit/ -v
```

All pass → mark PHASE 1.4 done. Clear context. Move to PHASE 1.5.

---

## PHASE 1.5 — CLI: rewind list + rewind inspect

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md.

Current phase: PHASE 1.5 — CLI: rewind list + rewind inspect
All of Phase 1.1–1.4 is complete: scaffold, storage, normalize, RecordAddon, rewind init, rewind record.

Implement:

1. `rewind list` command
   - Lists last 20 sessions (configurable with --limit N)
   - Rich table: ID (first 8 chars), agent_name, started_at, steps count, total_cost_usd, duration
   - --json flag: outputs JSON array instead of table

2. `rewind inspect <session-id>` command
   - Accepts full or partial session ID (prefix match)
   - Shows session metadata at top
   - Shows step-by-step table: order_idx, type, provider, model, input_tok, output_tok, latency_ms
   - --verbose flag: shows match_key (first 8 chars) for each step
   - Redacts auth headers everywhere in output (never show Authorization or x-api-key values)

3. tests/unit/test_display.py
   - Test that display functions don't crash with empty data
   - Test that inspect output does not contain "Authorization" or "x-api-key" strings
```

### Checkpoint: PHASE 1.5

```bash
rewind list              # shows table (may be empty or have test sessions)
rewind inspect --help    # shows usage
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit/ -v
```

All pass → PHASE 1 GATE.

---

## PHASE 1.6 — Phase 1 Gate

### Run all of these. Every single one must pass.

```bash
# Quality
ruff check src/ tests/
ruff format src/ tests/ --check
mypy src/ --strict

# Tests
pytest tests/unit/ -v

# Functional smoke test
rewind init                               # if already initialized, should say so gracefully
rewind record python -c "print('hello')"  # session created, 0 LLM steps, no errors
rewind list                               # shows the session
rewind inspect <session-id-from-above>    # shows session details

# Security check
cat ~/.rewind/ca.key | python -c "import sys; data=sys.stdin.read(); assert 'PRIVATE KEY' in data"
# Verify CA key is NOT in any log output above (check manually)
```

All pass → mark PHASE 1 done. Commit everything.

### Git commit before Phase 2

```bash
git init                    # if not already a git repo
git add .
git commit -m "feat: Phase 1 complete — scaffold, storage, RecordAddon, CLI init/record/list/inspect"
```

---

## PHASE 2.1 — match_key Normalization (complete + tested)

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/adr/ADR-004-match-key-normalization.md.

Current phase: PHASE 2.1 — Complete match_key normalization with edge case tests
Phase 1 is complete and committed.

normalize.py exists but may have gaps. Review and complete it with full edge case coverage:

1. Verify compute_match_key handles:
   - Messages with no tool_calls (normal messages)
   - Messages with tool_calls (assistant turn) — strip all tool_call.id values
   - Messages with role=tool (tool result turn) — strip tool_call_id field
   - Empty messages list
   - No tools field (tools=None vs tools=[])
   - Anthropic system prompt as top-level field (not in messages)
   - Tools with same functions in different order → same hash
   - temperature=None vs temperature not present → same handling

2. Expand tests/unit/test_normalize.py with parametrized test cases:
   - 10+ cases covering all above scenarios
   - Verify: two requests differing ONLY in tool_call_id produce same match_key
   - Verify: two requests with different messages produce different match_key
   - Verify: tools in different order produce same match_key
   - Verify: strip_sensitive_headers removes all listed headers, preserves content-type
```

### Checkpoint: PHASE 2.1

```bash
pytest tests/unit/test_normalize.py -v    # 10+ tests, all pass
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 2.1 done. Clear context. Move to PHASE 2.2.

---

## PHASE 2.2 — ReplayAddon + CassetteMissError

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/ARCHITECTURE.md.

Current phase: PHASE 2.2 — ReplayAddon + CassetteMissError
Phase 1 complete. normalize.py fully tested.

Implement src/rewind/proxy/replay.py — ReplayAddon (mitmproxy addon):

1. Constructor: takes session_id, db: RewindDB, blob_store: BlobStore, mode: Literal["strict","permissive"]
2. Maintains internal index: list[Step] loaded from DB, pointer per match_key for sequence matching
3. request(flow) hook:
   - Check if host is in PROVIDER_HOSTS — ignore all other traffic
   - Compute match_key from request body
   - Look up matching step: find step where step.match_key == match_key, use next in sequence if multiple
   - If found: load resp_blob, set flow.response with stored bytes, mark flow as "killed" (no upstream)
   - If not found + mode==strict: raise CassetteMissError with message including match_key[:8]
   - If not found + mode==permissive: log warning "REWIND: cassette miss for {key[:8]}..., passing through (costs real tokens)", allow through
4. Sequence matching: same match_key appearing twice in recording → serve in recorded order (not random)
5. Must never call upstream LLM when in strict mode and cassette exists

Integration test (tests/integration/test_replay.py):
- Record a fake "session" by manually inserting steps into DB with known match_keys and blob content
- Start ReplayAddon, send matching HTTP request via httpx through mitmproxy
- Assert: response matches stored blob exactly
- Assert: cassette miss in strict mode raises CassetteMissError (or returns 500 with that message)
- Use real mitmproxy but fake cassette data — no real LLM calls
```

### Checkpoint: PHASE 2.2

```bash
pytest tests/unit/ tests/integration/test_replay.py -v
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 2.2 done. Clear context. Move to PHASE 2.3.

---

## PHASE 2.3 — SSE Streaming Replay

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/ARCHITECTURE.md (SSE Streaming section).

Current phase: PHASE 2.3 — SSE Streaming Replay
ReplayAddon is implemented. Non-streaming replay works.

SSE streaming (used by OpenAI and Anthropic) needs special handling.

Implement:

1. In src/rewind/proxy/record.py — SSE recording:
   - Detect streaming response: Content-Type contains "text/event-stream"
   - Accumulate SSE chunks as list: [{"data": raw_line_bytes, "delay_ms": int}, ...]
   - Store complete chunk list as JSON blob (not raw HTTP bytes)
   - Add is_streaming=True to Step

2. In src/rewind/proxy/replay.py — SSE replay:
   - Detect is_streaming=True for this step
   - Load chunk list from blob
   - Construct mitmproxy response with Content-Type: text/event-stream
   - Stream chunks back in sequence
   - Respect delay_ms if REWIND_REPLAY_TIMING=true env var (default: instant)
   - Must include terminating "data: [DONE]\n\n" chunk

3. tests/unit/test_streaming.py:
   - Test SSE chunk serialization/deserialization roundtrip
   - Test that [DONE] chunk is always last
   - Test instant replay (no timing) vs timed replay
   - Test chunk reassembly: multiple chunks → correct sequence
```

### Checkpoint: PHASE 2.3

```bash
pytest tests/unit/test_streaming.py -v
pytest tests/integration/ -v
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 2.3 done. Clear context. Move to PHASE 2.4.

---

## PHASE 2.4 — CLI: rewind replay + rewind diff

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md.

Current phase: PHASE 2.4 — CLI: rewind replay + rewind diff
ReplayAddon complete. Streaming replay works.

Implement:

1. `rewind replay <session-id>` command
   - Accepts full or partial session ID
   - Loads session from DB
   - Starts mitmproxy with ReplayAddon in background
   - Re-runs the ORIGINAL command (stored in session metadata) with proxy env vars
   - Streams original command output to terminal
   - On completion: shows replay summary — steps matched, steps missed, any divergence from original
   - --permissive flag: passes PERMISSIVE mode to ReplayAddon
   - --command "custom cmd" flag: override the stored command

2. `rewind diff <session-id-a> <session-id-b>` command
   - Loads both sessions from DB
   - Walks steps in parallel by order_idx
   - For each step pair: compares resp_blob hash
   - Outputs rich diff table: step N, type, model_a vs model_b, MATCH/DIFFER
   - For differing steps: shows text diff of response content (truncated at 500 chars)
   - Summary line: "X steps match, Y steps differ. First divergence: step N"

3. Store original command in sessions table (add column: command TEXT)
   — this requires a schema migration note in db.py

4. tests/unit/test_diff.py:
   - Test diff logic with two sessions having identical steps → all MATCH
   - Test diff logic with sessions diverging at step 3 → correctly identifies step 3
```

### Checkpoint: PHASE 2.4

```bash
pytest tests/unit/ tests/integration/ -v
rewind replay --help
rewind diff --help
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 2.4 done. Clear context. Move to PHASE 2.5.

---

## PHASE 2.5 — @rewind.tool Decorator

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/ARCHITECTURE.md (Mode B — SDK Decorator section).

Current phase: PHASE 2.5 — @rewind.tool decorator
All proxy replay is working.

Implement src/rewind/sdk/decorator.py:

1. @rewind.tool decorator:
   - Wraps a Python function (sync or async)
   - In RECORD mode: calls real function, stores input_hash → output blob in current session
   - In REPLAY mode: computes input_hash, looks up blob, returns recorded output WITHOUT calling real function
   - input_hash = sha256(json.dumps(args + kwargs, sort_keys=True, default=str))
   - If in REPLAY mode and no recorded output exists: raise CassetteMissError
   - Works with type-annotated functions (preserves signature for mypy)

2. rewind.current_session() context var:
   - Returns current Session or None
   - Set by @rewind.session decorator (do that too)

3. @rewind.session decorator:
   - Wraps an async function
   - Creates new Session, sets current_session context var
   - In RECORD mode: lets function run, saves all @rewind.tool calls
   - In REPLAY mode: starts ReplayAddon in background, replays all recorded tool calls
   - Uses contextvars.ContextVar for session propagation (thread-safe)

4. tests/unit/test_decorator.py:
   - Test @rewind.tool in RECORD mode: function called, output stored
   - Test @rewind.tool in REPLAY mode: function NOT called, stored output returned
   - Test @rewind.tool in REPLAY mode, missing cassette: CassetteMissError raised
   - Test with both sync and async decorated functions
   - Test input_hash stability: same args → same hash
```

### Checkpoint: PHASE 2.5

```bash
pytest tests/unit/test_decorator.py -v
pytest tests/unit/ -v
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 2.5 done. Clear context. Move to PHASE 2.6.

---

## PHASE 2.6 — pytest-rewind Plugin

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/testing/STRATEGY.md.

Current phase: PHASE 2.6 — pytest-rewind plugin
All core recording and replay is working.

Create pytest_rewind/ as a separate installable package:

1. pytest_rewind/plugin.py:
   - @pytest.mark.rewind(cassette="path/to/cassette.rw") marker
   - Before test: load cassette, start ReplayAddon, set REWIND_MODE=replay
   - After test: stop proxy, verify all cassette steps were consumed (no unused steps)
   - If cassette doesn't exist and REWIND_RECORD_MODE=record: record it, save to cassette path
   - If cassette doesn't exist and not in record mode: fail with clear error message

2. Cassette file format (.rw):
   - JSON file containing: session metadata + all steps + all blob content (base64 encoded)
   - Self-contained: one file = full replay capability, no external blob store needed
   - `rewind export <session-id> --output cassette.rw` produces this format
   - `rewind import cassette.rw` loads into local DB + blob store

3. pytest_rewind/conftest.py:
   - rewind_mode fixture: returns current REWIND_MODE ("record" or "replay")
   - cassette_dir fixture: returns configured cassette directory

4. pyproject.toml entry point:
   [project.entry-points."pytest11"]
   rewind = "pytest_rewind.plugin"

5. tests/unit/test_plugin.py:
   - Test cassette file format: export and import roundtrip
   - Test that all blobs are correctly embedded in .rw file

Usage example to include in docstring:
```python
@pytest.mark.rewind(cassette="tests/cassettes/customer_support.rw")
async def test_agent_handles_refund_request():
    result = await run_customer_support_agent("I want a refund")
    assert "refund" in result.lower()
```
```

### Checkpoint: PHASE 2.6

```bash
pip install -e ".[dev]"             # picks up pytest_rewind entry point
pytest --co -q                      # shows rewind marker available
pytest tests/unit/test_plugin.py -v
ruff check src/ tests/ pytest_rewind/
mypy src/ pytest_rewind/ --strict
```

All pass → PHASE 2 GATE.

---

## PHASE 2.7 — Phase 2 Gate + Real Cassette Recording

### Record your first real cassette (requires real API key)

```bash
# Set your real API key temporarily
export ANTHROPIC_API_KEY=your_key_here   # or OPENAI_API_KEY

# Create a simple test agent file
cat > tests/agents/simple_agent.py << 'EOF'
import anthropic

client = anthropic.Anthropic()

def run():
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": "Say exactly: hello from rewind test"}]
    )
    print(response.content[0].text)

if __name__ == "__main__":
    run()
EOF

# Record it
rewind init    # if not done
rewind record python tests/agents/simple_agent.py

# Get the session ID from output
rewind list    # copy the session ID

# Replay it (zero API cost)
rewind replay <session-id>   # should print same response

# Export as cassette
rewind export <session-id> --output tests/cassettes/simple_agent.rw

# Verify cassette works
REWIND_MODE=replay pytest tests/integration/test_real_cassette.py -v  # write this test
```

### Write the cassette-based integration test

Ask Claude (new session):
```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/testing/STRATEGY.md.

I have a committed cassette at tests/cassettes/simple_agent.rw
It contains a recording of a call to claude-haiku-4-5-20251001 returning "hello from rewind test".

Write tests/integration/test_real_cassette.py:
- Uses @pytest.mark.rewind(cassette="tests/cassettes/simple_agent.rw")
- Runs the simple_agent via rewind replay
- Asserts output contains "hello from rewind test"
- Test must pass with NO API key set (pure cassette replay)
- Test must fail if cassette is deleted

Also verify: after the test runs, `cat tests/cassettes/simple_agent.rw | python -m json.tool | grep -i authorization` returns nothing (auth header not stored).
```

### Final Phase 2 Gate Commands

```bash
# No API key — all tests must still pass
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY

pytest -v                                    # all tests pass
pytest tests/integration/ -v                 # cassette replay works
ruff check src/ tests/ pytest_rewind/
ruff format src/ tests/ pytest_rewind/ --check
mypy src/ pytest_rewind/ --strict

# Functional
rewind record python tests/agents/simple_agent.py   # needs API key, skip if needed
rewind list
rewind replay <id>
rewind diff <id-1> <id-2>    # if you have two sessions
```

All pass → commit Phase 2.

```bash
git add .
git commit -m "feat: Phase 2 complete — ReplayAddon, SSE streaming, diff, @rewind.tool, pytest-rewind plugin"
```

---

## PHASE 3.1 — Bisection Engine

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/ARCHITECTURE.md (Bisection Engine section).

Current phase: PHASE 3.1 — Bisection Engine
Phases 1 and 2 complete and committed.

Implement src/rewind/engines/bisect.py:

BisectionResult dataclass:
  - diverges: bool
  - first_divergent_step: int | None
  - step_a: Step | None
  - step_b: Step | None
  - likely_cause: str  (e.g., "model version changed: gpt-4o → gpt-4o-2026-05", "response content differs", "step count differs")
  - response_diff: str  (text diff of the two response blobs, truncated at 1000 chars)

bisect(session_a_id: str, session_b_id: str, db: RewindDB, blobs: BlobStore) -> BisectionResult:
  1. Load steps for both sessions ordered by order_idx
  2. If step counts differ: note it, compare up to min(len_a, len_b)
  3. Walk steps in parallel
  4. For each step pair: compare resp_blob hash
  5. If hashes differ: load both blobs, compute text diff, check if model field differs
  6. Return BisectionResult for first divergence

tests/unit/test_bisect.py:
  - Two identical sessions → diverges=False
  - Sessions diverging at step 1 → first_divergent_step=0
  - Sessions diverging at step 3 of 5 → first_divergent_step=2
  - Session A has 3 steps, session B has 5 steps → diverges=True, noted in likely_cause
  - Model changed between sessions → likely_cause mentions model names
```

### Checkpoint: PHASE 3.1

```bash
pytest tests/unit/test_bisect.py -v
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 3.1 done. Clear context. Move to PHASE 3.2.

---

## PHASE 3.2 — CLI: rewind bisect

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md.

Current phase: PHASE 3.2 — CLI: rewind bisect
Bisection engine (bisect.py) is complete and tested.

Implement `rewind bisect <session-id-a> <session-id-b>` CLI command:

- Accepts full or partial session IDs (use existing prefix-match logic from inspect command)
- Calls bisect() from engines/bisect.py
- Rich output:
  - If no divergence: green "Sessions are identical across all N steps"
  - If divergence: red panel showing:
    - "First divergence: Step N (type: llm_call)"
    - Side-by-side: session A model vs session B model
    - "Likely cause: {likely_cause}"
    - Truncated diff of responses (rich Syntax block, diff format)
  - Summary: "Steps matched: X / Total: Y"

Design the output so a screenshot of it would go viral on Twitter.
It should look like a debugging revelation — clean, clear, dramatic.
```

### Checkpoint: PHASE 3.2

```bash
rewind bisect --help
# Create two test sessions with your test cassettes and run:
rewind bisect <session-a> <session-b>
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit/ tests/integration/ -v
```

All pass → mark PHASE 3.2 done. Clear context. Move to PHASE 3.3.

---

## PHASE 3.3 — Export / Import Cassettes

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md.

Current phase: PHASE 3.3 — Export / Import Cassettes
Bisection is done.

Implement:

1. `rewind export <session-id> [--output path.rw]`
   - Loads session + steps from DB
   - Loads all referenced blobs (req_blob + resp_blob for each step)
   - Writes single .rw JSON file:
     {
       "rewind_version": "1.0",
       "session": {session fields},
       "steps": [{step fields}],
       "blobs": {"<hash>": "<base64-encoded-zstd-compressed-content>"}
     }
   - Default output: ./<session_id[:8]>.rw
   - Verifies: no Authorization or x-api-key strings appear in exported content (security check)

2. `rewind import <path.rw>`
   - Reads .rw file
   - Verifies blob hashes (sha256 of decoded content must match key)
   - Writes blobs to local blob store
   - Writes session + steps to local DB
   - Prints imported session ID and step count

3. tests/unit/test_cassette_format.py:
   - Export → import roundtrip: all steps and blobs preserved, hashes verified
   - Security: exported file does not contain "Authorization" string
   - Tampered blob: import raises BlobTamperedError
```

### Checkpoint: PHASE 3.3

```bash
pytest tests/unit/test_cassette_format.py -v
rewind export --help
rewind import --help
# If you have a real session:
rewind export <session-id>
rewind import <session-id[:8]>.rw
ruff check src/ tests/
mypy src/ --strict
```

All pass → mark PHASE 3.3 done. Clear context. Move to PHASE 3.4.

---

## PHASE 3.4 — rewind stats

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md.

Current phase: PHASE 3.4 — rewind stats (cost analytics)
Export/import done.

Implement `rewind stats [--days N]` command:
- Default: last 7 days. --days 30 for monthly view.
- Rich output with two panels:

Panel 1 — Summary:
  - Total sessions: N
  - Total LLM steps: N
  - Total tokens: N input / N output
  - Total estimated cost: $X.XX
  - Most expensive agent: {name} ($X.XX)
  - Most common model: {model}

Panel 2 — Per-agent breakdown table:
  Columns: agent_name | sessions | steps | input_tok | output_tok | cost_usd | avg_latency_ms

Cost calculation (hardcode in constants.py, easy to update):
  - claude-haiku-4-5: $0.00025/1K input, $0.00125/1K output
  - claude-sonnet-4-6: $0.003/1K input, $0.015/1K output
  - gpt-4o: $0.0025/1K input, $0.01/1K output
  - gpt-4o-mini: $0.00015/1K input, $0.0006/1K output
  Label all cost figures as "estimated" in output.

No new tests needed — this is a display command over existing data.
Verify it doesn't crash on empty DB (new install).
```

### Checkpoint: PHASE 3.4

```bash
rewind stats
rewind stats --days 30
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit/ tests/integration/ -v
```

All pass → mark PHASE 3.4 done. Clear context. Move to PHASE 3.5.

---

## PHASE 3.5 — Phase 3 Gate (Full E2E Test)

### The viral demo — make this work perfectly

```bash
# Set API key (needed just this once to record)
export ANTHROPIC_API_KEY=your_key

# Full E2E flow:
rewind init
rewind record python tests/agents/simple_agent.py     # Step 1: record
SESSION=$(rewind list --json | python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

rewind replay $SESSION                                 # Step 2: replay (zero cost)
rewind diff $SESSION $SESSION                          # Step 3: diff with itself = no divergence
rewind export $SESSION --output demo.rw               # Step 4: export
rewind import demo.rw                                 # Step 5: import

# Now test the whole test suite with no API key
unset ANTHROPIC_API_KEY
pytest -v                                             # must all pass
```

### Final gate commands

```bash
# Quality
ruff check src/ tests/ pytest_rewind/
ruff format src/ tests/ pytest_rewind/ --check
mypy src/ pytest_rewind/ --strict

# Tests (no API key)
pytest -v

# Security
grep -r "PRIVATE KEY" ~/.rewind/    # only ca.key should have it
grep -r "Authorization" tests/cassettes/   # should return nothing

# CLI smoke test
rewind --help
rewind init --help
rewind record --help
rewind replay --help
rewind diff --help
rewind bisect --help
rewind export --help
rewind import --help
rewind stats --help
rewind list --help
rewind inspect --help
```

All pass → commit Phase 3.

```bash
git add .
git commit -m "feat: Phase 3 complete — bisect, export/import, stats — full E2E working"
```

---

## PHASE 3.6 — Launch Prep

### Starter Prompt

```
I'm building Rewind — AI agent record & replay debugger.
Read CLAUDE.md and docs/ARCHITECTURE.md.

Current phase: PHASE 3.6 — Launch preparation
All features complete. E2E working.

Create:

1. README.md — the GitHub README that will go viral:
   - Hero section: one-sentence value prop + the bisect demo (as ASCII or code block)
   - Problem section: "AI agents fail in production. You can't reproduce it." (3 sentences max)
   - Solution section: the 4-command demo showing record → replay → bisect
   - Installation: `pip install rewind-ai`
   - Quick start: 3 steps to first cassette
   - How it works: 2-paragraph architecture summary (link to docs/ARCHITECTURE.md)
   - Comparison table: Rewind vs LangSmith vs Braintrust vs Laminar (what we do they don't: true replay)
   - Contributing section
   - License: MIT

2. .github/workflows/tests.yml:
   - Triggers: push, pull_request
   - Python 3.11, 3.12
   - Steps: pip install -e ".[dev]", ruff check, mypy --strict, pytest -v
   - No API keys in CI (tests use cassettes only)

3. pyproject.toml updates:
   - description: "Time-travel debugger for AI agents. Record any production run, replay any failure."
   - keywords: ["llm", "ai-agents", "debugging", "observability", "replay", "mitmproxy"]
   - classifiers for PyPI
   - project.urls: Homepage, Repository, Documentation, Bug Tracker

The README hero section should be so clear and dramatic that a developer immediately understands the value.
Include the exact `rewind bisect` output (as ASCII art / code block) as the hero image equivalent.
```

### Checkpoint: PHASE 3.6

```bash
# Verify README renders correctly
# Open README.md in a markdown viewer or GitHub preview

# Verify CI workflow syntax
cat .github/workflows/tests.yml

# Final publish check
pip install build twine
python -m build
twine check dist/*       # must pass before PyPI publish
```

All pass → ready to publish and launch.

---

## When Things Go Wrong

### Claude gives wrong code
```
The code you wrote for [file] has [specific issue].
The error is: [paste exact error].
Read [relevant file] before fixing. Minimal change only.
```

### Context gets too long / Claude seems confused
Stop. Clear context. Start fresh session with the phase starter prompt. Do not try to salvage a confused session.

### Test fails and you don't know why
```
Test [test_name] is failing with:
[paste full error output]

Read [relevant source file] and [relevant test file].
Do not change the test to make it pass. Find the root cause in the implementation.
```

### mitmproxy cert issues
```bash
# Regenerate CA (deletes existing)
rm ~/.rewind/ca.key ~/.rewind/ca.pem
rewind init
# Re-set env vars and retry
```

### DuckDB schema changed and old DB breaks
```bash
# Nuclear option: delete local DB (you lose session history, cassettes survive)
rm ~/.rewind/db.duckdb
rewind init    # recreates with new schema
```

### Stuck on Phase 2+ for more than 2 sessions
Post the failing test + error to a new session with full context:
```
I'm building Rewind. I've been stuck on [phase]. Here is the test that's failing:
[paste test]
Here is the error:
[paste error]
Here is the relevant source file content:
[paste file]
What is wrong? Minimal fix only.
```

---

## Context Management Rules

| Situation | Action |
|-----------|--------|
| Checkpoint passed | Clear context. Start fresh. |
| Mid-task, session going long (>45 min) | Finish current sub-task, hit checkpoint, clear |
| Claude seems confused or starts hallucinating | Stop immediately. Clear. Restart with phase prompt. |
| Debugging a specific error | Keep context until error is resolved. Then checkpoint and clear. |
| Switching to completely different module | Clear context. New phase prompt. |
| Asking a quick question | Don't clear. Ask inline. |

---

## Quick Reference: All Commands

```bash
rewind init                                    # one-time setup
rewind record python agent.py                  # record a run
rewind record --name "my_agent" python agent.py
rewind list                                    # list sessions
rewind list --limit 50 --json
rewind inspect <session-id>                    # inspect session
rewind inspect <session-id> --verbose
rewind replay <session-id>                     # replay (zero cost)
rewind replay <session-id> --permissive        # allow cassette miss
rewind replay <session-id> --command "python other.py"
rewind diff <session-a> <session-b>            # diff two runs
rewind bisect <session-a> <session-b>          # find divergence
rewind export <session-id>                     # export as .rw cassette
rewind export <session-id> --output my.rw
rewind import my.rw                            # import cassette
rewind stats                                   # cost analytics
rewind stats --days 30
```

---

## What "Done" Looks Like

You can demo this in 60 seconds:
1. `rewind record python agent.py` — records a production failure
2. `rewind replay <id>` — reproduces it locally, zero cost
3. `rewind bisect <good-id> <bad-id>` — points to exact failing step
4. `pytest --rewind-cassette tests/cassettes/agent.rw` — passes in CI, no API key

That's the product. Ship it.
