"""Bounded durable projection for authored-hybrid worker quality evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


HYBRID_QUALITY_SUMMARY_SCHEMA = "public-hybrid-quality-summary/v1"
MAX_HYBRID_SLIDE_FALLBACKS = 200
_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")
_COUNT_FIELDS = (
    "editableSlides",
    "imageFallbackSlides",
    "rasterFallbackElements",
    "deckBudgetMillis",
    "slidesNotAttempted",
)
_STATUSES = frozenset({"fully-editable", "partially-editable", "image-only"})


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2_000_000_000 else None


def bounded_hybrid_quality_summary(value: object) -> dict[str, object]:
    """Return only the versioned, bounded fields safe for an operation receipt."""

    if (
        not isinstance(value, Mapping)
        or value.get("schemaVersion") != HYBRID_QUALITY_SUMMARY_SCHEMA
    ):
        return {}
    summary: dict[str, object] = {"schemaVersion": HYBRID_QUALITY_SUMMARY_SCHEMA}
    status = value.get("status")
    if status in _STATUSES:
        summary["status"] = status
    for field in _COUNT_FIELDS:
        count = _count(value.get(field))
        if count is not None:
            summary[field] = count

    tally = value.get("fallbackReasonTally")
    if isinstance(tally, Mapping):
        bounded_tally: dict[str, int] = {}
        for code, raw_count in tally.items():
            if len(bounded_tally) >= MAX_HYBRID_SLIDE_FALLBACKS:
                break
            count = _count(raw_count)
            if isinstance(code, str) and _CODE.fullmatch(code) and count is not None:
                bounded_tally[code] = count
        summary["fallbackReasonTally"] = bounded_tally

    fallbacks = value.get("slideFallbacks")
    if isinstance(fallbacks, Sequence) and not isinstance(fallbacks, (str, bytes)):
        bounded_fallbacks: list[dict[str, object]] = []
        for fallback in fallbacks:
            if len(bounded_fallbacks) >= MAX_HYBRID_SLIDE_FALLBACKS:
                break
            if not isinstance(fallback, Mapping):
                continue
            number = _count(fallback.get("n"))
            code = fallback.get("code")
            if number and isinstance(code, str) and _CODE.fullmatch(code):
                bounded_fallbacks.append({"n": number, "code": code})
        summary["slideFallbacks"] = bounded_fallbacks
    return summary


def bounded_hybrid_worker_evidence(value: Mapping[str, object]) -> dict[str, object]:
    """Normalize the quality member before the controller persists evidence."""

    evidence = dict(value)
    if "quality" in evidence:
        quality = bounded_hybrid_quality_summary(evidence.get("quality"))
        if quality:
            evidence["quality"] = quality
        else:
            evidence.pop("quality", None)
    return evidence


__all__ = [
    "HYBRID_QUALITY_SUMMARY_SCHEMA",
    "MAX_HYBRID_SLIDE_FALLBACKS",
    "bounded_hybrid_quality_summary",
    "bounded_hybrid_worker_evidence",
]
