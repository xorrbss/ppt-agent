"""H4/H6 receipt extras and F4 metrics readback.

See ``docs/authored-quality-speed-programme-design-20260905.md`` sections 4
(F4) and 6 (H4, H6). Both receipt surfaces are advisory: this file's contract
is that they appear under the documented keys when their knob is on, vanish
when it is off, and can never change what the deck publishes.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from services import authored_deck_instruments_stage as stage_mod
from services import authored_final_metrics as metrics_mod


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


def test_the_palette_is_the_style_swatches_plus_the_brand_primary():
    assert stage_mod.deck_palette(_Style(), _Brand()) == (
        "#0A0A0A",
        "#2563EB",
        "#10B981",
    )


def test_a_style_with_no_preview_palette_leaves_only_the_brand_primary():
    assert stage_mod.deck_palette(None, _Brand()) == ("#10B981",)
    assert stage_mod.deck_palette(None, None) == ()


# ---------------------------------------------------------------------------
# collect_deck_instruments
# ---------------------------------------------------------------------------


def test_no_instruments_module_is_a_none_not_a_failure(monkeypatch):
    # A3 owns ``utils/authored_deck_instruments``; until it lands the hook must
    # be a no-op rather than an import error inside the deck tail.
    import builtins

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "utils.authored_deck_instruments":
            raise ImportError("not built yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)

    assert stage_mod.collect_deck_instruments([(0, "<html></html>", {})], ()) is None


def test_an_empty_deck_measures_nothing():
    assert stage_mod.collect_deck_instruments([], ("#000000",)) is None


# ---------------------------------------------------------------------------
# attach_deck_receipt_extras
# ---------------------------------------------------------------------------


def _attach(receipt, **overrides):
    kwargs = {
        "htmls": ["<html>a</html>", "<html>b</html>"],
        "pngs": [b"png-a", b"png-b"],
        "metrics": [{"m": 1}, {"m": 2}],
        "style": _Style(),
        "brand": _Brand(),
    }
    kwargs.update(overrides)
    return _run(stage_mod.attach_deck_receipt_extras(receipt, **kwargs))


def test_both_knobs_off_leave_the_receipt_untouched(monkeypatch):
    monkeypatch.setenv("AUTHORED_DECK_INSTRUMENTS_ENABLED", "false")
    monkeypatch.setenv("AUTHORED_VISION_ADVISORY_ENABLED", "false")
    receipt: dict = {"status": "passed"}

    _attach(receipt)

    assert receipt == {"status": "passed"}


def test_the_advisory_lands_under_advisory_vision(monkeypatch):
    monkeypatch.setenv("AUTHORED_DECK_INSTRUMENTS_ENABLED", "false")
    monkeypatch.setenv("AUTHORED_VISION_ADVISORY_ENABLED", "true")
    from services import authored_vision_advisory as advisory_mod

    async def fake_advisory(pngs, *, slide_count):
        return {"model": "fake", "slides": [], "slideCount": slide_count}

    monkeypatch.setattr(advisory_mod, "run_vision_advisory", fake_advisory)
    receipt: dict = {"status": "passed"}

    _attach(receipt)

    assert receipt["advisory"]["vision"]["slideCount"] == 2
    assert receipt["status"] == "passed"


def test_the_instruments_land_under_instruments_deck(monkeypatch):
    monkeypatch.setenv("AUTHORED_DECK_INSTRUMENTS_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_VISION_ADVISORY_ENABLED", "false")
    seen: dict = {}

    def fake_collect(slides, palette):
        seen["slides"] = list(slides)
        seen["palette"] = tuple(palette)
        return {"largestEmptyRectPx": 42}

    monkeypatch.setattr(stage_mod, "collect_deck_instruments", fake_collect)
    receipt: dict = {"status": "passed"}

    _attach(receipt)

    assert receipt["instruments"]["deck"] == {"largestEmptyRectPx": 42}
    assert [index for index, _html, _metrics in seen["slides"]] == [0, 1]
    assert seen["palette"] == ("#0A0A0A", "#2563EB", "#10B981")


def test_a_failing_advisory_can_never_break_the_receipt(monkeypatch):
    monkeypatch.setenv("AUTHORED_DECK_INSTRUMENTS_ENABLED", "false")
    monkeypatch.setenv("AUTHORED_VISION_ADVISORY_ENABLED", "true")
    receipt: dict = {"status": "passed"}

    # The real advisory swallows provider failures; drive it with no renders so
    # the whole call path runs without a provider.
    _attach(receipt, pngs=[])

    assert receipt["advisory"]["vision"] == {"skipped": "no_renders"}


def test_missing_metrics_do_not_stop_the_instruments(monkeypatch):
    monkeypatch.setenv("AUTHORED_DECK_INSTRUMENTS_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_VISION_ADVISORY_ENABLED", "false")
    seen: dict = {}

    def fake_collect(slides, palette):
        seen["slides"] = list(slides)
        return {"ok": True}

    monkeypatch.setattr(stage_mod, "collect_deck_instruments", fake_collect)
    receipt: dict = {}

    _attach(receipt, metrics=[])

    assert [metrics for _index, _html, metrics in seen["slides"]] == [{}, {}]


# ---------------------------------------------------------------------------
# F4: metrics readback
# ---------------------------------------------------------------------------


def test_sealed_ledger_metrics_are_used_whole_including_their_gaps():
    htmls = ["<a>", "<b>"]
    retained = ({"source_digest": _digest("<a>")}, None)

    resolved = _run(
        metrics_mod.resolve_final_browser_metrics(htmls, retained=retained)
    )

    # The ledger deliberately retains a missing entry instead of re-rendering an
    # unchanged slide; the gate is meant to block on that, so it is passed on.
    assert resolved == [retained[0], None]


def test_a_ledger_of_the_wrong_length_is_not_the_decks_evidence():
    assert metrics_mod.checkpoint_browser_metrics(["<a>", "<b>"], ({"x": 1},)) is None
    assert metrics_mod.checkpoint_browser_metrics(["<a>"], ()) is None
    assert metrics_mod.checkpoint_browser_metrics(["<a>"], None) is None


def test_repair_round_metrics_are_reused_only_when_the_digest_matches(monkeypatch):
    htmls = ["<a>", "<b>", "<c>"]
    gathered: list[str] = []

    async def fake_ensure(html, *args, **kwargs):
        gathered.append(html)
        return {"source_digest": _digest(html), "gathered": True}

    from utils import slide_capture

    monkeypatch.setattr(slide_capture, "ensure_browser_metrics", fake_ensure)

    resolved = _run(
        metrics_mod.resolve_final_browser_metrics(
            htmls,
            retained=None,
            overrides={
                0: {"source_digest": _digest("<a>"), "from": "repair"},
                1: {"source_digest": _digest("STALE"), "from": "repair"},
            },
        )
    )

    # Slide 0 came from the repair round; 1's metrics were measured from other
    # bytes and 2 had none, so exactly those two were re-collected.
    assert resolved[0]["from"] == "repair"
    assert gathered == ["<b>", "<c>"]
    assert resolved[1]["gathered"] and resolved[2]["gathered"]


@pytest.mark.parametrize("metrics", [None, {}, {"source_digest": "sha256:other"}, "x"])
def test_metrics_without_this_htmls_digest_never_match(metrics):
    assert metrics_mod.metrics_match_html("<a>", metrics) is False


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
