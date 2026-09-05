"""Body font median as a receipt signal (QUAL-06).

Computed from the Chromium element metrics the capacity checks already
collected: no new render, and never a gate. The value lets a reader compare
the typographic density of two decks without opening the slides.

A body owner is a measured element that directly owns visible text under a
``body`` semantic role (the same role predicate the capacity checks use);
title, supporting, decoration and image owners are excluded. The deck-level
value is the median of the per-slide medians, so one text-heavy slide does
not dominate it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from services.authored_composition_policy import _is_body_role
from services.authored_vnext.composition_capacity import _median


def _element_role(item: Mapping[str, object]) -> str:
    return str(item.get("semantic_role", item.get("semanticRole", item.get("role", ""))) or "").strip().lower()


def body_font_sizes_px(elements: object) -> list[float]:
    """The computed font sizes of the body text owners in one slide's metrics."""
    if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)):
        return []
    sizes: list[float] = []
    for item in elements:
        if not isinstance(item, Mapping) or str(item.get("tag", "")).lower() == "img":
            continue
        text = str(item.get("rendered_text", item.get("renderedText", "")) or "").strip()
        owned = item.get("has_owned_text", item.get("hasOwnedText"))
        owns_text = owned is True if isinstance(owned, bool) else bool(text)
        if not owns_text or not text or not _is_body_role(_element_role(item)):
            continue
        size = item.get("font_size", item.get("fontSize"))
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
            sizes.append(float(size))
    return sorted(sizes)


def body_font_median_px(metrics: object) -> float | None:
    """Median body font size of one slide, or ``None`` with no body owner."""
    elements = metrics.get("elements") if isinstance(metrics, Mapping) else None
    sizes = body_font_sizes_px(elements)
    return round(_median(sizes), 2) if sizes else None


def body_font_median_signal(metrics_by_slide: Sequence[object]) -> dict[str, object]:
    """Per-slide medians (index aligned, ``None`` where unmeasured) and the deck median."""
    slides = [body_font_median_px(metrics) for metrics in metrics_by_slide]
    measured = sorted(value for value in slides if value is not None)
    return {
        "slides": slides,
        "deck": round(_median(measured), 2) if measured else None,
        "measuredSlideCount": len(measured),
    }


__all__ = ["body_font_median_px", "body_font_median_signal", "body_font_sizes_px"]
