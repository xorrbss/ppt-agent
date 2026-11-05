"""Byte accounting shared by the in-process HTML render caches.

A metrics payload's footprint is its JSON size, which costs a full encode of
a ~1 MB mapping. The resident-byte sums run after every cache insert, so a
caller may pass ``sizes``: a memo of each entry's footprint by cache key,
bound to the entry object's identity. Entries are treated as immutable once
cached (every reader shares them), so an entry is measured once.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, MutableMapping, Protocol


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


SizeMemo = MutableMapping[object, "tuple[int, int]"]


def _resident_bytes(
    cache: Mapping[object, Any], measure: Callable[[Any], int], sizes: SizeMemo | None
) -> int:
    if sizes is None:
        return sum(measure(value) for value in cache.values())
    total = 0
    for key, value in cache.items():
        known = sizes.get(key)
        if known is None or known[0] != id(value):
            known = (id(value), measure(value))
            sizes[key] = known
        total += known[1]
    for stale in [key for key in sizes if key not in cache]:
        del sizes[stale]
    return total


def metrics_cache_resident_bytes(
    cache: Mapping[object, Mapping[str, Any]], *, overflow_bytes: int,
    sizes: SizeMemo | None = None,
) -> int:
    return _resident_bytes(
        cache, lambda value: metrics_payload_bytes(value, overflow_bytes=overflow_bytes), sizes
    )


def capture_cache_resident_bytes(
    cache: Mapping[object, _Capture], *, overflow_bytes: int,
    sizes: SizeMemo | None = None,
) -> int:
    return _resident_bytes(
        cache,
        lambda value: len(value.png) + (
            metrics_payload_bytes(value.browser_metrics, overflow_bytes=overflow_bytes)
            if value.browser_metrics is not None
            else 0
        ),
        sizes,
    )
