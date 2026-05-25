# I Built Mutation Testing for LLM Agents and It Found Bugs in My Own Code in 8 Seconds

*Draft. Replace bracketed placeholders before publishing. Target length: 1200-1500 words. Recommended outlets: personal blog -> Hacker News -> r/LocalLLaMA -> r/MachineLearning -> Twitter thread linking back.*

---

## TL;DR

I wrote a tool called Rewind that records every HTTP call an AI agent
makes, then **mutates the recording** and replays the agent against
each mutation to find places it silently breaks. I ran it against
[YOUR PICK: a popular OSS agent — LangChain ReAct / CrewAI hierarchical
team / AutoGen group chat] and found N real fragility bugs in M
seconds.

Install: `pip install llm-rewind`
Repo: https://github.com/llm-rewind/rewind
License: MIT

## The Problem

Your agent works in staging. It fails in production. You read the logs.
The logs show one LLM call returned an empty completion and the agent
quietly used `""` instead of crashing, which made the next step pick
the wrong tool, which silently corrupted a row in your database.

Logs show *what* happened. They do not show *what would have happened
if step 3 had returned an HTTP 429 instead*. Or *if the tool result
had been empty*. Or *if the model had returned a truncated response*.

These are the failure modes that actually bite production. Most agent
code has never been exercised against them.

## Prior Art and Why It Isn't Enough

- **LangSmith, Braintrust, Laminar, Phoenix:** observability platforms.
  They show you traces. They do not replay deterministically; their
  "replay" buttons re-call the live model.
- **vcr-langchain, Helicone, Docker cagent:** real cassette-style HTTP
  replay for LLM calls. Good. But they stop at faithful playback.
- **Stryker / mutmut:** mutation testing for code. Great for unit tests.
  Useless against an agent whose behaviour depends on LLM responses.

What is missing: take the *cassette* and mutate *it* instead of the
code. The recording is now the substrate; perturbing it tells you how
the agent behaves under failure modes the original run did not see.

## What I Built

`rewind record python my_agent.py` captures every LLM call your agent
makes via a local HTTPS proxy (mitmproxy under the hood, so it works
with any language and any framework, not just Python).

`rewind replay <session>` replays the agent against the recording with
zero API cost. Fine.

`rewind mutate <session>` is the part that matters. It walks the
cassette and emits five mutations per step:

- **drop_step** — pretend that LLM call never happened (network drop)
- **empty_response** — return zero content (refusal, max_tokens=0)
- **truncate_response** — cut the response in half (max_tokens hit
  mid-stream)
- **error_response** — return HTTP 429 (rate limit)
- **provider_500** — return HTTP 500 (provider outage)

Then it re-runs the agent against each mutated cassette and reports
which mutations the agent survived, which changed its output, and
which crashed the process.

The crashed column is your weekly oncall queue, displayed before any
of those incidents happen.

## I Ran It Against [TARGET AGENT]

[REPLACE THIS SECTION WITH REAL RESULTS — instructions at the bottom.]

I picked [PROJECT NAME] because it is one of the most-starred OSS
agent implementations and its README explicitly says it is
production-ready. I recorded one typical run, then ran
`rewind mutate`:

```
                          Mutation Report
+-------------------+------+----------------+--------------------------+
| Mutation          | Step | Outcome        | Detail                   |
+===================+======+================+==========================+
| drop_step         |    2 | CRASHED        | KeyError on missing tool |
| empty_response    |    4 | OUTPUT CHANGED | agent looped 6 times     |
| truncate_response |    1 | CRASHED        | JSONDecodeError          |
| error_response    |    3 | OUTPUT CHANGED | silent retry, wrong tool |
| provider_500      |    0 | CRASHED        | bare except: re-raised   |
+-------------------+------+----------------+--------------------------+
Survived: 12 | Changed: 5 | Crashed: 8 | Total: 25
```

Eight crashes on a 25-mutation budget. Five behaviour changes the
authors probably did not test for. All of these are real production
failure modes, and the recording took 4 seconds.

[REPLACE WITH ACTUAL RESULTS]

## How `bisect` Goes One Better

When two runs of the same agent diverge — version bump, prompt edit,
flaky tool — you usually want to know *why*, not just *where*. Most
diff tools stop at "step 4 differs". Rewind classifies the cause:

```
$ rewind bisect run-good-7f3a run-bad-9b2c

First divergence at step 4
  Session A: run-good  model='gpt-4o-2025-11'
  Session B: run-bad   model='gpt-4o-2026-05'
  Cause:    model_version_changed
  Detail:   model changed: 'gpt-4o-2025-11' -> 'gpt-4o-2026-05'.
            Model upgrades are the highest-likelihood cause of
            behaviour shifts.
```

The taxonomy: `model_version_changed`, `prompt_drift`,
`tool_list_changed`, `upstream_tool_output_drifted`,
`llm_nondeterminism`, `step_count_differs`, `step_type_changed`. Each
comes with the actual diff and a one-line "what to look at next".

## How It Works (Briefly)

The proxy is `mitmproxy 11` configured with a custom addon. Storage is
DuckDB metadata plus content-addressed blobs (SHA-256, zstd
compression). The replay loop matches incoming requests by a
canonical fingerprint that strips volatile fields (`tool_call_id`,
credential query params like Gemini's `?key=`) before hashing, so
cassettes recorded by one developer replay for any other developer
without re-coding their API keys into the test fixtures.

There is one fix I had to make that surprised me: Python 3.13's
stricter X.509 verifier rejects CA certs without a SubjectKeyIdentifier
extension. mitmproxy issues per-host certs signed by the CA you give
it; without SKI on the trust anchor, those certs are unverifiable by
modern clients. The HTTPS interception silently fails with a
ConnectError. Fix is one line of `cryptography` — there is an
[ADR](https://github.com/llm-rewind/rewind/blob/main/docs/adr/) and
the bug is filed against any prior LLM proxy tool that does not
include SKI in its generated CA.

## Try It

```bash
pip install llm-rewind
rewind init                       # one-time CA generation
rewind record python my_agent.py  # records to ~/.rewind/
rewind mutate <session-id>        # tells you where your agent breaks
```

Full walkthrough: https://github.com/llm-rewind/rewind/blob/main/docs/GETTING_STARTED.md

Pull requests, bug reports, and "I ran this against X and found Y"
posts welcome. The mutation kinds are deliberately conservative; if
your domain needs others (poison tool output, model swap, partial
JSON), add them in `src/rewind/engines/mutate.py` and open a PR.

---

## Notes for the Author Before Publishing

To turn this draft into a real post:

1. **Pick a target agent.** Best candidates by likely-bug yield:
   - `langchain-ai/langgraph` example agents
   - `crewAIInc/crewAI` quickstart agents
   - `microsoft/autogen` group-chat samples
   - Anything from `awesome-agents` with > 1k stars and a quick-start
2. **Record one realistic run.** Pick a task that exercises 5-15 LLM
   steps. A customer-support agent or a code-review agent works well.
3. **Run `rewind mutate <session-id>`** and capture the output.
4. **Pick the most embarrassing 3-5 crashes** and link them to the
   exact source line in the target project (responsible disclosure
   first if the target is a vendor product, not OSS).
5. **Replace the bracketed sections** with the real findings.
6. **Take screenshots** of the mutation report and bisect output for
   the post. Use a terminal with a clean theme; iTerm2 / Windows
   Terminal works fine.
7. **Animated GIF** (60 seconds max) showing `rewind record` ->
   `rewind mutate` -> the report. Tool suggestions: asciinema for
   recording (`asciinema rec demo.cast`), agg to convert to GIF
   (`agg demo.cast demo.gif`). Script for the GIF is in
   `docs/launch/DEMO_SCRIPT.md`.

Posting order:
1. Personal blog or Substack first (controls the canonical URL).
2. Hacker News submission with title: "Mutation testing for LLM
   agents (with bisect cause inference)".
3. Twitter thread: hero GIF + 4 reply tweets summarising each
   mutation kind.
4. `r/LocalLLaMA`, `r/MachineLearning`, `r/Python`.
5. LinkedIn post with the bug-counts headline. This is the post
   recruiters will see.

Top HN comment risk: "this is just VCR.py". The README and this post
both pre-empt that by leading with the two genuinely-new features
(cause inference + mutation testing) and crediting cagent, vcr-
langchain, and pytest-recording as the prior art the project builds
on. Be honest in replies and the discussion stays productive.
