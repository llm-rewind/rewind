# Rewind — Time-Travel Debugger for AI Agents

> Record any production run. Replay any failure. Find the exact step that broke.

```
$ rewind bisect run-good-7f3a run-bad-9b2c

  ✗ First divergence at step 4 (llm_call)
  ─────────────────────────────────────────────────────────────
  Model:    claude-sonnet-4-6 (both runs)
  Provider: anthropic

  Step 1   match  ✓  "Understood, I'll look into the account."
  Step 2   match  ✓  [tool_call: lookup_account → {status: active}]
  Step 3   match  ✓  "The account shows a balance of $1,240."
  Step 4  DIVERGED ✗
    Good:  "I'll proceed with the transfer."
    Bad:   "I cannot complete this action without explicit confirmation."

  Likely cause: prompt drift between runs (system prompt changed 09:14 UTC)
  Steps matched: 3 / 12
```

[![CI](https://github.com/llm-rewind/rewind/actions/workflows/tests.yml/badge.svg)](https://github.com/llm-rewind/rewind/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/llm-rewind)](https://pypi.org/project/llm-rewind/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The Problem

AI agents fail in production. You can't reproduce it.

The model call that worked in staging silently uses a different system prompt. A tool returns slightly different output under load. A model version rolls out at midnight and three days later your agent starts refusing requests it used to handle. You have logs, but logs show *what* happened — not *why* it changed.

Rewind gives you the recording.

---

## The Solution — 4 Commands

```bash
# 1. Record any agent run (works with any language or framework)
rewind record python my_agent.py --input '{"query": "process refund #8821"}'

# 2. Replay it locally — zero LLM API cost, deterministic output
rewind replay 7f3a2b

# 3. Find exactly where a bad run diverged from a good one
rewind bisect run-good-7f3a run-bad-9b2c

# 4. Export a cassette to share with your team
rewind export 7f3a2b --output incident-8821.rw
```

---

## Install

```bash
pip install llm-rewind
rewind init      # generates local CA cert for HTTPS interception
```

That's it. No account, no cloud, no API key for Rewind itself.

---

## Quick Start — First Cassette in 3 Steps

**Step 1: Record**
```bash
export ANTHROPIC_API_KEY=sk-...   # your existing key
rewind record python my_agent.py
# → Session recorded: 7f3a2b9c  (12 LLM calls, 3,241 tokens)
```

**Step 2: Replay** (no API key needed)
```bash
rewind replay 7f3a2b
# → Replaying 12 steps from cassette... done. Output identical.
```

**Step 3: Inspect**
```bash
rewind inspect 7f3a2b
# → Rich table: step-by-step model calls, token counts, latency
```

---

## How It Works

Rewind runs as a local HTTPS proxy (via [mitmproxy](https://mitmproxy.org)) that intercepts every LLM API call your agent makes — to OpenAI, Anthropic, or Gemini. Each request and response is stored in a content-addressed blob store (SHA-256, zstd-compressed) with DuckDB metadata. Because the proxy operates at the HTTP layer, **Rewind works with any language and any framework** — Python, Node.js, Go, LangChain, LlamaIndex, raw SDK calls, all of it.

On replay, Rewind starts the same proxy in replay mode. Incoming requests are matched by a canonical fingerprint (`match_key`) that strips volatile fields like `tool_call_id` while preserving semantic content. Matched requests get the exact recorded response bytes — SSE streaming preserved, token counts preserved, latency simulated. No LLM calls go out. The bisect engine walks two session recordings in parallel and identifies the first step where responses diverge, giving you a precise root cause instead of a log diff.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

---

## SDK Decorator (Python shortcut)

Don't want to configure a proxy? Use the decorator mode for pure-Python agents:

```python
import rewind

@rewind.session(name="customer_support", mode="record")
async def run_agent(query: str) -> str:
    ...

@rewind.tool  # records non-HTTP tool calls too
def search_database(query: str) -> list[dict]:
    ...
```

---

## Comparison

| Feature | Rewind | LangSmith | Braintrust | Laminar |
|---------|--------|-----------|------------|---------|
| True deterministic replay | ✅ | ❌ | ❌ | ❌ |
| Works with any language | ✅ | ❌ Python/JS | ❌ Python/JS | ❌ Python |
| Local / private (no cloud) | ✅ | ❌ cloud | ❌ cloud | ❌ cloud |
| Zero-cost replay | ✅ | ❌ | ❌ | ❌ |
| Bisect to find divergence | ✅ | ❌ | ❌ | ❌ |
| Shareable cassette files | ✅ `.rw` | ✅ datasets | ✅ datasets | ✅ datasets |
| Cost analytics | ✅ | ✅ | ✅ | ✅ |
| Open source | ✅ MIT | ✅ MIT | ✅ MIT | ✅ Apache |

**The key difference:** LangSmith, Braintrust, and Laminar are *observability* tools — they show you what happened. Rewind is a *debugger* — it lets you reproduce and isolate failures deterministically.

---

## Supported Providers

| Provider | Recording | Streaming SSE |
|----------|-----------|---------------|
| Anthropic (`api.anthropic.com`) | ✅ | ✅ |
| OpenAI (`api.openai.com`) | ✅ | ✅ |
| Google Gemini | ✅ | ✅ |

---

## pytest Integration

```python
# Install: pip install llm-rewind

@pytest.mark.rewind(cassette="tests/cassettes/customer_support.rw")
async def test_agent_handles_refund_request():
    result = await run_customer_support_agent("I want a refund")
    assert "refund" in result.lower()
```

Cassettes are committed to git. Tests run with zero API cost in CI. See [docs/testing/STRATEGY.md](docs/testing/STRATEGY.md).

---

## CLI Reference

```bash
rewind init                               # generate local CA cert
rewind record <command>                   # record an agent run
rewind replay <session-id>               # replay from cassette
rewind list                              # list recorded sessions
rewind inspect <session-id>              # inspect step details
rewind diff <session-a> <session-b>      # compare two sessions
rewind bisect <good-run> <bad-run>       # find first divergence
rewind export <session-id> [--output f.rw]   # export cassette file
rewind import <cassette.rw>              # import cassette to local DB
rewind stats [--days 30]                 # cost analytics
```

---

## Contributing

```bash
git clone https://github.com/llm-rewind/rewind
cd rewind
pip install -e ".[dev]"
pytest                  # all tests use cassettes — no API key needed
ruff check src/ tests/
mypy src/ --strict
```

Issues and PRs welcome. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design decisions and ADRs.

---

## License

MIT — see [LICENSE](LICENSE).

Built because production AI agent debugging was broken and no one had fixed it yet.
