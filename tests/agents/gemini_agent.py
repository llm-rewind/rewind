"""Minimal Gemini REST agent used for live cassette recording.

We hit the REST endpoint with httpx directly instead of using the
google-generativeai SDK because the SDK defaults to gRPC which bypasses
HTTPS proxies entirely, making it invisible to mitmproxy. REST works
through any HTTP proxy.

Run:
    GEMINI_API_KEY=AIza... rewind record py tests/agents/gemini_agent.py
"""

from __future__ import annotations

import os

import httpx

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def run() -> str:
    if not API_KEY:
        raise SystemExit("Set GEMINI_API_KEY in the environment before recording.")

    response = httpx.post(
        URL,
        params={"key": API_KEY},
        json={
            "contents": [{"parts": [{"text": "Say exactly: hello from rewind gemini test"}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 64},
        },
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    print(text)
    return text


if __name__ == "__main__":
    run()
