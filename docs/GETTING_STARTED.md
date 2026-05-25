# Getting Started with Rewind

Time required: 10 minutes. You need Python 3.11 or newer and a terminal.
No prior knowledge of mitmproxy, DuckDB, or LLM internals is needed.

By the end of this guide you will have:

1. Installed Rewind from PyPI
2. Recorded a real LLM call against Google Gemini's free tier
3. Replayed it with zero API cost
4. Run mutation testing and watched it find fragility in the demo agent
5. Compared two runs with `rewind bisect` and seen cause inference work

---

## 0. Prerequisites

Check your Python:

```bash
python --version
```

You need 3.11, 3.12, or 3.13. If you have an older version, install one
of those before continuing.

You also need a free Google Gemini API key for the recording step
(replay and mutation work afterwards with no key). Get a key here (no
credit card required, free tier):

  https://aistudio.google.com/apikey

Click "Create API key", copy the value (starts with `AIza`), keep it
handy.

---

## 1. Install

```bash
pip install --upgrade llm-rewind
```

That installs the `rewind` CLI and the `pytest-rewind` plugin in one
package. Verify:

```bash
rewind --version
# should print: python -m rewind, version 0.2.1 (or higher)
```

---

## 2. Initialise (one time per machine)

```bash
rewind init
```

This generates a local certificate authority at `~/.rewind/ca.pem` that
lets Rewind intercept HTTPS traffic. The CA private key is stored
owner-only.

The next step depends on your OS. `rewind init` prints the exact
command for you; here are the three to expect:

**macOS:**

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ~/.rewind/ca.pem
```

**Linux (Debian/Ubuntu):**

```bash
sudo cp ~/.rewind/ca.pem /usr/local/share/ca-certificates/rewind-ca.crt
sudo update-ca-certificates
```

**Windows (Administrator command prompt):**

```cmd
certutil -addstore -f "ROOT" %USERPROFILE%\.rewind\ca.pem
```

You only do this once.

---

## 3. Test 1: Record a Real LLM Call

Save your Gemini key in the environment:

```bash
# bash / zsh
export GEMINI_API_KEY=AIza...your_key_here

# Windows PowerShell
$env:GEMINI_API_KEY = "AIza...your_key_here"

# Windows cmd
set GEMINI_API_KEY=AIza...your_key_here
```

The Rewind repository ships a tiny demo agent that calls Gemini once.
Clone the repo so you have it locally:

```bash
git clone https://github.com/llm-rewind/rewind
cd rewind
```

Now record:

```bash
rewind record --port 18080 --name demo \
  py tests/agents/gemini_agent.py
```

Expected output:

```
● Recording <session-id>
  Proxy:   http://127.0.0.1:18080
  Command: py tests/agents/gemini_agent.py

hello from rewind gemini test

✓ Captured 1 LLM call(s) — ~$0.0000 | 1.3s
  rewind inspect <session-id>
```

Copy the session id (the first 8 characters are enough).

**What just happened:** Rewind started a local proxy on port 18080,
launched the demo agent as a subprocess with `HTTPS_PROXY` pointing at
the proxy, intercepted the agent's HTTPS call to Gemini, and stored
the full request and response in `~/.rewind/`.

---

## 4. Test 2: Replay With Zero Cost

```bash
rewind replay <session-id>
```

Expected:

```
▶ Replaying <session-id> (strict mode)
  Cassette: 1 step(s)
  Proxy:    http://127.0.0.1:8080
  Command:  py tests/agents/gemini_agent.py

hello from rewind gemini test

✓ Replay complete — 0.4s (zero LLM cost)
```

The agent ran again, printed the same response, but **no real Gemini
call was made**. You can prove this by unsetting your key first:

```bash
unset GEMINI_API_KEY   # (or `$env:GEMINI_API_KEY = $null` in PowerShell)
rewind replay <session-id>
```

Still works.

---

## 5. Test 3: Mutation Testing

This is the feature no other tool ships. It systematically perturbs
your recorded cassette and re-runs the agent against each variation
to see where it breaks.

```bash
rewind mutate <session-id> \
  --command "py tests/agents/gemini_agent.py" \
  --port 19000
```

Expected output (the demo agent has zero error handling, so every
mutation crashes it; that is the point):

```
Mutation testing session <session-id>
  Command: py tests/agents/gemini_agent.py

Running baseline replay...
Running 5 mutations...

                                Mutation Report
┌───────────────────┬──────┬─────────┬────────────────────────────────────┐
│ Mutation          │ Step │ Outcome │ Detail                             │
├───────────────────┼──────┼─────────┼────────────────────────────────────┤
│ drop_step         │    0 │ CRASHED │ step 0 removed entirely            │
│ empty_response    │    0 │ CRASHED │ step 0 response replaced with empty│
│ truncate_response │    0 │ CRASHED │ step 0 response truncated to half  │
│ error_response    │    0 │ CRASHED │ step 0 returns 429 rate-limit      │
│ provider_500      │    0 │ CRASHED │ step 0 returns 500 server error    │
└───────────────────┴──────┴─────────┴────────────────────────────────────┘

Survived: 0 | Changed: 0 | Crashed: 5 | Total: 5

Crashed mutations indicate fragility. The agent does not gracefully
handle these failure modes.

Cleaned up 5 mutated session(s) (pass --keep-sessions to retain).
```

Read that as: "Your agent crashes on rate limits, server errors,
empty responses, truncated responses, and missing steps." That is
exactly the production-readiness gap report Rewind exists to produce.

---

## 6. Test 4: Compare Two Runs (bisect)

Record a second session:

```bash
export GEMINI_API_KEY=AIza...    # set again
rewind record --port 18080 --name demo2 \
  py tests/agents/gemini_agent.py
```

Get both session ids:

```bash
rewind list
```

Compare them:

```bash
rewind bisect <session-a> <session-b>
```

You will see one of:

- `Sessions <a> and <b> are identical.` — if Gemini returned the
  same response both times (it usually does at temperature 0)
- A `First divergence at step 0` block with `Cause: llm_nondeterminism`
  and an explanatory line — if the provider returned something
  slightly different on the second run

The cause inference is the value: it tells you whether the difference
came from the model, the prompt, a tool, or something deeper.

---

## 7. Cassette Portability

Cassettes are self-contained files you can share with teammates or
commit to git so CI can replay without API keys.

```bash
rewind export <session-id> --output demo.rw
rewind import demo.rw
```

The exported `.rw` file is a single JSON document with every blob
embedded as base64. SHA-256 verified on import.

---

## 8. Quick Health Check (no API key)

After install, you can verify everything works without any keys:

```bash
git clone https://github.com/llm-rewind/rewind
cd rewind
pip install -e ".[dev]"
pytest                                        # 142 tests should pass
ruff check src/ tests/ pytest_rewind/         # clean
mypy src/ pytest_rewind/ --strict             # clean
```

---

## Troubleshooting

**"Captured 0 LLM call(s)"** — your CA cert is not trusted by the
client. Re-run the OS-specific trust step from section 2. On Windows
you must run the `certutil` command as Administrator.

**"timed out" during record** — usually means HTTPS interception is
failing because the CA is missing the SubjectKeyIdentifier extension.
This was a v0.1 bug; it is fixed in v0.2.1. Make sure you are on
v0.2.1 or later (`rewind --version`).

**"Already initialized"** — `rewind init` is idempotent. If you need
to regenerate the CA, delete `~/.rewind/ca.key`, `~/.rewind/ca.pem`,
and `~/.rewind/mitmproxy-ca.pem` first.

**Free Gemini "quota exceeded"** — Google moved `gemini-2.0-flash`
off the free tier in 2026. The demo agent uses `gemini-2.5-flash`
which is still free.

**"`pip install llm-rewind` picks an old version"** — make sure your
pip is recent and run `pip install --upgrade llm-rewind`.

---

## What to Do Next

- Read [README.md](../README.md) for the architecture and feature
  overview.
- Read [docs/ARCHITECTURE.md](ARCHITECTURE.md) if you want to
  understand the internals.
- Try `rewind mutate` against your own agent and see what breaks.
  That is the actual value of the tool.
- File issues at https://github.com/llm-rewind/rewind/issues
