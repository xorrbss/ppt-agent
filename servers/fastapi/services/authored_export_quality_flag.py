"""Read the hybrid exporter's per-slide fallback ids and flag crowded slides.

The exporter already reports, per slide, which elements it had to leave on the
raster backplate (``quality.slides[n].fallbackElementIds``, mirrored in the
``<file>.pptx.quality.json`` sidecar). Stage 4's rule is simple: a slide that
keeps more than ``AUTHORED_EXPORT_FALLBACK_LIMIT`` of them (2 by default) did
not export as an editable slide in any meaningful sense, and is marked a
quality failure.

Two reasons are excluded from the count on the exporter's own advice: an
element whose *paint* stayed on the backplate because of a gradient or a shadow
still contributes its text natively, so it is not evidence of a failed slide.

This is reporting only. Nothing here rejects an export or changes a file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from utils.get_env import get_authored_export_fallback_limit

#: Fallback reasons that describe backplate-only paint, not a lost element.
BACKPLATE_ONLY_REASONS = frozenset({"gradient-backplate-only", "shadow-backplate-only"})


@dataclass(frozen=True)
class SlideFallbackQuality:
    """One slide's counted raster fallbacks and whether it failed the rule."""

    slide_number: int
    fallback_element_ids: tuple[str, ...]
    counted_element_ids: tuple[str, ...]
    failed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "slideNumber": self.slide_number,
            "fallbackElementIds": list(self.fallback_element_ids),
            "countedElementIds": list(self.counted_element_ids),
            "failed": self.failed,
        }


@dataclass(frozen=True)
class ExportFallbackQuality:
    """The per-slide verdicts of one export."""

    limit: int
    slides: tuple[SlideFallbackQuality, ...]

    @property
    def failed_slide_numbers(self) -> tuple[int, ...]:
        return tuple(slide.slide_number for slide in self.slides if slide.failed)

    @property
    def failed(self) -> bool:
        return bool(self.failed_slide_numbers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "failed": self.failed,
            "failedSlideNumbers": list(self.failed_slide_numbers),
            "slides": [slide.to_dict() for slide in self.slides],
        }


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _backplate_only_ids(slide: Mapping[str, Any]) -> frozenset[str]:
    """Ids whose only recorded reasons are backplate-only paint."""
    elements = slide.get("fallbackElements")
    if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)):
        return frozenset()
    excluded: set[str] = set()
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        element_id = str(element.get("elementId", "")).strip()
        reasons = set(_string_tuple(element.get("reasons")))
        if element_id and reasons and reasons <= BACKPLATE_ONLY_REASONS:
            excluded.add(element_id)
    return frozenset(excluded)


def evaluate_export_fallback_quality(
    quality: Mapping[str, Any] | None,
    *,
    limit: int | None = None,
    settings: Any = None,
) -> ExportFallbackQuality:
    """Classify every slide of one export quality report."""
    threshold = get_authored_export_fallback_limit(settings) if limit is None else max(0, int(limit))
    raw_slides = quality.get("slides") if isinstance(quality, Mapping) else None
    if not isinstance(raw_slides, Sequence) or isinstance(raw_slides, (str, bytes)):
        return ExportFallbackQuality(limit=threshold, slides=())
    slides: list[SlideFallbackQuality] = []
    for index, raw in enumerate(raw_slides):
        if not isinstance(raw, Mapping):
            continue
        try:
            number = int(raw.get("slideNumber", index + 1))
        except (TypeError, ValueError):
            number = index + 1
        ids = _string_tuple(raw.get("fallbackElementIds"))
        excluded = _backplate_only_ids(raw)
        counted = tuple(item for item in ids if item not in excluded)
        slides.append(
            SlideFallbackQuality(
                slide_number=number,
                fallback_element_ids=ids,
                counted_element_ids=counted,
                failed=len(counted) > threshold,
            )
        )
    return ExportFallbackQuality(limit=threshold, slides=tuple(slides))


__all__ = [
    "BACKPLATE_ONLY_REASONS",
    "ExportFallbackQuality",
    "SlideFallbackQuality",
    "evaluate_export_fallback_quality",
]
