# ADR-003: Content-Addressed Blob Store

**Status:** Accepted

## Context

LLM request/response payloads are large (up to ~100KB JSON) and repetitive — the same system prompt appears across thousands of runs. Rewind needs efficient, tamper-evident storage for these payloads.

## Decision

Content-addressed filesystem at `~/.rewind/blobs/<sha256[:2]>/<sha256>`, zstd-compressed, SHA-256 verified on every read.

Path sharding by first 2 hex chars (256 subdirectories) prevents filesystem inode exhaustion at scale.

## Rationale

Content-addressing naturally deduplicates: identical system prompts stored once regardless of how many sessions reference them. SHA-256 integrity on read makes cassette tampering detectable — critical for debugging trust (a corrupted cassette leading to wrong root cause is worse than no cassette). zstd achieves ~70-80% compression on JSON payloads.

## Tradeoffs

- **Gained:** Deduplication, tamper evidence, immutability guarantees, efficient storage, simple implementation
- **Lost:** Blobs are immutable (no in-place update); blob deletion requires a GC pass

## Alternatives Considered

- **DuckDB BLOB inline:** Fine for <1MB but impractical for future audio/image tool outputs; harder to export
- **Sequential flat files:** No deduplication, no integrity check, no portable export format
- **Git-like object store:** Correct approach but significant complexity; our needs don't require full git semantics

## Consequences

- `blobs.py` must verify `sha256(content) == stored_hash` on every read — hash mismatch = `BlobTamperedError`
- `rewind export` bundles metadata JSON + referenced blobs (portable cassette format)
- `rewind import` verifies blob hashes before accepting imported cassette
- Future: `rewind gc --dry-run` to preview orphaned blobs before deletion
