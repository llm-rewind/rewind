# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Live recording cassette via Google Gemini (drops the synthetic placeholder)
- `x-goog-api-key` header stripping + URL `?key=` redaction for Gemini support
- gRPC interception support (Gemini SDK defaults to gRPC, currently bypasses proxy)

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

[Unreleased]: https://github.com/llm-rewind/rewind/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/llm-rewind/rewind/releases/tag/v0.1.0
