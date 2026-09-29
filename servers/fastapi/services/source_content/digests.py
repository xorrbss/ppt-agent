"""Canonical digest helpers used across source-content contracts."""

from __future__ import annotations

from typing import Any

from utils.hashing import canonical_json_bytes, sha256_prefixed

__all__ = ["canonical_json_bytes", "digest_bytes", "digest_json"]


def digest_json(value: Any) -> str:
    return sha256_prefixed(canonical_json_bytes(value))


def digest_bytes(value: bytes) -> str:
    return sha256_prefixed(value)
