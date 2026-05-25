# Side-by-Side Comparison: Rewind vs Existing Tools

A real, reproducible head-to-head against the three tools Rewind is
most likely to be accused of cloning. Run this once, screenshot the
output, embed it in the blog post and Twitter thread. The asymmetry
in what each tool can answer is the strongest single argument for
the project.

## The Scenario

Pick one realistic agent failure to debug. Recommended:

> A customer-support agent that worked yesterday now hallucinates
> account numbers. You have a recording of yesterday (good run) and
> a recording of today (bad run). Find the root cause without
> burning more API tokens.

This is the scenario every observability platform claims to handle.
Run the same scenario through each tool and document what each one
gives you.

## Tool 1: LangSmith

```python
# Open the LangSmith UI, find both runs, click into them.
# Manually compare span trees. Read JSON payloads side by side.
```

What you get:
- A pretty tree of spans for each run
- Click into individual LLM calls to see prompt + completion
- "Re-run" button that calls the live model

What you do NOT get:
- Automatic comparison of two runs
- Classification of why they differ
- A way to replay locally without LLM cost
- A way to test how the agent would behave if step 3 returned an
  error (you would have to mock that yourself in code)

Time to root cause: 10-30 minutes of manual reading, depending on
trace length. Cost: real API tokens if you re-run anything.

## Tool 2: vcr-langchain

```python
import vcr_langchain as vcr

with vcr.use_cassette("yesterday.yaml"):
    result = run_agent("...")
```

What you get:
- Deterministic replay of yesterday's recording, no API cost
- Works in pytest

What you do NOT get:
- Anything if you need to compare yesterday to today; vcr-langchain is
  one cassette at a time
- Anything about *why* the agent's output today differs
- Anything about how the agent would behave if today's response had
  been an empty completion or a 429
- Cross-provider: vcr-langchain only handles LangChain SDK calls

## Tool 3: Docker cagent

```yaml
# cassette.yaml
- request: {...}
  response: {...}
```

What you get:
- VCR-style HTTP cassettes for LLM calls. Real deterministic replay.
- `tool_call_id` normalisation (Rewind borrowed this design)
- Works inside cagent's own agent framework

What you do NOT get:
- Comparison of two recordings
- Cause inference on divergence
- Mutation testing
- Works only inside the cagent framework, not arbitrary agents

## Tool 4: Rewind

```bash
$ rewind bisect yesterday today

First divergence at step 4
  Session A: yesterday  model='gpt-4o-2025-11'
  Session B: today      model='gpt-4o-2026-05'
  Cause:    model_version_changed
  Detail:   model changed: 'gpt-4o-2025-11' -> 'gpt-4o-2026-05'.
            Model upgrades are the highest-likelihood cause of
            behaviour shifts.
```

```bash
$ rewind mutate yesterday --command "python customer_agent.py"

Mutation Report
+-------------------+------+----------------+--------------------------+
| Mutation          | Step | Outcome        | Detail                   |
+===================+======+================+==========================+
| empty_response    |    4 | OUTPUT CHANGED | hallucinated account #   |
| truncate_response |    7 | CRASHED        | JSONDecodeError          |
| ...
```

Time to root cause: 4 seconds.

Bonus: the `empty_response` mutation finding hallucination is the
same class of bug that triggered the original incident. So you have
both the cause of *this* incident and a list of related failure
modes the same agent is vulnerable to.

## Summary Table for the Blog Post

| Capability                              | LangSmith | vcr-langchain | cagent | **Rewind** |
| --------------------------------------- | :-------: | :-----------: | :----: | :--------: |
| Deterministic replay (no API cost)      |    no     |      yes      |  yes   |  **yes**   |
| Framework-agnostic (works with any SDK) |    yes    |      no       |   no   |  **yes**   |
| Compare two runs automatically          |    no     |      no       |   no   |  **yes**   |
| Classify divergence cause               |    no     |      no       |   no   |  **yes**   |
| Mutation testing for agents             |    no     |      no       |   no   |  **yes**   |
| Local-only (no cloud)                   |    no     |      yes      |  yes   |  **yes**   |
| Open source                             |  partial  |      MIT      | Apache |  **MIT**   |

## How to Reproduce This Comparison

If you want to actually run the four tools side by side for the post:

1. Pick or build an agent that takes a short instruction and produces
   a short answer. The
   `tests/agents/gemini_agent.py` in this repo is fine for a first
   pass; build a more realistic 5-10 step agent for the real post.
2. Record one "good" run and one "bad" run. Easiest way to force a
   divergence: change a model in the second run, OR change a system
   prompt, OR introduce a deliberate tool-output drift.
3. Time how long it takes to find the root cause with each of the
   four tools above. Use a stopwatch.
4. Record the screen as you do it. Even a low-quality screen capture
   is fine; the asymmetry between Rewind's one-command answer and the
   manual JSON-reading the other tools require is the story.
5. Take the four screen captures and assemble them into a single
   image (Figma or any image editor). 2x2 grid. Each panel labelled
   with the tool name and the wall-clock time it took.

That image is the hero of the blog post.

## What Not to Do

- Do not cherry-pick failures the other tools could find with a bit
  more effort. The honest argument is "Rewind gives you a one-command
  answer; the others give you a tree to read." Lean on the asymmetry,
  not on staged failures.
- Do not claim Rewind replaces observability platforms. LangSmith /
  Braintrust / Laminar do many things Rewind does not (long-term
  cost analytics, dataset eval, prompt versioning, etc). Position
  Rewind as the **debugger** that complements them, not the
  observability platform that replaces them.
- Do not bury cagent. Crediting the prior art that inspired your
  project signals confidence. Hiding it invites the inevitable HN
  comment that does it for you.
