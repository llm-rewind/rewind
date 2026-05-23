# Security Standards

## Threat Model

| Threat | Mitigation |
|--------|-----------|
| API key leaked via cassette | Auth headers stripped before storage (ADR-004) |
| Tampered cassette causing wrong debug conclusion | SHA-256 integrity check on every blob read |
| CA private key exposure | chmod 600, never logged, excluded from exports |
| PII in cassettes committed to git | `--scrub-pii` flag + documentation warning |
| Replay mode silently calling live APIs | `CassetteMissError` hard fail in STRICT mode |
| MITM proxy used beyond intended scope | CA cert scope: localhost only, documented |

## CA Certificate

Rewind generates a local CA certificate for HTTPS MITM. Highest-risk component.

Rules:
- CA private key at `~/.rewind/ca.key` — `chmod 600` immediately after generation
- CA cert at `~/.rewind/ca.pem` (public, safe to share for debugging setup)
- CA private key **never** appears in: logs, error messages, cassette exports, git commits
- `rewind init` generates 4096-bit RSA CA minimum
- Scope: localhost MITM only. Rewind must not be used as a system-wide proxy.
- CA cert valid for 1 year. `rewind init` rotates automatically on expiry.

## Cassette Storage

Cassettes may contain sensitive prompts, responses, and PII.

Rules:
- Auth headers (`Authorization`, `x-api-key`, all `x-stainless-*`) stripped before any blob is written
- Stripping verified in integration tests: stored blob must not contain `Authorization` key
- `rewind inspect` and `rewind export` redact auth-related fields from displayed/exported output
- `--scrub-pii` flag applies configurable regex patterns before storage (documented, not enabled by default)
- Cassettes for sharing must be reviewed before distribution
- Cassettes committed to git must not contain real user data or prompts

## Blob Integrity

- SHA-256 verified on every blob read (not just on write)
- Hash mismatch raises `BlobTamperedError` — never log-and-continue, never silently return data
- Rationale: Rewind is a debugging tool; tampered cassettes would corrupt debugging conclusions
- Write path is append-only: blobs immutable once written

## Replay Mode Safety

- `REWIND_MODE=replay` must block all outbound LLM API calls at the proxy level
- Cassette miss in STRICT mode (default): `CassetteMissError` with message:
  `"No cassette for match_key {key[:8]}... — run with REWIND_RECORD_MODE=record to create one"`
- PERMISSIVE mode (`--permissive`): passthrough to live API, log explicit warning with cost estimate
- Never silently passthrough without warning

## Secrets Hygiene

- No API keys in: source code, tests, cassettes, logs, error messages
- `rewind inspect` output redacts all auth headers
- `REWIND_API_KEY` env var used only during recording; Rewind does not store or forward it

## All Auth/AuthZ Modifications Are High Risk

Any change to CA cert generation, blob integrity checks, or cassette header stripping requires:
- Impact analysis documenting what security guarantee changes
- Review of all callers
- Integration test verifying the security invariant still holds
