"""Bounded durable projection for authored-hybrid worker quality evidence."""

from __future__ import annotations

import math
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
EDITABILITY_SCHEMA = "public-hybrid-editability/v1"
_EDITABILITY_VERDICTS = frozenset({"fully-editable", "partially-editable", "not-editable", "not-applicable", "unknown"})
_EDITABILITY_COUNTS = (
    "requiredTargetCount", "nativeCorrespondenceCount", "rasterizedTargetCount",
    "missingTargetCount", "occludedTargetCount", "unknownTargetCount",
    "fullSlideRasterCount", "rasterFragmentCount",
)


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
    if "editability" in evidence:
        evidence["editability"] = bounded_hybrid_editability(evidence["editability"])
    if "timing" in evidence:
        evidence["timing"] = bounded_hybrid_timing(evidence["timing"])
    if "localFitFallbackPages" in evidence:
        pages = evidence["localFitFallbackPages"]
        evidence["localFitFallbackPages"] = [
            {"n": page["n"], "code": page["code"],
             **({"diagnostics": diagnostic} if (diagnostic := _bounded_fragment_diagnostic(page.get("diagnostics"))) else {})}
            for page in (pages[:100] if isinstance(pages, list) else [])
            if isinstance(page, Mapping) and type(page.get("n")) is int and 1 <= page["n"] <= 100
            and isinstance(page.get("code"), str) and _CODE.fullmatch(page["code"])
        ]
    if "quality" in evidence:
        quality = bounded_hybrid_quality_summary(evidence.get("quality"))
        if quality:
            evidence["quality"] = quality
        else:
            evidence.pop("quality", None)
    return evidence


def _bounded_fragment_diagnostic(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or value.get("schemaVersion") != "raster-fragment-diagnostic/v1":
        return {}
    result: dict[str, object] = {"schemaVersion": "raster-fragment-diagnostic/v1"}
    for key in ("bandIndex", "fragmentOrder", "packagedImageCount", "digestMatchCount", "placementMatchCount"):
        if _count(value.get(key)) is not None:
            result[key] = value[key]
    for key in ("fragmentIdSha256", "expectedDigest"):
        if isinstance(value.get(key), str) and re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value[key]):
            result[key] = value[key]
    def placement(raw: object) -> dict[str, object]:
        if not isinstance(raw, Mapping):
            return {}
        return {key: raw[key] for key in ("x", "y", "widthPt", "heightPt")
                if type(raw.get(key)) in (int, float) and math.isfinite(raw[key]) and abs(raw[key]) <= 1_000_000}
    result["expectedPlacementPt"] = placement(value.get("expectedPlacementPt"))
    tolerance = value.get("placementTolerancePt")
    if type(tolerance) in (int, float) and math.isfinite(tolerance) and 0 <= tolerance <= 1:
        result["placementTolerancePt"] = tolerance
    candidates = value.get("candidates")
    result["candidates"] = []
    for candidate in (candidates[:4] if isinstance(candidates, list) else []):
        if not isinstance(candidate, Mapping) or _count(candidate.get("imageIndex")) is None:
            continue
        item = {"imageIndex": candidate["imageIndex"], "placementPt": placement(candidate.get("placementPt"))}
        digest = candidate.get("digestSha256")
        if isinstance(digest, str) and re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest):
            item["digestSha256"] = digest
        result["candidates"].append(item)
    return result


def bounded_hybrid_timing(value: object) -> dict[str, object]:
    """Persist counters, never arbitrary worker diagnostics or source paths."""
    if not isinstance(value, Mapping) or value.get("schemaVersion") != "public-hybrid-timing/v1":
        return {}
    result: dict[str, object] = {"schemaVersion": "public-hybrid-timing/v1"}
    for key in ("setupMillis", "fontEmbeddingMillis", "conversionWallMillis", "assemblyMillis",
                "writeAndVerificationMillis", "wallMillis", "slidesNotAttempted"):
        count = _count(value.get(key))
        if count is not None:
            result[key] = count
    slides = value.get("slides")
    if isinstance(slides, list):
        result["slides"] = [
            {"n": item["n"], "conversionMillis": item["conversionMillis"],
             **({"recovery": recovery} if (recovery := _bounded_slide_recovery(item.get("recovery"))) else {})}
            for item in slides[:100] if isinstance(item, Mapping)
            and type(item.get("n")) is int and 1 <= item["n"] <= 100
            and _count(item.get("conversionMillis")) is not None
        ]
    return result


def _bounded_slide_recovery(value: object) -> dict[str, object]:
    """Retain retry outcomes without persisting browser logs or source content."""
    if not isinstance(value, Mapping) or type(value.get("attempts")) is not int or value["attempts"] not in (1, 2):
        return {}
    result: dict[str, object] = {"attempts": value["attempts"]}
    for key in ("firstFailureCode", "finalFailureCode"):
        code = value.get(key)
        if isinstance(code, str) and _CODE.fullmatch(code):
            result[key] = code
    if type(value.get("cleanupFailure")) is bool:
        result["cleanupFailure"] = value["cleanupFailure"]
    if value.get("retryDisposition") in (
        "budget-insufficient", "non-transient", "attempted", "recovered", "returned-fallback", "failed"
    ):
        result["retryDisposition"] = value["retryDisposition"]
    return result


def bounded_hybrid_editability(value: object) -> dict[str, object]:
    """Keep per-page counts and stable reasons, not body text or target ids."""
    if not isinstance(value, Mapping) or value.get("schemaVersion") != EDITABILITY_SCHEMA:
        return {}
    if value.get("verdict") not in _EDITABILITY_VERDICTS:
        return {}
    result: dict[str, object] = {"schemaVersion": EDITABILITY_SCHEMA, "verdict": value["verdict"]}
    result["requiredByRole"] = _bounded_required_by_role(value.get("requiredByRole"))
    for key in _EDITABILITY_COUNTS:
        count = _count(value.get(key))
        if count is not None:
            result[key] = count
    pages = value.get("imageOnlyPages")
    if isinstance(pages, list):
        result["imageOnlyPages"] = sorted({n for n in pages if type(n) is int and 1 <= n <= 100})
    slides = value.get("slides")
    bounded: list[dict[str, object]] = []
    if isinstance(slides, list):
        for slide in slides[:100]:
            if not isinstance(slide, Mapping):
                continue
            number = slide.get("slideNumber")
            if type(number) is not int or not 1 <= number <= 100 or slide.get("verdict") not in _EDITABILITY_VERDICTS:
                continue
            entry: dict[str, object] = {"slideNumber": number, "verdict": slide["verdict"]}
            entry["requiredByRole"] = _bounded_required_by_role(slide.get("requiredByRole"))
            for key in _EDITABILITY_COUNTS:
                count = _count(slide.get(key))
                if count is not None:
                    entry[key] = count
            for key in ("imageOnly", "photoOnlyExempt", "fullSlideRaster"):
                if type(slide.get(key)) is bool:
                    entry[key] = slide[key]
            reasons = slide.get("reasons")
            if isinstance(reasons, list):
                entry["reasons"] = sorted({code for code in reasons if isinstance(code, str) and _CODE.fullmatch(code)})[:32]
            bounded.append(entry)
    result["slides"] = bounded
    # Export itself does not perform an interactive PowerPoint edit.
    result["powerpointInteraction"] = {"status": "not-run"}
    return result


def _bounded_required_by_role(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {
        role: {key: counts[key] for key in ("total", "native", "rasterized", "missing", "occluded", "unknown")
               if _count(counts.get(key)) is not None}
        for role in ("title", "body", "tableCell", "supportedShape")
        if isinstance((counts := value.get(role)), Mapping)
    }


__all__ = [
    "HYBRID_QUALITY_SUMMARY_SCHEMA",
    "MAX_HYBRID_SLIDE_FALLBACKS",
    "bounded_hybrid_quality_summary",
    "bounded_hybrid_worker_evidence",
]
