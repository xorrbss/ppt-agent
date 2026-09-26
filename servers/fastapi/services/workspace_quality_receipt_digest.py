"""Digest helpers for sealed workspace quality receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _release_review_receipt_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
    )
