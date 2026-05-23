# ADR-001: HTTP Proxy Over SDK Monkey-Patching

**Status:** Accepted

## Context

Rewind must intercept LLM API calls to record and replay them. Two capture strategies:

A. Patch the provider SDK at the Python function level (monkey-patching)
B. Run an HTTP MITM proxy that intercepts at the network layer

## Decision

HTTP MITM proxy (mitmproxy embedded via `DumpMaster`) is the primary capture mode.
SDK decorator (`@rewind.session`) is kept as secondary convenience mode.

## Rationale

SDK patching is fragile: breaks when provider SDK internals change, Python-only, requires per-SDK maintenance, misses HTTP calls that bypass the SDK (e.g., subprocess agents, Node.js sidecar).

MITM proxy intercepts at the HTTP layer regardless of language, framework, or SDK version. Neither `openai-python` nor `anthropic-python` implement certificate pinning (both use `httpx` with system cert validation), so MITM requires no bypasses.

## Tradeoffs

- **Gained:** Language-agnostic, framework-agnostic, stable across SDK version changes, captures all outbound HTTP
- **Lost:** Requires one-time CA cert installation; slightly more setup friction vs pure decorator

## Alternatives Considered

- **SDK decorator only:** Simpler setup but Python-only, fragile against SDK updates, misses non-SDK HTTP calls
- **LD_PRELOAD socket interception:** Too complex, platform-specific, not portable to Windows/macOS

## Consequences

- `rewind init` must install mitmproxy CA cert to system trust store (`chmod 600` on private key)
- `rewind record <cmd>` wrapper auto-sets `HTTPS_PROXY` and `SSL_CERT_FILE`
- CA cert handling is **High Risk** — see `docs/security/STANDARDS.md`
