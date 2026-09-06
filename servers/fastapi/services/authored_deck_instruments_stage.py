"""H4 hook: collect the deck-level quality instruments into the receipt.

``docs/authored-quality-speed-programme-design-20260905.md`` section 6, H4
(phase 1b): instruments only. The measurements themselves --- the largest empty
rect below the content union, palette conformance, radius families, margin
deviation, sub-floor computed fonts, stretched images --- live in
``utils/authored_deck_instruments.py``. This module is only the adapter between
the generation function's deck state and that module's input shape, so the
generation function keeps a one-call hook and the instruments stay testable
without a deck.

The instruments are evidence, never a gate: they give G-G3 and G-G4 their
numbers so the phase-2 decision about which of them deserve to become flags is
made from measurements rather than from opinion. Any failure here (including
the instruments module not being present yet) returns ``None`` and the deck
publishes exactly as it would have.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

LOGGER = logging.getLogger(__name__)

_MISSING_LOGGED = False


def _log_missing_once(reason: str) -> None:
    global _MISSING_LOGGED
    if not _MISSING_LOGGED:
        _MISSING_LOGGED = True
        LOGGER.info("[authored] deck instruments unavailable (%s)", reason)


def collect_deck_instruments(
    slides: Sequence[tuple[int, str, Mapping[str, Any]]],
    palette: Sequence[str] = (),
    fit_reports: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Measure one finished deck, or ``None`` when it cannot be measured.

    ``slides`` is ``(index, html, browser_metrics)`` per published slide, in
    deck order. ``palette`` is the deck's own colour list; an empty palette is
    passed through unchanged so the instrument reports palette conformance as
    null rather than inventing a reference. ``fit_reports`` is the deck's
    in-render-fit reports keyed by slide index (``docs/authored-in-render-
    fit-design-20260906.md`` section 6); a slide absent from it simply
    reports no fit.
    """

    try:
        from utils.authored_deck_instruments import (  # noqa: PLC0415
            SlideInstrumentInput,
            compute_deck_instruments,
        )
    except ImportError as error:
        _log_missing_once(str(error))
        return None

    try:
        colours = tuple(
            str(colour).strip() for colour in palette if str(colour or "").strip()
        )
        reports = fit_reports or {}
        inputs = [
            SlideInstrumentInput(
                index=int(index),
                html=str(html or ""),
                metrics=metrics if isinstance(metrics, Mapping) else {},
                palette=colours,
                fit_report=reports.get(int(index)),
            )
            for index, html, metrics in slides
        ]
        if not inputs:
            return None
        return dict(compute_deck_instruments(inputs).to_receipt())
    except Exception:
        LOGGER.warning("[authored] deck instruments failed", exc_info=True)
        return None


def deck_palette(style: Any, brand: Any) -> tuple[str, ...]:
    """The deck's palette: the style preview swatches plus the brand primary.

    Both sources are optional. An empty result is a legitimate answer (a deck
    authored from a style with no preview palette has nothing to conform to)
    and the instrument treats it as "not measured", never as "0 % conformant".
    """

    colours: list[str] = []
    for colour in getattr(style, "preview_palette", None) or ():
        text = str(colour or "").strip()
        if text and text not in colours:
            colours.append(text)
    primary = str(getattr(brand, "primary", "") or "").strip()
    if primary and primary not in colours:
        colours.append(primary)
    return tuple(colours)


async def attach_deck_receipt_extras(
    receipt: dict[str, Any],
    *,
    htmls: Sequence[str],
    pngs: Sequence[bytes],
    metrics: Sequence[Any],
    style: Any = None,
    brand: Any = None,
    fit_reports: Mapping[int, Mapping[str, Any]] | None = None,
) -> None:
    """Write H4's ``instruments.deck`` and H6's ``advisory.vision`` into the receipt.

    One hook for the generation tail so the two phase-1 evidence surfaces are
    added, gated and failure-isolated in one place. Both are advisory: this
    function mutates nothing but the receipt, and swallows every failure, so a
    deck's publication outcome is identical with and without it. ``fit_reports``
    carries the draft render's in-render-fit reports through to the deck
    instruments; see ``collect_deck_instruments``.
    """

    from utils.get_env_programme import (
        is_authored_deck_instruments_enabled,
        is_authored_vision_advisory_enabled,
    )

    if is_authored_deck_instruments_enabled():
        instruments = collect_deck_instruments(
            [
                (index, html, metrics[index] if index < len(metrics) else {})
                for index, html in enumerate(htmls)
            ],
            deck_palette(style, brand),
            fit_reports,
        )
        if instruments is not None:
            receipt["instruments"] = {**(receipt.get("instruments") or {}), "deck": instruments}

    if is_authored_vision_advisory_enabled():
        from services.authored_vision_advisory import run_vision_advisory

        receipt["advisory"] = {
            **(receipt.get("advisory") or {}),
            "vision": await run_vision_advisory(list(pngs), slide_count=len(htmls)),
        }


__all__ = [
    "attach_deck_receipt_extras",
    "collect_deck_instruments",
    "deck_palette",
]
