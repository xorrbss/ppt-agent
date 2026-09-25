"""`pixel_text_by_cause`: per-deck breakdown of ALL pixel-kept text chars by
WHY they were kept as pixels, computed entirely from `analysis_result.json`
(the same single source and per-region char-counting method as
`editable_text_ratio_body_excl_mockup` -- see `mockup_regions.py`) plus,
only for `page_or_group_fallback`, `slide_evidence_report.json` (the
analysis never carries that category -- see below).

Categories (2026-09-25 follow-up):
  mark_disagreement      -- structure recognized this as text but couldn't
                            confidently transcribe/bound it
  number_unconfirmed     -- a targeted number re-read never confirmed
  table                  -- a table region kept as one picture (any reason)
  mockup_screenshot      -- STRUCTURE's own screenshot/UI-mock classification
                            (role=="screenshot" and not editable_fallback;
                            same set as `mockup_regions.mockup_audit`)
  page_or_group_fallback -- a later, render-time density fallback
                            (page-content / dense_group_pixel_fallback, see
                            `services/source_content/raster_redesign_fallback.py`)
                            -- NEVER appears as an analysis region (it's
                            synthesized during redesign layout, AFTER
                            analysis), so this category's `chars` is always
                            `None` here; only a `region_count`, read from
                            `slide_evidence_report.json`'s own
                            `advisories_by_code["dense_group_pixel_fallback"]`
                            totals when that file is available.
  other                  -- any `review_reason` not mapped below (the raw
                            codes are tracked in `other_raw_reasons`)

`REASON_CATEGORY` below maps every `review_reason` actually observed on a
pixel-kept region across the 5 real baseline decks on 2026-09-25 (deck1-4 +
a large external reference deck), plus a few same-family codes from
`services/source_content/*.py` that weren't observed in that specific run
but are the same kind of signal -- see README "Pixel text by cause" for the
worked per-deck numbers this mapping produces.

Two rules take priority over the `review_reason` lookup, since they're
determined by STRUCTURE/role, not by why a region was pixelated:
  1. `role == "screenshot" and not editable_fallback` -> `mockup_screenshot`
     (ignores `review_reason`, which is usually `None` there anyway).
  2. `role == "table"` -> `table` (a pixel-kept table is "table" no matter
     which reason code got it there -- observed on the real baseline: one
     region carries `role="table"` with `review_reason="crop_text_boundary_line"`,
     which would otherwise map to `mark_disagreement`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

REASON_CATEGORY = {
    # structure recognized this as text but couldn't confidently
    # transcribe/bound it -- the dominant category on every real deck
    "low_confidence_text_pixels": "mark_disagreement",
    "unreadable_text_pixels": "mark_disagreement",
    "unreadable_glyphs": "mark_disagreement",
    "uncovered_ink": "mark_disagreement",
    "crop_text_boundary": "mark_disagreement",
    "crop_text_boundary_line": "mark_disagreement",
    "crop_text_boundary_card": "mark_disagreement",
    "crop_edge_line": "mark_disagreement",
    # a targeted number re-read never confirmed
    "unconfirmed_number": "number_unconfirmed",
    # redundant with the role=="table" rule in _categorize below (kept for
    # completeness/robustness against a review_reason landing on a region
    # whose role isn't "table" for some other reason)
    "table_unstructured": "table",
    "table_cells_partially_unverified": "table",
    "table_cell_ocr_low_confidence": "table",
    # a whole-page structuring failure, if it ever reaches a region's own
    # review_reason (not observed there as of 2026-09-25; page-level
    # render-time fallbacks are the usual source of this category, see
    # module docstring)
    "page_structuring_failed": "page_or_group_fallback",
}

CATEGORIES = ("mark_disagreement", "number_unconfirmed", "table", "mockup_screenshot",
              "page_or_group_fallback", "other")

FALLBACK_ADVISORY_CODE = "dense_group_pixel_fallback"


def _region_ocr_text(region: dict, ocr_inventory: list) -> str:
    indices = region.get("ocr_token_indices") or []
    words = [
        ocr_inventory[i]["text"] for i in indices
        if 0 <= i < len(ocr_inventory) and ocr_inventory[i].get("text")
    ]
    return " ".join(words) if words else (region.get("text") or "")


def _char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _is_pixel_kept(region: dict) -> bool:
    """Same condition as `services/source_content/raster_structure.py`'s
    `is_pixel_region()` (`role in PIXEL_ROLES or (role == "table" and
    table_cells is None) or editable_fallback`), reimplemented against the
    analysis JSON shape rather than the live `RasterRegion` model."""
    role = region.get("role")
    if role in ("screenshot", "logo", "decoration", "full_page_preserve", "source_reference"):
        return True
    if role == "table" and region.get("table_cells") is None:
        return True
    return bool(region.get("editable_fallback"))


def _categorize(region: dict) -> str:
    role = region.get("role")
    if role == "screenshot" and not region.get("editable_fallback"):
        return "mockup_screenshot"
    if role == "table":
        return "table"
    return REASON_CATEGORY.get(region.get("review_reason"), "other")


def pixel_text_by_cause(analysis: Optional[dict], evidence: Optional[dict] = None) -> dict:
    """Returns `{"categories": {cat: {"chars": int|None, "region_count": int}},
    "other_raw_reasons": {code: chars}}`. `evidence` is
    `slide_evidence_report.json`'s own parsed dict (optional); when given
    and the analysis itself carried no `page_or_group_fallback` regions
    (the normal case -- see module docstring), that category's
    `region_count` is filled from `advisories_by_code["dense_group_pixel_fallback"]`
    totals across all slides, with `chars` left `None` (not derivable from
    that source)."""
    categories = {c: {"chars": 0, "region_count": 0} for c in CATEGORIES}
    other_raw: dict = {}
    if analysis:
        for page in analysis.get("pages", []):
            inventory = page.get("ocr_inventory", [])
            for region in page.get("regions", []):
                if not _is_pixel_kept(region):
                    continue
                chars = _char_count(_region_ocr_text(region, inventory))
                if chars <= 0:
                    continue
                cat = _categorize(region)
                categories[cat]["chars"] += chars
                categories[cat]["region_count"] += 1
                if cat == "other":
                    reason = region.get("review_reason")
                    other_raw[reason] = other_raw.get(reason, 0) + chars
    if categories["page_or_group_fallback"]["region_count"] == 0 and evidence:
        fallback_count = sum(
            (s.get("advisories_by_code") or {}).get(FALLBACK_ADVISORY_CODE, 0)
            for s in evidence.get("slides", [])
        )
        if fallback_count:
            categories["page_or_group_fallback"] = {"chars": None, "region_count": fallback_count}
    return {"categories": categories, "other_raw_reasons": other_raw}


def load_evidence(evidence_path: Optional[Path]) -> Optional[dict]:
    import json

    if not evidence_path or not Path(evidence_path).exists():
        return None
    return json.loads(Path(evidence_path).read_text(encoding="utf-8"))
