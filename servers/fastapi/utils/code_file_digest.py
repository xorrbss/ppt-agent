"""Per-process cache of source-file digests that bind code identity into evidence.

Recovery and template authorities hash the bytes of their own source files.
Those bytes cannot change under a running process (a code change needs a
restart, and the backend runs without a reloader), so each file is read and
hashed once per process instead of on every call.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

from utils.hashing import sha256_hex


@cache
def code_file_sha256(path: Path) -> str:
    """Bare hex SHA-256 of one source file, read once per process."""
    return sha256_hex(Path(path).read_bytes())


def clear_code_file_digests() -> None:
    """Forget cached digests; only tests that simulate changed code bytes need it."""
    code_file_sha256.cache_clear()
