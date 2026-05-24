"""All configuration constants. No magic values elsewhere in the codebase."""

from __future__ import annotations

from pathlib import Path

# Filesystem layout
REWIND_DIR = Path.home() / ".rewind"
DB_PATH = REWIND_DIR / "db.duckdb"
BLOB_DIR = REWIND_DIR / "blobs"
CA_CERT_PATH = REWIND_DIR / "ca.pem"
CA_KEY_PATH = REWIND_DIR / "ca.key"

# Proxy
DEFAULT_PROXY_PORT = 8080
PROXY_HOST = "127.0.0.1"

# Providers — hostname → provider name
PROVIDER_HOSTS: dict[str, str] = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "gemini",
}

# Storage
BLOB_SHARD_PREFIX_LEN = 2  # use first N hex chars as subdir (256 shards, prevents inode exhaustion)
MAX_INLINE_BLOB_BYTES = 5 * 1024 * 1024  # 5MB — larger blobs stored as path reference

# Security — headers stripped before any blob is written (see ADR-004)
STRIP_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "x-goog-api-key",  # Google Generative AI / Vertex AI auth header
        "x-goog-user-project",  # Google project identifier (PII)
        "x-request-id",
        "idempotency-key",
        "cf-ray",
        "cf-cache-status",
        "set-cookie",
        "anthropic-organization-id",
    }
)

# Header prefixes stripped before storage (all headers starting with these)
STRIP_HEADER_PREFIXES: tuple[str, ...] = ("x-stainless-",)

# URL query parameters that carry credentials and must never appear in
# stored cassettes or in the match_key. Google's REST API authenticates via
# ?key=AIza... in the query string, so leaving it in would both leak the
# key and tie the match_key to a single user's credentials.
STRIP_URL_PARAMS: frozenset[str] = frozenset({"key", "api_key", "access_token", "token"})

# Estimated costs in USD per 1K tokens (input, output) — label as "estimated" in all output
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "claude-haiku-4-5": (0.00025, 0.00125),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-opus-4-7": (0.015, 0.075),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "gemini-1.5-pro": (0.00125, 0.005),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "gemini-2.0-flash": (0.0001, 0.0004),
}

# CA certificate
CA_KEY_BITS = 4096
CA_CERT_DAYS = 365
CA_COMMON_NAME = "Rewind Local CA"
