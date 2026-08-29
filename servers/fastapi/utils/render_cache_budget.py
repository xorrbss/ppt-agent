"""Byte accounting shared by the in-process HTML render caches."""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol


class _Capture(Protocol):
    png: bytes
    browser_metrics: Mapping[str, Any] | None


def metrics_payload_bytes(value: Mapping[str, Any], *, overflow_bytes: int) -> int:
    """Return a conservative JSON footprint; unknown values cannot be free."""

    try:
        return len(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        )
    except Exception:
        return overflow_bytes + 1


def render_cache_resident_bytes(cache: Mapping[object, bytes]) -> int:
    return sum(len(value) for value in cache.values())


def metrics_cache_resident_bytes(
    cache: Mapping[object, Mapping[str, Any]], *, overflow_bytes: int
) -> int:
    return sum(
        metrics_payload_bytes(value, overflow_bytes=overflow_bytes)
        for value in cache.values()
    )


def capture_cache_resident_bytes(
    cache: Mapping[object, _Capture], *, overflow_bytes: int
) -> int:
    return sum(
        len(value.png)
        + (
            metrics_payload_bytes(value.browser_metrics, overflow_bytes=overflow_bytes)
            if value.browser_metrics is not None
            else 0
        )
        for value in cache.values()
    )
