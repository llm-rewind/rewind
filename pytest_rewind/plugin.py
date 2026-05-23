"""pytest-rewind plugin — cassette marker and fixtures for LLM test replay.

Usage:
    @pytest.mark.rewind(cassette="tests/cassettes/customer_support.rw")
    async def test_agent_handles_refund_request():
        result = await run_customer_support_agent("I want a refund")
        assert "refund" in result.lower()

Set REWIND_RECORD_MODE=record to create cassettes from real LLM calls.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pytest_rewind.cassette import import_cassette, load_cassette_file
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "rewind(cassette): run test with Rewind cassette replay; "
        "create cassette with REWIND_RECORD_MODE=record",
    )


@pytest.fixture
def rewind_mode() -> str:
    """Current Rewind mode: 'record' or 'replay'."""
    return os.environ.get("REWIND_MODE", "replay")


@pytest.fixture
def cassette_dir() -> Path:
    """Root directory where cassette .rw files are stored."""
    return Path(os.environ.get("REWIND_CASSETTE_DIR", "tests/cassettes"))


@pytest.fixture
def rewind_cassette(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> RewindDB | None:
    """Load a .rw cassette for the current test (if @pytest.mark.rewind is set).

    Returns the loaded RewindDB on success, or None if no marker is present.
    Fails the test if the cassette is missing and REWIND_RECORD_MODE != record.
    """
    marker = request.node.get_closest_marker("rewind")
    if marker is None:
        return None

    cassette_path_str: str = marker.kwargs.get("cassette", "")
    if not cassette_path_str:
        pytest.fail("@pytest.mark.rewind requires cassette= keyword argument")

    cassette_path = Path(cassette_path_str)
    record_mode = os.environ.get("REWIND_RECORD_MODE", "replay")

    if not cassette_path.exists():
        if record_mode == "record":
            # Record mode — test runs live; cassette saved after by the test itself
            return None
        pytest.fail(
            f"Cassette not found: {cassette_path}\nRun with REWIND_RECORD_MODE=record to create it."
        )

    # Replay mode — load cassette into an in-memory DB + tmp blob store
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    data = load_cassette_file(cassette_path)
    import_cassette(data, db, blobs)
    return db
