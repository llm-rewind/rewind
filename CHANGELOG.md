# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `rewind explain <good> <bad>` — causal explanation engine. Where `bisect`
  reports the single first divergence, `explain` treats it as the root,
  then walks the rest of the trace to separate divergences that propagated
  from it (changed inputs) from independent ones (identical request,
  different response). Reports the full chain with a heuristic confidence
  (capped at 0.95) that the first divergence is the true root. New module
  `src/rewind/engines/explain.py`; reuses the bisect cause inference.

### Planned

- gRPC interception support (Gemini SDK defaults to gRPC, currently bypasses proxy)
- Cross-platform CI matrix (currently Ubuntu only; tests run locally on Windows)

## [0.2.1] - 2026-05-24

### Security

- **v0.2.0 sdist contained a PyPI upload token in `.claude/settings.local.json`**
  (a tooling artifact that was tracked in git but should have been ignored).
  The token was detected by Deps.dev within hours of publication and
  automatically revoked by PyPI; no malicious release was made. v0.2.0
  has been yanked. **Upgrade to 0.2.1.**
- `.claude/settings.local.json` is now in `.gitignore` and removed from
  the tree, so no editor or build-tool secrets can ride along in future
  releases.
- Followups for users who installed 0.2.0: no action required for runtime
  use; the token did not authenticate to any production system other
  than the publisher's own PyPI account.

### Fixed

- Audit gaps from the second-pass review of v0.2.0 land here as
  bundled fixes:
  - `asyncio.create_task(_waiter())` in proxy/addon.py held no
    reference and could be garbage-collected, silently breaking
    programmatic shutdown. Reference is now retained and cancelled
    in the finally block.
  - CA private key (`~/.rewind/ca.key`) was world-readable on Windows
    because `Path.chmod(0o600)` is a silent no-op on NTFS. `rewind
    init` now invokes `icacls` on win32 to set an owner-only DACL.
  - `rewind mutate` did not clean up mutated sessions on abort, and
    sequential mutations raced against TIME_WAIT on the same proxy
    port. Cleanup is in a finally block; each mutation gets a fresh
    free port.
  - `bisect` cause inference reported `model changed: None -> None`
    when neither side had a model field. Guarded.
  - `gemini_agent.py` raised `SystemExit` on missing key even in
    replay mode where no real key is needed. Replay now uses a
    placeholder.
- Docs: CLAUDE.md guardrail 1 describes both the proxy 599 path and
  the SDK decorator raise. CONTRIBUTING test count updated.

## [0.2.0] - 2026-05-24 [YANKED]

Yanked due to credential leak; see 0.2.1 security note.

The release that makes the previous one honest. v0.1.0 shipped without
end-to-end proof that the record-replay loop worked end-to-end against
real provider traffic. An internal audit found three production bugs
that quietly broke HTTPS interception on modern Python. All three are
fixed and covered by tests that drive real bytes through a real
mitmproxy instance. Also adds the two features that actually
differentiate this project from VCR.py and Docker cagent: divergence
cause inference in `bisect` and a mutation-testing harness.

### Added

- `rewind mutate <session-id>`. Systematically perturbs the cassette
  (drop step, empty response, truncate, 429, 500) and re-runs the
  agent against each mutation. Reports survival rate per mutation
  kind. Surfaces fragility before production drift does.
- Divergence cause inference in `rewind bisect`. Classifies the root
  cause into `model_version_changed`, `prompt_drift`,
  `tool_list_changed`, `tool_output_drift`, `llm_nondeterminism`,
  `step_count_differs`, or `step_type_changed`, with an actionable
  detail line for each.
- `x-goog-api-key` header is now stripped on capture.
- URL query parameters carrying credentials (`key`, `api_key`,
  `access_token`, `token`) are stripped from the path before either
  the `match_key` hash or the stored blob. A cassette recorded with
  one user's Gemini key now replays for any other user.
- `tests/cassettes/gemini_simple_agent.rw`: a real cassette recorded
  against `generativelanguage.googleapis.com` with zero key residue.
- End-to-end test harness: local self-signed HTTPS server in
  `tests/fixtures/local_llm_server.py` plus a threaded mitmproxy
  runner in `tests/fixtures/proxy_runner.py`. Used by three new
  integration tests that prove the record, replay, and SSE round-trip
  paths end-to-end with real HTTP.
- 40 new unit and integration tests; full suite is now 140 tests.

### Changed

- `RecordAddon` and `ReplayAddon` now accept an optional
  `provider_hosts` parameter so users can record against providers
  beyond the OpenAI/Anthropic/Gemini defaults without editing the
  source. The same parameter unlocks honest end-to-end tests.
- `run_record_proxy` and `run_replay_proxy` accept `ssl_insecure` for
  test scenarios that hit self-signed upstreams. Production paths
  default to `False` and must opt in.
- Session command is now stored as a JSON-encoded argv list rather
  than a space-joined string. Paths with spaces and quotes survive
  the round-trip. Old string-formatted entries are still parsed via
  shlex with the platform-appropriate posix mode.

### Fixed

- **CA cert was missing the SubjectKeyIdentifier extension.** Python
  3.13's stricter X.509 verifier rejects chains anchored on certs
  without SKI, which means every cert mitmproxy issued was unusable
  by modern clients. HTTPS interception silently failed with a
  `ConnectError`. All users should re-run `rewind init` after
  upgrading to regenerate the CA.
- **`subprocess.run` was blocking the event loop.** The CLI started
  mitmproxy as an asyncio task and then ran the agent via a blocking
  `subprocess.run` on the same loop. The proxy could not service any
  request until the agent exited, so the agent timed out on its first
  HTTPS call. Replaced with `asyncio.create_subprocess_exec`.
- **Strict-mode cassette miss fell through to the real upstream.** A
  `raise CassetteMissError` inside a mitmproxy addon hook is logged
  and the request continues upstream, which silently calls the live
  LLM. `ReplayAddon` now writes a 599 HTTP response with a structured
  error body and `X-Rewind-Cassette-Miss` header to short-circuit the
  upstream dial.
- Proxy-bind race: `cli.py` used to sleep 0.8s and hope; now polls
  the TCP port until it accepts connections.
- `rewind --version` was looking up the wrong distribution name
  because the package is `llm-rewind` and the command is `rewind`.

### Removed

- `src/rewind/engines/replay.py`: an empty stub from the original
  phase plan. Replay orchestration lives in `cli.py:replay` and
  `proxy/addon.py:run_replay_proxy`.
- `src/rewind/sdk/patches.py`: an empty stub. The decorator path uses
  contextvars; per-SDK patching turned out to be unnecessary.

### Security

- Stricter strict-mode enforcement: cassette miss never reaches the
  upstream provider in `REWIND_MODE=replay` even under realistic
  mitmproxy lifecycle conditions, which the previous version's unit
  tests did not exercise.

## [0.1.0] - 2026-05-23

First public release. End-to-end record and replay works for OpenAI, Anthropic,
and (with REST transport) Google Gemini.

### Added

- HTTPS MITM proxy capture via mitmproxy 11, intercepting `api.openai.com`,
  `api.anthropic.com`, and `generativelanguage.googleapis.com`
- DuckDB-backed session and step metadata store
- Content-addressed blob store with SHA-256 integrity verification and zstd
  compression
- `match_key` request normalization that strips `tool_call_id`, sorts tool
  definitions, and drops volatile fields before hashing (see ADR-004)
- Server-Sent Events recording and replay with chunk-order preservation
- Bisection engine that walks two sessions in parallel and surfaces the first
  divergent step
- CLI: `init`, `record`, `replay`, `list`, `inspect`, `diff`, `bisect`,
  `export`, `import`, `stats`
- `@rewind.session` and `@rewind.tool` decorators for pure-Python agents
- `pytest-rewind` plugin with the `@pytest.mark.rewind(cassette=...)` marker
- `.rw` cassette file format for portable, self-contained session sharing
- CA certificate auto-generation with 600 permissions on the private key
- 100 unit and integration tests, all running without live API access

### Security

- Authorization, x-api-key, x-request-id, idempotency-key, all x-stainless-\*,
  cf-ray, cf-cache-status, set-cookie, and anthropic-organization-id headers
  stripped before any blob is written
- Defense-in-depth check on cassette export rejects any blob containing an
  Authorization or x-api-key substring
- Replay mode raises `CassetteMissError` rather than passing through to the
  live API. Permissive passthrough requires an explicit `--permissive` flag
- CA private key never appears in logs, error messages, or exports

[Unreleased]: https://github.com/llm-rewind/rewind/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/llm-rewind/rewind/releases/tag/v0.2.1
[0.2.0]: https://github.com/llm-rewind/rewind/releases/tag/v0.2.0
[0.1.0]: https://github.com/llm-rewind/rewind/releases/tag/v0.1.0
