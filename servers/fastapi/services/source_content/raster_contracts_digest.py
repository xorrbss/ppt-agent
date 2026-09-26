"""Digest sealing and legacy-compatible digest verification for the raster
reconstruction analysis and its approval pin."""
from __future__ import annotations

import hashlib
import json

from services.source_content.raster_contracts_analysis import RasterAnalysis
from services.source_content.raster_contracts_base import RasterApprovalPin


def raster_digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Not content: measurement of how the analysis was produced. Excluding it keeps
# approval pins and fences independent of how long a run took.
_UNDIGESTED_ANALYSIS_FIELDS = frozenset({"digest", "stage_timings"})


def seal_analysis(analysis: RasterAnalysis | dict) -> RasterAnalysis:
    payload = analysis.model_dump(mode="json") if isinstance(analysis, RasterAnalysis) else analysis
    value = RasterAnalysis.model_validate(payload)
    digest = raster_digest(value.model_dump(mode="json", exclude=set(_UNDIGESTED_ANALYSIS_FIELDS)))
    return value.model_copy(update={"digest": digest})


_ADDITIVE_REGION_DEFAULTS = {
    "confidence": None, "reading_order": None, "table_cells": None, "review_reason": None,
    "ocr_token_indices": None, "editable_fallback": False, "group_id": None, "heading_line_count": 0,
}
_ADDITIVE_PAGE_DEFAULTS = {"reference_region": None, "unpromoted_ocr_indices": []}


def _strip_additive_defaults(payload: dict) -> dict | None:
    """Rebuild the pre-redesign serialization, or None if redesign data is present."""

    for page in payload["pages"]:
        for key, value in _ADDITIVE_PAGE_DEFAULTS.items():
            if page.get(key, value) != value:
                return None
            page.pop(key, None)
        for region in (*page["regions"], *((page["full_page_region"],) if page.get("full_page_region") else ())):
            for key, value in _ADDITIVE_REGION_DEFAULTS.items():
                if region.get(key, value) != value:
                    return None
                region.pop(key, None)
    return payload


def require_analysis_digest(analysis: RasterAnalysis) -> None:
    if analysis.digest and analysis.digest == seal_analysis(analysis).digest:
        return
    # Records sealed before the additive redesign fields existed.
    legacy = _strip_additive_defaults(analysis.model_dump(mode="json", exclude=set(_UNDIGESTED_ANALYSIS_FIELDS)))
    if legacy is None:
        raise ValueError("raster_analysis_digest_mismatch")
    if analysis.digest and analysis.digest == raster_digest(legacy):
        return
    if any(page["approval_method"] != "manual_review" for page in legacy["pages"]):
        raise ValueError("raster_analysis_digest_mismatch")
    for page in legacy["pages"]:
        page.pop("approval_method")
    if analysis.digest and analysis.digest == raster_digest(legacy):
        return
    for page in legacy["pages"]:
        defaults = {"render_mode": "reconstruct", "full_page_region": None,
                    "full_page_preserve_acknowledgements": [], "recommended_render_mode": None,
                    "recommendation_reason": None}
        if any(page.get(key) != value for key, value in defaults.items()):
            raise ValueError("raster_analysis_digest_mismatch")
        for key in defaults:
            page.pop(key, None)
    if not analysis.digest or analysis.digest != raster_digest(legacy):
        raise ValueError("raster_analysis_digest_mismatch")


def seal_approval(pin: RasterApprovalPin) -> RasterApprovalPin:
    digest = raster_digest(pin.model_dump(mode="json", exclude={"approval_digest"}))
    return pin.model_copy(update={"approval_digest": digest})


def approval_digest_matches(pin: RasterApprovalPin) -> bool:
    if pin.approval_digest and pin.approval_digest == seal_approval(pin).approval_digest:
        return True
    if pin.automatic_redesign_pages:
        return False
    # Pins sealed before automatic_redesign_pages existed.
    legacy = pin.model_dump(mode="json", exclude={"approval_digest", "automatic_redesign_pages"})
    if pin.approval_digest and pin.approval_digest == raster_digest(legacy):
        return True
    if pin.full_page_preserve_pages:
        return False
    oldest = pin.model_dump(
        mode="json", exclude={"approval_digest", "full_page_preserve_pages", "automatic_redesign_pages"}
    )
    return bool(pin.approval_digest) and pin.approval_digest == raster_digest(oldest)
