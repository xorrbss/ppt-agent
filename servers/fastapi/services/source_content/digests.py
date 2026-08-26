"""Canonical digest helpers used across source-content contracts."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest_json(value: Any) -> str:
    return f"sha256:{sha256(canonical_json_bytes(value)).hexdigest()}"


def digest_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"
