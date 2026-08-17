"""Whitespace-safe transport for immutable retained-reference snapshots."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
import zlib


RETAINED_REFERENCE_MARKER_CODEC = "zlib-base64-v1"
_MAX_REFERENCE_MARKER_BYTES = 25_000_000


def canonical_reference_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def encode_reference_layout_marker(value: dict[str, Any]) -> str:
    """Encode a snapshot without whitespace-sensitive semantic text boundaries."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_REFERENCE_MARKER_BYTES:
        raise ValueError("retained reference layout marker exceeds size limit")
    return base64.b64encode(zlib.compress(encoded, level=9)).decode("ascii")


def decode_reference_layout_marker(value: str) -> dict[str, Any]:
    """Decode a bounded, deterministic retained-reference marker payload."""

    try:
        compressed = base64.b64decode(value, validate=True)
        decompressor = zlib.decompressobj()
        encoded = decompressor.decompress(
            compressed,
            _MAX_REFERENCE_MARKER_BYTES + 1,
        )
        if (
            len(encoded) > _MAX_REFERENCE_MARKER_BYTES
            or decompressor.unconsumed_tail
            or decompressor.unused_data
            or not decompressor.eof
        ):
            raise ValueError
        encoded += decompressor.flush()
        if len(encoded) > _MAX_REFERENCE_MARKER_BYTES:
            raise ValueError
        decoded = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise ValueError("retained reference layout marker encoding is invalid") from exc
    if not isinstance(decoded, dict):
        raise ValueError("retained reference layout marker must contain an object")
    return decoded


__all__ = [
    "RETAINED_REFERENCE_MARKER_CODEC",
    "canonical_reference_digest",
    "decode_reference_layout_marker",
    "encode_reference_layout_marker",
]
