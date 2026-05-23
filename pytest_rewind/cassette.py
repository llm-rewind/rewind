"""Cassette file format (.rw) for pytest-rewind — re-exports from rewind.engines.cassette.

Self-contained JSON: session metadata + steps + all blob content (base64-encoded).
One .rw file = full replay capability without external DB or blob store.
"""

from __future__ import annotations

from rewind.engines.cassette import (
    CASSETTE_VERSION,
    export_cassette,
    import_cassette,
    load_cassette_file,
    save_cassette_file,
)

__all__ = [
    "CASSETTE_VERSION",
    "export_cassette",
    "import_cassette",
    "load_cassette_file",
    "save_cassette_file",
]
