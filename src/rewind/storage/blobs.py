"""Content-addressed blob filesystem store.

Write-once, SHA-256 verified on every read, zstd compressed.
Hash mismatch raises BlobTamperedError — never log-and-continue.
See ADR-003 for design rationale.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import zstandard as zstd

from rewind.constants import BLOB_DIR, BLOB_SHARD_PREFIX_LEN
from rewind.exceptions import BlobTamperedError

_COMPRESSOR = zstd.ZstdCompressor(level=3)
_DECOMPRESSOR = zstd.ZstdDecompressor()


class BlobStore:
    def __init__(self, base_dir: Path = BLOB_DIR) -> None:
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)

    def write(self, data: bytes) -> str:
        """Compress and store data. Returns SHA-256 hex digest."""
        digest = hashlib.sha256(data).hexdigest()
        path = self._path_for(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            compressed = _COMPRESSOR.compress(data)
            path.write_bytes(compressed)
        return digest

    def read(self, digest: str) -> bytes:
        """Load, decompress, and verify blob. Raises BlobTamperedError on hash mismatch."""
        path = self._path_for(digest)
        compressed = path.read_bytes()
        data = _DECOMPRESSOR.decompress(compressed)
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise BlobTamperedError(expected=digest, actual=actual)
        return data

    def exists(self, digest: str) -> bool:
        return self._path_for(digest).exists()

    def _path_for(self, digest: str) -> Path:
        shard = digest[:BLOB_SHARD_PREFIX_LEN]
        return self._base / shard / digest
