# Contributing to Rewind

Thanks for the interest. Rewind is small and the bar for changes is high
because the project sits in two security-sensitive paths at once: a local
MITM proxy and a content-addressed blob store. Read the rules below before
opening a PR.

## Local Setup

```bash
git clone https://github.com/llm-rewind/rewind
cd rewind
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## The Loop You Will Live In

Every change goes through these four commands. All four must pass.

```bash
pytest                                          # 140+ tests, no API key required
ruff check src/ tests/ pytest_rewind/
ruff format src/ tests/ pytest_rewind/ --check
mypy src/ pytest_rewind/ --strict
```

If any of these fail, your PR is not ready.

## Required Reading

Before touching the proxy, storage, or cassette format:

- [CLAUDE.md](CLAUDE.md) – the rules of engagement for this codebase
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – overall design
- [docs/security/STANDARDS.md](docs/security/STANDARDS.md) – the security model
- [docs/testing/STRATEGY.md](docs/testing/STRATEGY.md) – why we never call live APIs
- [docs/adr/](docs/adr/) – the architecture decisions that shape this project

## Risk Classification

| Low risk                                           | Medium risk                                       | High risk                                                 |
|----------------------------------------------------|---------------------------------------------------|-----------------------------------------------------------|
| CLI display, docs, constants, new test cassettes   | Schema evolution, new provider, dependency adds   | Proxy matching, CA handling, blob integrity, match_key    |

High-risk changes require an impact analysis in the PR description, the
failure modes you considered, and a rollback plan.

## Tests Are Not Optional

- New behavior needs at least one test that exercises it
- Cassettes go in `tests/cassettes/` and are committed to git
- Tests must pass with no API keys configured. CI will fail loudly otherwise.

If your test needs a new cassette and you have an API key, record it locally:

```bash
REWIND_RECORD_MODE=record pytest -m record
```

Then commit the resulting `.rw` file along with your test.

## Commits and PRs

- Subject line under 72 characters, imperative present tense, no prefix
- Body explains why, not what. The diff already shows what.
- One logical change per PR. Refactors and features do not share a commit.
- No silent fixups or hook bypasses (`--no-verify`, `--no-gpg-sign`)

## Reporting Security Issues

Do not open public issues for security problems. Email the maintainer
listed in `pyproject.toml`. We will respond within 72 hours.

## Code of Conduct

Be direct. Be respectful. If you would not say it in a code review at a
serious company, do not write it here.
