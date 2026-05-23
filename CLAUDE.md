# Rewind — AI Agent Record & Replay Debugger

@docs/ARCHITECTURE.md
@docs/testing/STRATEGY.md
@docs/security/STANDARDS.md

## Commands

```bash
# Setup
pip install -e ".[dev]"

# Test (zero live LLM calls — ever)
pytest                           # all tests
pytest -m unit                   # unit tests only
pytest -m integration            # cassette-based integration tests

# Quality (all must pass before work is done)
ruff check src/ tests/           # lint
ruff format src/ tests/          # format
mypy src/ --strict               # type check
```

## Source of Truth (priority order)

1. Production code + DuckDB schema (`src/rewind/storage/db.py`)
2. Security constraints (`docs/security/STANDARDS.md`)
3. This file
4. ADRs → `docs/adr/`
5. Implementation docs → `docs/`
6. Inline comments

Lower-priority sources never override higher-priority constraints.

## Architecture Summary

Two-mode capture: **HTTP MITM proxy** (primary, language-agnostic) + **SDK decorator** (Python convenience).
Storage: DuckDB metadata + content-addressed blob filesystem.
See `@docs/ARCHITECTURE.md`.

## Coding Standards

- Python 3.11+. `from __future__ import annotations` in every module.
- All public symbols typed. No `Any` without `# type: ignore` + justification comment.
- No bare `except:`. Catch specific exceptions.
- No magic values. Use `src/rewind/constants.py`.
- Match existing patterns before introducing new abstractions.
- New dependency requires justification: purpose, license, maintenance quality, runtime impact.

## AI Agent Rules

**Stop on ambiguity.** Provider API formats, cassette schema, match_key normalization — verify, never infer.

**Verify before modifying.** Before changing proxy addon or storage layer: inspect callers, tests, and the relevant ADR.

**Minimal change.** Smallest safe change wins. No speculative abstractions. No refactoring outside task scope.

**Completion criteria.** Done when: `pytest` passes, `ruff check` clean, `mypy --strict` clean, cassettes committed, behavior validated.

## Risk Classification

| Low | Medium | High |
|-----|--------|------|
| CLI display, docs, constants, new test cassettes | Storage schema evolution, new provider support, dependency additions, new CLI commands | Proxy addon matching logic, CA cert handling, blob integrity checks, cassette format, `match_key` normalization |

High-risk changes require: impact analysis, failure-mode documentation, rollback plan.

## Non-Negotiable Guardrails

- `REWIND_MODE=replay` **must never** call real LLM APIs. Cassette miss → `CassetteMissError`, never silent passthrough.
- CA private key **never** logged, stored in cassettes, or committed to git.
- Blob SHA-256 verified on every read. Hash mismatch → hard fail (`BlobTamperedError`), not warning.
- Auth headers (`Authorization`, `x-api-key`, `x-stainless-*`) **never** stored in cassettes.
- `match_key` must strip all volatile fields before hashing. See `ADR-004` + `src/rewind/proxy/normalize.py`.
- Tests **never** call real LLM APIs. All integration tests use committed cassettes.
- Never silently fall back to guessed or inferred behavior anywhere in proxy or storage logic.
