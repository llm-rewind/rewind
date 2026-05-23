---
paths:
  - "src/rewind/proxy/**"
  - "src/rewind/storage/**"
  - "src/rewind/cli.py"
  - "src/rewind/sdk/**"
---

# Security Rules

## CA Certificate

- CA private key (`~/.rewind/ca.key`) must never appear in: logs, `print()`, error messages, `repr()`, cassette exports
- `rewind init` must call `os.chmod(ca_key_path, 0o600)` immediately after writing the key
- Never hardcode or default to a shared/test CA key in production code paths

## Cassette Storage — Header Stripping

`normalize.py` is the single source of truth for which headers are stripped.

Before any blob is written, these headers must be absent from stored bytes:
- `Authorization`
- `x-api-key`
- `x-request-id`
- `idempotency-key`
- All `x-stainless-*` headers

Verify stripping in integration tests: assert stored blob does not contain `"Authorization"` as a key.
Never skip header stripping "for debugging convenience."

## Blob Integrity

- Every call to `blobs.read(hash)` must verify `sha256(content) == hash`
- Mismatch raises `BlobTamperedError` — never `logging.warning()` and continue
- Never add a `verify=False` parameter to blob reads

## Replay Mode

- When `REWIND_MODE=replay`: the proxy must block all outbound calls to LLM providers
- Cassette miss in STRICT mode raises `CassetteMissError` with actionable message
- Never add logic that silently falls back to live API without explicit `--permissive` flag and warning log

## Display and Export

- `rewind inspect`, `rewind export`, and all `ui/display.py` output must redact auth headers
- Redaction pattern: `Authorization: Bearer sk-***...{last4}`
- Never log full API keys even at DEBUG level
