"""F4: how large the browser-metrics LRU is allowed to be.

``docs/authored-quality-speed-programme-design-20260905.md`` sections 1.1 and 4.
A 32-entry LRU made a long deck evict its own early slides, and the deck tail
re-rendered them in Chromium purely to collect telemetry it had already
collected once (counter ``metrics_recollection_requests``). The registered
knobs raise that to 64 entries / 96 MB by default.

This lives outside ``utils/slide_capture.py`` because that module is at the
file-size ratchet, and because the resolution has one property worth stating
on its own: it happens on FIRST USE, never at import. ``slide_capture`` is
imported by benchmark scripts and by tooling with no runtime-settings
snapshot, so reading settings at import time would move a request-scoped read
into module load.
"""

from __future__ import annotations

import os

#: The legacy, unregistered override that predates the knobs. An operator who
#: set it explicitly asked for a specific budget and still gets it.
LEGACY_BYTE_BUDGET_ENV = "AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET"


def resolve_metrics_cache_limits(
    *, entries_default: int, budget_default: int
) -> tuple[int, int]:
    """Return ``(entries, byte budget)`` for the browser-metrics LRU.

    The defaults passed in are the module's own import-time values, which are
    also the answer whenever settings are unavailable (some tooling has no
    snapshot) — the documented previous behaviour, never an exception.
    """

    entries, budget = entries_default, budget_default
    try:
        from utils.get_env_programme import (
            get_authored_metrics_cache_entries,
            get_authored_metrics_cache_mb,
        )

        # An explicit AUTHORED_RENDER_CACHE_SIZE above the knob is honoured
        # rather than lowered; the knob is a floor for the metrics cache.
        entries = max(entries, int(get_authored_metrics_cache_entries()))
        raw_budget = os.getenv(LEGACY_BYTE_BUDGET_ENV, "").strip()
        if not raw_budget:
            budget = int(get_authored_metrics_cache_mb()) * 1024 * 1024
        else:
            try:
                parsed = int(raw_budget)
                budget = parsed if parsed >= 0 else budget
            except ValueError:
                pass
    except Exception:
        pass
    return entries, budget


__all__ = ["LEGACY_BYTE_BUDGET_ENV", "resolve_metrics_cache_limits"]
