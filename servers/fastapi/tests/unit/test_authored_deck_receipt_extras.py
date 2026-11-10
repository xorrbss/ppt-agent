"""H4/H6 receipt extras and F4 metrics readback.

See ``docs/authored-quality-speed-programme-design-20260905.md`` sections 4
(F4) and 6 (H4, H6). Both receipt surfaces are advisory: this file's contract
is that they appear under the documented keys when their knob is on, vanish
when it is off, and can never change what the deck publishes.
"""

from __future__ import annotations

import asyncio
import hashlib


def _run(coro):
    return asyncio.run(coro)


def _digest(html: str) -> str:
    return "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Deck palette
# ---------------------------------------------------------------------------


class _Style:
    preview_palette = ["#0A0A0A", "#2563EB", "#2563EB"]


class _Brand:
    primary = "#10B981"


# ---------------------------------------------------------------------------
# collect_deck_instruments
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# attach_deck_receipt_extras
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# F4: metrics readback
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# F4: the metrics LRU is sized from the knobs, lazily
# ---------------------------------------------------------------------------


def test_the_metrics_cache_is_sized_from_the_knobs_on_first_use(monkeypatch):
    from utils import slide_capture

    monkeypatch.setattr(slide_capture, "_DOM_METRICS_CACHE_LIMITS", None)
    monkeypatch.setenv("AUTHORED_METRICS_CACHE_ENTRIES", "128")
    monkeypatch.setenv("AUTHORED_METRICS_CACHE_MB", "48")
    monkeypatch.delenv("AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET", raising=False)

    assert slide_capture._dom_metrics_cache_limits() == (128, 48 * 1024 * 1024)
    # Resolved once: a later change of the knob does not re-read settings.
    monkeypatch.setenv("AUTHORED_METRICS_CACHE_ENTRIES", "16")
    assert slide_capture._dom_metrics_cache_limits() == (128, 48 * 1024 * 1024)
    monkeypatch.setattr(slide_capture, "_DOM_METRICS_CACHE_LIMITS", None)


def test_an_explicit_legacy_byte_budget_still_wins(monkeypatch):
    from utils import slide_capture

    monkeypatch.setattr(slide_capture, "_DOM_METRICS_CACHE_LIMITS", None)
    monkeypatch.setenv("AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET", str(8 * 1024 * 1024))
    monkeypatch.setenv("AUTHORED_METRICS_CACHE_MB", "96")

    assert slide_capture._dom_metrics_cache_limits()[1] == 8 * 1024 * 1024
    monkeypatch.setattr(slide_capture, "_DOM_METRICS_CACHE_LIMITS", None)
