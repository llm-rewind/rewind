"""Semantic mutation — LLM-driven adversarial rewriting of recorded responses.

The five built-in mutations in ``mutate.py`` are *syntactic*: drop a step,
blank a body, force a 429. They test how an agent handles transport-level
faults. Semantic drift tests something harder — what happens when an upstream
model returns an answer that is still well-formed and on-topic but subtly
**wrong**: a flipped recommendation, a changed number, an inverted
conclusion. That is the failure mode that slips past schema checks and
quietly corrupts agent output, and no other cassette tool perturbs it.

A small, cheap model (Gemini Flash) does the rewriting. **Tests never call
it.** The mutator is injected behind the ``SemanticMutator`` protocol; the
suite passes a deterministic stub. The real ``GeminiFlashMutator`` reads its
key from the environment and sends it as the ``x-goog-api-key`` header — never
a URL query param — so the key cannot leak into a request URL, an httpx error
message, or a log line.
"""

from __future__ import annotations

import copy
import os
from typing import Protocol, runtime_checkable

from rewind.constants import (
    SEMANTIC_MUTATOR_HOST,
    SEMANTIC_MUTATOR_MODEL,
    SEMANTIC_MUTATOR_TIMEOUT_S,
)
from rewind.exceptions import SemanticMutatorError

# Instruction handed to the rewriting model. Deliberately asks for a change
# that stays plausible and same-shaped so the mutation probes semantic
# robustness, not format handling (the syntactic mutations cover that).
_REWRITE_PROMPT = (
    "You are an adversarial tester for AI agents. Rewrite the assistant message "
    "below so it stays fluent, on-topic, and the same approximate length, but is "
    "now subtly WRONG: flip a recommendation, change a number, invert a "
    "conclusion, or swap a key fact. Do not add disclaimers or mention that you "
    "changed anything. Return ONLY the rewritten message text.\n\n"
    "--- assistant message ---\n{text}"
)


@runtime_checkable
class SemanticMutator(Protocol):
    """Anything that can rewrite a piece of assistant text into a drifted variant.

    The real implementation calls a small LLM; tests pass a deterministic stub.
    """

    def rewrite(self, text: str) -> str: ...


class GeminiFlashMutator:
    """Rewrites text via Gemini Flash. Key from env, sent as a header, never logged."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = SEMANTIC_MUTATOR_MODEL,
        timeout: float = SEMANTIC_MUTATOR_TIMEOUT_S,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("REWIND_API_KEY")
        if not key:
            raise SemanticMutatorError(
                "no API key for semantic mutator; set GEMINI_API_KEY (or REWIND_API_KEY)"
            )
        self._key = key
        self._model = model
        self._timeout = timeout

    def rewrite(self, text: str) -> str:
        import httpx

        url = f"https://{SEMANTIC_MUTATOR_HOST}/v1beta/models/{self._model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": _REWRITE_PROMPT.format(text=text)}]}],
            # High temperature so repeated runs explore different wrong answers.
            "generationConfig": {"temperature": 1.0},
        }
        try:
            resp = httpx.post(
                url,
                # Header auth, NOT ?key= — keeps the key out of the URL and out
                # of any httpx error message that echoes the request URL.
                headers={"x-goog-api-key": self._key, "content-type": "application/json"},
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            out = data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:  # noqa: BLE001 — sanitise: never surface the request detail
            # Deliberately drop the original message and chain. httpx errors can
            # carry the request URL/headers; re-raising only the type name
            # guarantees no key material reaches logs or tracebacks.
            raise SemanticMutatorError(f"gemini rewrite failed ({type(e).__name__})") from None
        return str(out).strip()


# ---------------------------------------------------------------------------
# Provider response shape handling
# ---------------------------------------------------------------------------
# Extract / replace the assistant's text inside a parsed provider response
# body. Shapes are the documented response formats for each provider; an
# unrecognised shape returns None so the caller can skip rather than guess.


def extract_assistant_text(body: dict[str, object]) -> str | None:
    """Pull the assistant's text out of a parsed provider response body.

    Returns None if the body does not match a known provider shape (OpenAI
    chat completions, Anthropic messages, or Gemini generateContent).
    """
    # OpenAI: {"choices": [{"message": {"content": "..."}}]}
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                openai_text = msg.get("content")
                if isinstance(openai_text, str):
                    return openai_text

    # Anthropic: {"content": [{"type": "text", "text": "..."}]}
    content = body.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    return t

    # Gemini: {"candidates": [{"content": {"parts": [{"text": "..."}]}}]}
    cands = body.get("candidates")
    if isinstance(cands, list) and cands:
        first = cands[0]
        if isinstance(first, dict):
            cand_content = first.get("content")
            if isinstance(cand_content, dict):
                parts = cand_content.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict):
                            gemini_text = part.get("text")
                            if isinstance(gemini_text, str):
                                return gemini_text

    return None


def set_assistant_text(body: dict[str, object], new_text: str) -> dict[str, object]:
    """Return a copy of `body` with the assistant text replaced by `new_text`.

    Mirrors the shape detection in :func:`extract_assistant_text`. If no known
    shape matches, the body is returned unchanged (callers only invoke this
    after a successful extraction, so that branch should not be reached).
    """
    out = copy.deepcopy(body)

    choices = out.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            msg["content"] = new_text
            return out

    content = out.get("content")
    if isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                block["text"] = new_text
                return out

    cands = out.get("candidates")
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        cand_content = cands[0].get("content")
        if isinstance(cand_content, dict):
            parts = cand_content.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part["text"] = new_text
                        return out

    return out
