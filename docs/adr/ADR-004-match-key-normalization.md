# ADR-004: match_key Normalization Strategy

**Status:** Accepted

## Context

Deterministic replay requires matching incoming LLM requests to recorded responses. LLM requests contain volatile fields that change across runs without affecting semantics:
- `tool_call_id` values (e.g., `call_abc123`) — random UUIDs generated fresh per run
- SDK telemetry headers (e.g., `x-stainless-lang`) — vary by SDK version
- Auth headers — change per environment

Without normalization: cassette miss on every run because no two requests match. With wrong normalization: false matches return wrong cassettes.

## Decision

`match_key = SHA-256(canonical_json)` where canonical JSON contains only semantically meaningful fields.

**Included in match_key:**
```python
{
    "model": str,
    "messages": messages_with_tool_call_ids_stripped,  # strip "id" from tool_calls entries
    "tools": tools_sorted_by_name,                     # stable order
    "temperature": float | None,
    "max_tokens": int | None,
    "system": str | None,  # Anthropic system prompt
}
```

**Stripped before hashing (volatile, not semantic):**
- `tool_call_id` and `tool_call.id` values in messages
- `stream` flag (does not change model response content)
- `user` field (tracking ID, not semantic)
- `n` field

**Stripped from cassette storage entirely (secrets/infrastructure):**
- `Authorization`, `x-api-key`
- `x-request-id`, `idempotency-key`
- All `x-stainless-*` headers (openai-python SDK telemetry)
- `cf-ray`, `cf-cache-status`, `set-cookie`
- `anthropic-organization-id`

## Rationale

Docker cagent identified `tool_call_id` normalization as the critical insight — without stripping these random UUIDs, replay always fails because messages referencing tool calls contain IDs that never match. Stripping `x-stainless-*` headers prevents openai-python version bumps from invalidating cassettes.

Sorting tools by name ensures that tools advertised in different order produce the same match_key.

## Tradeoffs

- **Gained:** Stable cassettes across retries, SDK version changes, and environment differences
- **Lost:** Fields not in match_key cannot distinguish requests — if a stripped field affects model behavior, two different requests may match the same cassette (incorrect replay)

## Alternatives Considered

- **Full raw body hash:** Breaks on tool_call_id rotation, SDK header changes, retry variations
- **Sequence position only (like VCR.py default):** Breaks on parallel tool calls and retries
- **Per-field allowlist matching:** More precise but complex to maintain; overkill for current needs

## Consequences

- `src/rewind/proxy/normalize.py` is **single source of truth** for normalization logic
- Changes to the strip list or included fields are **High Risk**: invalidate existing cassettes
- Adding a new field to match_key requires regenerating all committed test cassettes
- This ADR must be updated whenever normalization rules change
