"""Canonical Scene identity, shared by validation and revision sealing."""
import hashlib
import json
import math
import unicodedata
from typing import Any

def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("scene canonical JSON does not permit non-finite numbers")
        if value.is_integer():
            return int(value)
        return float(format(value, ".15g"))
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_value(item)
            for key, item in value.items()
        }
    return value


def scene_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
