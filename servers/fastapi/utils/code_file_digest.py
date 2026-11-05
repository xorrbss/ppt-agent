"""Per-process cache of source-file digests that bind code identity into evidence.

Recovery and template authorities hash the bytes of their own source files.
Those bytes cannot change under a running process (a code change needs a
restart, and the backend runs without a reloader), so each file is read and
hashed once per process instead of on every call.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from utils.hashing import sha256_hex


@dataclass(frozen=True)
class CodeFileDigest:
    """One source file's SHA-256 as it is on disk and over LF-normalized bytes."""

    raw: str
    lf: str
    #: The bytes on disk contain CRLF, as a Git ``autocrlf`` checkout of a text file does.
    crlf: bool

    @classmethod
    def of(cls, data: bytes) -> "CodeFileDigest":
        raw = sha256_hex(data)
        if b"\r\n" not in data:
            return cls(raw, raw, False)
        return cls(raw, sha256_hex(data.replace(b"\r\n", b"\n")), True)


@cache
def code_file_digest(path: Path) -> CodeFileDigest:
    """Digests of one source file, read once per process."""
    return CodeFileDigest.of(Path(path).read_bytes())


def code_file_sha256(path: Path) -> str:
    """Bare hex SHA-256 of one source file, read once per process."""
    return code_file_digest(path).raw


def clear_code_file_digests() -> None:
    """Forget cached digests; only tests that simulate changed code bytes need it."""
    code_file_digest.cache_clear()
