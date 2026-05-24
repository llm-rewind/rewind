"""Tests against the REAL Gemini cassette recorded via `rewind record`.

Unlike test_real_cassette.py which uses a synthetic stand-in, this file
asserts on bytes that came back from generativelanguage.googleapis.com
through the actual recording pipeline. Security invariants here are
tested against real provider data, not hand-crafted JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rewind.engines.cassette import import_cassette, load_cassette_file
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB

CASSETTE_PATH = Path("tests/cassettes/gemini_simple_agent.rw")

# Recorded with this key; presence in the cassette would be a security regression.
_RECORDED_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_-]{20,}")


@pytest.fixture()
def loaded(tmp_path: Path) -> tuple[RewindDB, BlobStore, str]:
    data = load_cassette_file(CASSETTE_PATH)
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    session_id = import_cassette(data, db, blobs)
    return db, blobs, session_id


@pytest.mark.integration
def test_cassette_file_exists() -> None:
    assert CASSETTE_PATH.exists()


@pytest.mark.integration
def test_cassette_no_api_key_residue() -> None:
    """A real-recording cassette must not contain any AIza...-shaped key."""
    text = CASSETTE_PATH.read_text(encoding="utf-8")
    matches = _RECORDED_KEY_PATTERN.findall(text)
    assert matches == [], f"API key leaked into cassette: {matches}"


@pytest.mark.integration
def test_cassette_no_auth_headers() -> None:
    text = CASSETTE_PATH.read_text(encoding="utf-8")
    # Case-sensitive check on the header NAMES (values are gone too because
    # the headers themselves are stripped, but defense in depth).
    for banned in ("Authorization", "x-api-key", "x-goog-api-key"):
        assert banned not in text, f"banned header name appeared in cassette: {banned}"


@pytest.mark.integration
def test_cassette_url_path_has_no_key_query_param(
    loaded: tuple[RewindDB, BlobStore, str],
) -> None:
    """Gemini auth is ?key= in the URL. Recording must strip it before storage."""
    db, blobs, session_id = loaded
    steps = db.get_steps(session_id)
    assert steps
    req_blob = steps[0].req_blob
    assert req_blob is not None
    req = json.loads(blobs.read(req_blob))
    assert "key=" not in req["path"]
    assert "AIza" not in req["path"]


@pytest.mark.integration
def test_cassette_recorded_provider_is_gemini(
    loaded: tuple[RewindDB, BlobStore, str],
) -> None:
    db, _, session_id = loaded
    steps = db.get_steps(session_id)
    assert len(steps) == 1
    assert steps[0].provider == "gemini"
    assert steps[0].latency_ms > 0  # real network latency was recorded


@pytest.mark.integration
def test_cassette_response_body_contains_expected_text(
    loaded: tuple[RewindDB, BlobStore, str],
) -> None:
    """The agent prompted Gemini to say 'hello from rewind gemini test'. The
    real response must contain that text. This proves we recorded a real
    Gemini reply, not a placeholder."""
    db, blobs, session_id = loaded
    steps = db.get_steps(session_id)
    resp_blob = steps[0].resp_blob
    assert resp_blob is not None
    resp = json.loads(blobs.read(resp_blob))
    # The body field is the response JSON as a string.
    body = json.loads(resp["body"])
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    assert "hello from rewind gemini test" in text.lower()
