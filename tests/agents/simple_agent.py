"""Simple agent for Phase 2.7 cassette recording demo.

Record:  rewind record python tests/agents/simple_agent.py
Replay:  rewind replay <session-id>
"""

from __future__ import annotations

import anthropic


def run() -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": "Say exactly: hello from rewind test"}],
    )
    text = response.content[0].text  # type: ignore[union-attr]
    print(text)
    return text


if __name__ == "__main__":
    run()
