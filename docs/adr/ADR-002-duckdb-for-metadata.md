# ADR-002: DuckDB for Session Metadata

**Status:** Accepted

## Context

Rewind needs structured storage for session and step metadata with analytical query capability:
- "Find all sessions where step 4 returned X"
- "Total cost by agent this week"
- "Sessions with more than 20 steps (possible loop detection)"

It must work as a zero-ops local developer tool with no server process.

## Decision

DuckDB (`~/.rewind/db.duckdb`) for all structured metadata.

## Rationale

DuckDB is zero-dependency (single pip install), embedded (no server), and columnar — matching the analytical read-heavy access pattern for session/step data. Native JSON support enables querying metadata fields without schema churn. Single `.duckdb` file is trivially portable and exportable.

## Tradeoffs

- **Gained:** Zero-ops, fast analytics, rich SQL + JSON, single-file portability, no server process
- **Lost:** No concurrent multi-process writes (acceptable: local tool, single writer per run)

## Alternatives Considered

- **SQLite:** Simpler but row-oriented (slower for analytics), limited JSON query support
- **PostgreSQL:** Powerful but requires running server — inappropriate for local dev tool
- **JSON files per session:** No query capability, poor performance at scale

## Consequences

- Database at `~/.rewind/db.duckdb`; schema managed in `src/rewind/storage/db.py`
- Schema changes are **High Risk**: require migration logic + cassette compatibility check
- `rewind gc` command needed for orphaned blob cleanup (future)
