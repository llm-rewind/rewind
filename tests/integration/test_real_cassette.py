"""Integration tests for cassette-based replay — Phase 2.7 gate.

Uses tests/cassettes/simple_agent.rw (synthetic, no live API key needed).
All assertions test replay machinery correctness, not provider response format.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewind.engines.cassette import import_cassette, load_cassette_file
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB

CASSETTE_PATH = Path("tests/cassettes/simple_agent.rw")


@pytest.fixture()
def loaded(tmp_path: Path) -> tuple[RewindDB, BlobStore, str]:
    """Load simple_agent.rw into a fresh in-memory DB + tmp blob store."""
    data = load_cassette_file(CASSETTE_PATH)
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    session_id = import_cassette(data, db, blobs)
    return db, blobs, session_id


@pytest.mark.integration
def test_cassette_file_exists() -> None:
    assert CASSETTE_PATH.exists(), f"Cassette missing: {CASSETTE_PATH}"


@pytest.mark.integration
def test_cassette_loads_one_step(loaded: tuple[RewindDB, BlobStore, str]) -> None:
    db, _, session_id = loaded
    steps = db.get_steps(session_id)
    assert len(steps) == 1
    assert steps[0].order_idx == 0
    assert steps[0].type == "llm_call"
    assert steps[0].provider == "anthropic"
    assert steps[0].model == "claude-haiku-4-5-20251001"


@pytest.mark.integration
def test_cassette_response_contains_expected_text(
    loaded: tuple[RewindDB, BlobStore, str],
) -> None:
    db, blobs, session_id = loaded
    steps = db.get_steps(session_id)
    assert steps[0].resp_blob is not None
    raw = blobs.read(steps[0].resp_blob)
    response = json.loads(raw)
    text = response["content"][0]["text"]
    assert text == "hello from rewind test"


@pytest.mark.integration
def test_cassette_match_key_is_stable(loaded: tuple[RewindDB, BlobStore, str]) -> None:
    """match_key must be deterministic — same request body always produces same key."""
    from rewind.proxy.normalize import normalize_request

    db, _, session_id = loaded
    steps = db.get_steps(session_id)
    stored_key = steps[0].match_key
    assert stored_key is not None

    req_body = json.dumps(
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Say exactly: hello from rewind test"}],
        }
    ).encode()
    recomputed = normalize_request("POST", "/v1/messages", req_body)
    assert recomputed == stored_key


@pytest.mark.integration
def test_cassette_no_auth_headers_on_disk() -> None:
    """Security invariant: auth headers must never appear in committed cassettes."""
    raw = CASSETTE_PATH.read_text(encoding="utf-8")
    assert "Authorization" not in raw
    assert "x-api-key" not in raw
    assert "Bearer" not in raw


@pytest.mark.integration
def test_cassette_blob_sha256_verified(loaded: tuple[RewindDB, BlobStore, str]) -> None:
    """BlobStore must verify SHA-256 on every read — integrity guarantee."""
    db, blobs, session_id = loaded
    steps = db.get_steps(session_id)
    # Read both blobs — raises BlobTamperedError if hash doesn't match
    assert steps[0].req_blob is not None
    assert steps[0].resp_blob is not None
    req_raw = blobs.read(steps[0].req_blob)
    resp_raw = blobs.read(steps[0].resp_blob)
    assert len(req_raw) > 0
    assert len(resp_raw) > 0


@pytest.mark.integration
def test_cassette_session_metadata(loaded: tuple[RewindDB, BlobStore, str]) -> None:
    db, _, session_id = loaded
    session = db.get_session(session_id)
    assert session is not None
    assert session.agent_name == "simple_agent"
    assert session.command == "python tests/agents/simple_agent.py"


@pytest.mark.integration
def test_missing_cassette_fails(tmp_path: Path) -> None:
    """Missing cassette must raise FileNotFoundError, not silently pass."""
    missing = tmp_path / "nonexistent.rw"
    with pytest.raises(FileNotFoundError):
        load_cassette_file(missing)
