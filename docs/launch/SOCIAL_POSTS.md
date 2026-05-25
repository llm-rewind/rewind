# Launch-Day Post Drafts

One file per channel below. Copy, fill in the bracketed placeholders
after you record real results, post. Order of posting matters: blog
first, HN second, Twitter and the subreddits third, LinkedIn last.

---

## 1. Personal Blog or Substack (post first, owns the canonical URL)

Use `docs/launch/BLOG_POST_DRAFT.md` as the source. Cross-link to:

- README on GitHub
- `docs/GETTING_STARTED.md`
- `docs/launch/SIDE_BY_SIDE_COMPARISON.md`

Publish, then capture the URL. Every other post below links to this.

---

## 2. Hacker News (Show HN)

Title (under 80 chars):

> Show HN: Mutation testing for LLM agents, plus bisect cause inference

URL: link to the blog post (NOT directly to GitHub; blog post owns
the framing).

First comment (post immediately after submission, from the same
account):

```
Author here. Quick context on why this exists and what it is not.

It's a local HTTPS proxy that records every LLM call your agent
makes (any language, any framework; mitmproxy under the hood). The
cassette-replay layer is the same idea VCR.py has shipped since 2010
and that Docker cagent shipped earlier this year — credit where due,
both are linked in the post and the comparison table.

What's new on top of that base layer:

1. `rewind bisect` classifies *why* two runs diverge:
   model_version_changed, prompt_drift, tool_list_changed,
   upstream_tool_output_drifted, llm_nondeterminism. Other tools stop
   at "step N differs"; this turns a diff into a diagnosis.

2. `rewind mutate` is the part I'm most curious to hear feedback on.
   It systematically perturbs the cassette (drops a step, returns
   429/500, truncates a response, replaces a tool result with empty)
   and re-runs the agent against each mutation to find places it
   silently breaks. Stryker for LLM agents.

Known caveats baked into the README so I'm not hiding them:
- Survival oracle is stdout equality; agents that print the same
  thing while doing the wrong thing internally are marked SURVIVED.
- LLM_NONDETERMINISM is a catch-all when none of the other causes
  match; volatile fields like `seed` or `tool_choice` are not in
  the prompt fingerprint yet.

PRs welcome on both. Repo: https://github.com/llm-rewind/rewind
Install: pip install llm-rewind
```

Watch the thread for the first 90 minutes. The submission ranks on
early upvotes and author engagement. Reply to every top-level
comment; brevity beats defensiveness.

---

## 3. Twitter Thread

Thread of 5 tweets. Post the GIF on tweet 1, that is the only thing
the algorithm cares about.

**Tweet 1 (hero):**

> Built `rewind mutate`: it perturbs a recorded LLM agent run
> (drops steps, returns 429s, truncates responses) and replays the
> agent against each mutation to find where it silently breaks.
>
> [GIF: mutation report rendering, 5 crashes on a naive agent]
>
> github.com/llm-rewind/rewind

**Tweet 2:**

> Why this exists: most agent failures are caused by drift the
> original test suite never exercised. The model returns "" instead
> of refusing. A tool times out. The provider returns 429.
>
> Agent silently misbehaves. You find out in production.

**Tweet 3:**

> Sibling command: `rewind bisect` doesn't just tell you two runs
> differ. It classifies the cause:
>
> model_version_changed
> prompt_drift
> tool_list_changed
> upstream_tool_output_drifted
> llm_nondeterminism
>
> [Screenshot of bisect output]

**Tweet 4:**

> Works with any LLM SDK and any language. HTTPS proxy at the wire
> level (mitmproxy). Storage is DuckDB + content-addressed blobs.
> Auth headers and credential query params (Gemini's ?key=) stripped
> before anything hits disk.

**Tweet 5 (CTA):**

> `pip install llm-rewind` and `rewind init`.
>
> Walkthrough: [link to GETTING_STARTED.md]
> Full writeup: [link to blog]
>
> If you try `rewind mutate` on a real agent and find something, I
> want to see the report. Reply with screenshots.

---

## 4. r/LocalLLaMA

Title:

> [Tool] Mutation testing for LLM agents - find where your agent
> silently breaks under production-like failure modes

Body:

```
I've been frustrated that no testing tool for LLM agents tells you
how the agent would behave if step 3 returned an HTTP 429 instead of
the actual response. So I built one.

`rewind record python agent.py` captures every LLM call via a local
HTTPS proxy. `rewind mutate` then perturbs the recording
(drop_step, empty_response, truncate, 429, 500) and re-runs the
agent against each mutation. Reports which mutations the agent
survives, which change its output, which crash the process.

Bonus: `rewind bisect` compares two runs and classifies why they
differ (model version, prompt drift, tool drift, model
non-determinism).

MIT, local-only, works with any LLM provider including local Ollama
because it's HTTPS proxy + content-addressed cassette storage.

Repo: https://github.com/llm-rewind/rewind
Blog: [link]

Curious whether this addresses an actual pain point for the
agent-builders here. Feedback welcome.
```

---

## 5. r/MachineLearning

Different audience: more academic, more skeptical, less likely to
care about CLI ergonomics. Lead with the *why* not the *how*.

Title:

> [P] Cause-classified bisection and mutation testing for LLM agent
> recordings

Body:

```
Two ideas worth discussing whether or not anyone uses my
implementation:

1. **Cause inference on session divergence.** Given two recordings
   of the same agent execution that diverged, classify the cause:
   model version change, prompt drift, tool-list change, upstream
   tool output drift, or model non-determinism. Each comes with an
   actionable detail line. The naive baseline (diff the response
   blobs) is essentially what every observability platform does
   today; cause classification turns the diff into a diagnosis.

2. **Mutation testing as fault injection on replay.** Cassette-style
   HTTP replay for LLM calls is well-trodden (VCR.py 2010,
   vcr-langchain 2023, Docker cagent 2026). Treating the cassette
   as the substrate for systematic mutation is, as far as I can
   tell, novel. Mutations are deliberately mapped to real failure
   modes: dropped LLM call, empty completion, max_tokens
   truncation, 429, 500. Tells you the agent's tolerance to each
   without writing fault-injection scaffolding by hand.

Implementation, tests, and a side-by-side comparison vs LangSmith,
vcr-langchain, and Docker cagent are in the repo:
https://github.com/llm-rewind/rewind

Happy to expand on the cause taxonomy or the mutation kinds in
comments.
```

---

## 6. r/Python

Lead with the engineering, not the AI angle. This subreddit
appreciates clean implementations more than novel applications.

Title:

> Built a local HTTPS proxy that records LLM API calls, replays
> them deterministically, and mutation-tests the calling agent

Body:

```
TL;DR: pip install llm-rewind; mitmproxy + DuckDB + zstd blobs + a
custom addon that strips auth headers and credential query params
before anything hits disk.

Two non-obvious things I learned building it:

1. Python 3.13's stricter X.509 verifier rejects CA certs that lack
   a SubjectKeyIdentifier extension. mitmproxy will happily issue
   per-host certs signed by your CA, but no modern client will
   verify the chain. HTTPS interception silently fails. Worth
   knowing if you're rolling your own MITM tool.

2. subprocess.run() blocks the asyncio event loop. If your CLI
   starts mitmproxy as an asyncio task and then runs the agent via
   subprocess.run() on the same loop, the proxy stops servicing
   requests for the entire subprocess lifetime. Switch to
   asyncio.create_subprocess_exec.

Both were real bugs I shipped in v0.1.0 and fixed in v0.2.1 after
a second-pass audit caught them. CHANGELOG has the full story.

Repo: https://github.com/llm-rewind/rewind
```

---

## 7. LinkedIn (post last, this is for recruiters)

```
Shipped Rewind: an open-source debugger for AI agents that records,
replays, and mutation-tests them at the HTTP layer.

Two features no existing tool ships:

→ `rewind bisect` doesn't just show that two agent runs differ. It
classifies the cause: was it a model version bump, a prompt change,
a tool returning different output, or model non-determinism?

→ `rewind mutate` systematically perturbs the recorded cassette
(drops steps, returns 429s, truncates responses) and re-runs the
agent against each mutation to find where it silently breaks
before production does.

Both built on top of a cassette-replay substrate (HTTPS proxy +
content-addressed blobs + DuckDB), prior art credit to VCR.py and
Docker cagent for that base layer.

MIT, 142 tests, supports OpenAI / Anthropic / Gemini.

Tech stack: Python 3.11+, mitmproxy 11, DuckDB 1.2, zstandard 0.23.

GitHub: https://github.com/llm-rewind/rewind
Blog with end-to-end walkthrough: [link]

If you're building production agents and want to know where they're
fragile before users find out, give it a try and let me know what
breaks.

#AI #OpenSource #Python #LLM
```

---

## Posting Order and Timing

1. **Tuesday 09:00 PT** - Blog post goes live.
2. **Tuesday 09:30 PT** - HN Show post (Tue morning is peak HN
   traffic for technical posts).
3. **Tuesday 10:00 PT** - Twitter thread.
4. **Tuesday 14:00 PT** - r/Python.
5. **Wednesday 09:00 PT** - r/LocalLLaMA, r/MachineLearning (post on
   day two so the HN spike doesn't compete with itself).
6. **Wednesday 12:00 PT** - LinkedIn (recruiters read LinkedIn on
   Wed afternoon).

Total launch week effort: 4 hours of writing, 1 hour of recording,
2 hours of replying to comments. Plan to be available all of
Tuesday afternoon.
