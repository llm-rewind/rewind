# Rewind — Time-Travel Debugger for AI Agents

> Record any production run. Replay any failure. Find the exact step that broke.

```
$ rewind bisect run-good-7f3a run-bad-9b2c

  First divergence: Step 4 (llm_call)
  ─────────────────────────────────────────────────────────
  Model A:   claude-sonnet-4-6
  Model B:   claude-sonnet-4-6-20260501  ← changed at 09:00 UTC

  Good response:  "I'll proceed with the transfer."
  Bad response:   "I cannot complete this action without..."

  Likely cause: model version change at step 4
  Steps matched: 3 / 12
```

**AI agents fail in production. You can't reproduce it.**
Rewind fixes that.

## Install

```bash
pip install rewind-ai
rewind init
```

## Quick Start

```bash
# Record a run
rewind record python my_agent.py

# Replay it locally — zero LLM cost
rewind replay <session-id>

# Find where two runs diverged
rewind bisect <good-run> <bad-run>
```

## Status

Work in progress. See [BUILD_GUIDE.md](BUILD_GUIDE.md) for implementation status.
