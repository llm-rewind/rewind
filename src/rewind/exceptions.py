"""Rewind exception hierarchy. All exceptions raised by Rewind are subclasses of RewindError."""

from __future__ import annotations


class RewindError(Exception):
    """Base exception for all Rewind errors."""


class CassetteMissError(RewindError):
    """No cassette entry matches the incoming request in strict replay mode."""

    def __init__(self, match_key: str, session_id: str | None = None) -> None:
        self.match_key = match_key
        self.session_id = session_id
        super().__init__(
            f"No cassette found for match_key {match_key[:8]}... "
            f"(session: {session_id or 'unknown'}). "
            f"Run with REWIND_RECORD_MODE=record to create one."
        )


class BlobTamperedError(RewindError):
    """Blob content does not match its stored SHA-256 hash."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Blob integrity check failed. "
            f"Expected SHA-256: {expected[:16]}..., got: {actual[:16]}..."
        )


class RewindNotInitializedError(RewindError):
    """rewind init has not been run."""

    def __init__(self) -> None:
        super().__init__("Rewind not initialized. Run: rewind init")


class RewindSessionNotFoundError(RewindError):
    """No session found matching the given ID or prefix."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"No session found matching: {session_id!r}")


class SemanticMutatorError(RewindError):
    """The semantic mutator (small LLM) could not rewrite a response.

    Messages must never contain the API key. The Gemini key is sent as the
    `x-goog-api-key` header, not a URL query param, so upstream httpx error
    text and request URLs cannot leak it; this error only ever carries the
    exception type, never the underlying request detail.
    """
