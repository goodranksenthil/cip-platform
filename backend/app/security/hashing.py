"""CIP-PA-003 / 025 / 283 — SHA-256 integrity fingerprints.

* Python `hashlib` standard library. `usedforsecurity=True` makes the intent explicit:
  on a FIPS-enabled OpenSSL build only approved algorithms are permitted, and SHA-256 is.
* Streaming in 64 KB chunks keeps memory flat regardless of document size, so a 400-page
  PDF costs the same RAM as a 1-page note.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO

DEFAULT_CHUNK = 64 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def new_sha256() -> "hashlib._Hash":
    return hashlib.sha256(usedforsecurity=True)


def iter_chunks(stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK) -> Iterator[bytes]:
    while chunk := stream.read(chunk_size):
        yield chunk


def sha256_stream(stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK) -> tuple[str, int]:
    """Return (hex digest, total bytes) reading the stream in fixed-size chunks."""
    h = new_sha256()
    total = 0
    for chunk in iter_chunks(stream, chunk_size):
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest(), total


def sha256_file(path: Path, chunk_size: int = DEFAULT_CHUNK) -> str:
    with path.open("rb") as fh:
        return sha256_stream(fh, chunk_size)[0]


def sha256_bytes(data: bytes) -> str:
    h = new_sha256()
    h.update(data)
    return h.hexdigest()


def sha256_iter(chunks: Iterable[bytes]) -> str:
    h = new_sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def is_valid_digest(value: str | None) -> bool:
    return bool(value) and bool(_HEX64.match(value))  # type: ignore[arg-type]


def digests_equal(a: str, b: str) -> bool:
    """Constant-time comparison — avoids leaking prefix-match timing."""
    return hmac.compare_digest(a.encode(), b.encode())
