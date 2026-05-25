# Rewind — Time-Travel Debugger for AI Agents

> Record any production run. Bisect to the failing step. Mutation-test
> for the failure modes you have not hit yet.

[![CI](https://github.com/llm-rewind/rewind/actions/workflows/tests.yml/badge.svg)](https://github.com/llm-rewind/rewind/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/llm-rewind)](https://pypi.org/project/llm-rewind/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

```
$ rewind bisect run-good-7f3a run-bad-9b2c

First divergence at step 4
  Session A: run-good  model='gpt-4o-2025-11'
  Session B: run-bad   model='gpt-4o-2026-05'
  Cause:    model_version_changed
  Detail:   model changed: 'gpt-4o-2025-11' -> 'gpt-4o-2026-05'.
            Model upgrades are the highest-likelihood cause
            of behaviour shifts.
```

The point of Rewind is the last two lines. Other tools tell you that
two runs of your agent differ. Rewind tells you why.

---

## What Makes This Different

Cassette-style HTTP replay for LLMs is not new. VCR.py has shipped this
pattern since 2010; vcr-langchain since 2023; Docker cagent shipped a
nearly identical implementation in 2026 and inspired several pieces of
this codebase (see `docs/adr/`).

What Rewind adds on top of that base layer:

**Cause inference.** `rewind bisect` classifies the reason two runs
diverged: was it a model version bump, a tool returning different
output, prompt drift between deploys, or model non-determinism? Every
other tool in the space stops at "step N differs".

**Mutation testing for agents.** `rewind mutate` is Stryker for LLM
agents. It systematically perturbs a recorded cassette: drops steps,
returns 429s, truncates responses, replaces tool outputs with
errors. It re-runs your agent against each mutation and reports which
ones the agent silently fails. Tells you where production drift will
bite before it does.

Everything else (HTTPS MITM via mitmproxy, content-addressed blobs,
SSE streaming preservation, `pytest-rewind`) is table stakes that
existing tools also do. The cause inference and mutation harness are
the part that justifies the project.

---

## Install

```bash
pip install llm-rewind
rewind init
```

`rewind init` generates a local CA cert at `~/.rewind/ca.pem` for
HTTPS interception. On macOS and Linux the trust step is one
command; on Windows it needs Administrator. `rewind init` prints the
exact command for your platform after generating the cert.

**New user? Follow the [10-minute end-to-end walkthrough](docs/GETTING_STARTED.md)** —
clone the repo, record one real Gemini call (free tier), replay it
without an API key, and run mutation testing to see fragility in
the demo agent.

---

## Three Loops

### 1. Reproduce a production failure

```bash
ANTHROPIC_API_KEY=sk-...
rewind record python my_agent.py
# Captured 12 LLM call(s)  ~$0.034  | 8.4s

rewind list
# 7f3a2b9c  my_agent  2026-05-23 14:35:29   12  $0.034  c0e577f

rewind replay 7f3a2b
# Replay complete  8.4s (zero LLM cost)
```

### 2. Find the exact step a regression broke

```bash
rewind bisect run-good-7f3a run-bad-9b2c
# First divergence at step 4
#   Cause:  upstream_tool_output_drifted
#   Detail: previous step (3, tool_call) returned different output.
#           Likely root cause is upstream; bisect that step first.
```

### 3. Pressure-test before shipping

```bash
rewind mutate 7f3a2b9c

# Mutation Report
# +-------------------+------+----------------+----------------------------+
# | Mutation          | Step | Outcome        | Detail                     |
# +===================+======+================+============================+
# | empty_response    | 0    | SURVIVED       | step 0 response empty      |
# | provider_500      | 0    | CRASHED        | step 0 returns 500 error   |
# | error_response    | 4    | OUTPUT CHANGED | step 4 returns 429         |
# | truncate_response | 7    | CRASHED        | step 7 truncated to half   |
# +-------------------+------+----------------+----------------------------+
# Survived: 9 | Changed: 3 | Crashed: 3 | Total: 15
#
# Caveat: the survival oracle is stdout equality. An agent that
# prints the same thing while doing the wrong thing internally is
# marked SURVIVED. Augment your agent's stdout if you need a finer
# oracle.
```

The Crashed row is what you fix before deploying.

---

## How It Works

Rewind runs as a local HTTPS proxy (via [mitmproxy](https://mitmproxy.org))
that intercepts every LLM API call your agent makes — OpenAI,
Anthropic, or Gemini. Each request and response is stored in a
content-addressed blob store (SHA-256, zstd-compressed) with DuckDB
metadata. Because the proxy operates at the HTTP layer, **Rewind works
with any language and any framework**: Python, Node.js, Go, LangChain,
LlamaIndex, raw SDK calls.

On replay, Rewind starts the same proxy in replay mode. Incoming
requests get matched by a canonical fingerprint (`match_key`) that
strips volatile fields like `tool_call_id` and credential query
parameters while preserving semantic content. Matched requests get the
exact recorded response bytes back. Strict mode never falls through to
the live API; a cassette miss returns HTTP 599 with a structured error
body and a clear `X-Rewind-Cassette-Miss` header. No quiet billing.

`docs/ARCHITECTURE.md` has the full design and the ADRs.

---

## Comparison

| Feature                             | Rewind | LangSmith | Braintrust | Laminar | Helicone | vcr-langchain | Docker cagent |
| ----------------------------------- | ------ | --------- | ---------- | ------- | -------- | ------------- | ------------- |
| True deterministic replay           | yes    | no        | no         | no      | yes      | yes           | yes           |
| Cause inference on divergence       | yes    | no        | no         | no      | no       | no            | no            |
| Mutation testing for agents         | yes    | no        | no         | no      | no       | no            | no            |
| Framework-agnostic (HTTP-level)     | yes    | no        | no         | no      | yes      | no            | no            |
| Local-only, no cloud                | yes    | no        | no         | no      | yes      | yes           | yes           |
| Open source                         | MIT    | partial   | partial    | Apache  | MIT      | MIT           | Apache        |

LangSmith, Braintrust, and Laminar are observability platforms — they
show you what happened. vcr-langchain, Helicone, and cagent are
cassette/proxy tools — they let you replay. Rewind is positioned as a
**debugger**: replay plus the two engines (bisect cause inference,
mutation testing) that turn a recording into a diagnosis.

---

## pytest Integration

```python
@pytest.mark.rewind(cassette="tests/cassettes/customer_support.rw")
async def test_agent_handles_refund_request():
    result = await run_customer_support_agent("I want a refund")
    assert "refund" in result.lower()
```

Cassettes get committed to git. CI runs them with zero API cost and no
keys configured. See `docs/testing/STRATEGY.md`.

---

## SDK Decorator (Python convenience)

For pure-Python agents that do not want a proxy setup:

```python
import rewind

@rewind.session(name="customer_support", mode="record")
async def run_agent(query: str) -> str:
    ...

@rewind.tool
def search_database(query: str) -> list[dict]:
    ...
```

The proxy approach is recommended for any non-Python or
multi-language agent.

---

## CLI Reference

```bash
rewind init                                # generate local CA cert
rewind record <command>                    # record an agent run
rewind replay <session-id>                 # replay from cassette
rewind list                                # list recorded sessions
rewind inspect <session-id>                # inspect step details
rewind diff <a> <b>                        # compare two sessions
rewind bisect <good> <bad>                 # find divergence + classify cause
rewind mutate <session-id>                 # mutation test the agent
rewind export <session-id> [--output f.rw] # export cassette file
rewind import <cassette.rw>                # import cassette to local DB
rewind stats [--days 30]                   # cost analytics
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Setup:

```bash
git clone https://github.com/llm-rewind/rewind
cd rewind
pip install -e ".[dev]"
pytest                  # 140 tests, no API key needed
ruff check src/ tests/ pytest_rewind/
mypy src/ pytest_rewind/ --strict
```

The local end-to-end tests stand up an HTTPS server and a real
mitmproxy instance, so they exercise the same code path a user hits.
They run on CI for Python 3.11, 3.12, and 3.13.

---

## License

MIT — see [LICENSE](LICENSE).
