<!-- Subject line: imperative present, under 72 chars, no prefix. -->

## What

One or two sentences. What does this PR change in behavior?

## Why

The reason this change exists. Reference an issue if there is one. If the
change is non-obvious, explain the constraint or incident that motivated it.

## How it was tested

- [ ] `pytest` passes locally
- [ ] `ruff check src/ tests/ pytest_rewind/` clean
- [ ] `ruff format src/ tests/ pytest_rewind/ --check` clean
- [ ] `mypy src/ pytest_rewind/ --strict` clean
- [ ] New behavior has at least one test
- [ ] Cassettes are committed if any were added

## Risk

- [ ] **Low** — CLI display, docs, constants, new test cassettes
- [ ] **Medium** — schema evolution, new provider, dependency add
- [ ] **High** — proxy matching, CA handling, blob integrity, match_key

If High risk, link the impact analysis and rollback plan in the body.

## Anything reviewers should know

Tradeoffs you made, decisions you punted, follow-ups you plan.
