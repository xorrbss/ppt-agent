"""Digest helpers for sealed workspace quality receipts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from utils.hashing import canonical_json_digest


def _release_review_receipt_digest(payload: Mapping[str, Any]) -> str:
    return canonical_json_digest(dict(payload), allow_nan=True)


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
    )
