"""Mock-up / screenshot region exclusion for the editable-text-ratio metric.

The raster analysis result (see `services/source_content/raster_contracts.py`,
`RasterRegion`) records, per structurally-classified content region, whether
STRUCTURE decided it is genuinely non-text content -- a screenshot/UI mock/
image (`role == "screenshot"` with `editable_fallback == False`, set only by
`_screenshot()` in `services/source_content/raster_structure_emit.py` during
analysis) -- as opposed to text that structure recognized as text but had to
render as pixels for fidelity reasons (`editable_fallback == True`; reasons
include `paragraph_pixels`, `line_pixels`, `low_confidence_text_pixels`,
`unreadable_glyphs`, `unconfirmed_number`) or a later, render-time density
fallback (`page-content` / `dense_group_pixel_fallback`, from
`services/source_content/raster_redesign_fallback.py` -- applied during
redesign layout, AFTER analysis, so it never appears as a region in the
analysis result at all).

This distinction reuses the exact same `role == "screenshot" and not
editable_fallback` condition already used pervasively across the raster
services (see `raster_boundary.py`'s / `raster_crop_edges.py`'s own
`real_capture = crop.role == "screenshot" and not crop.editable_fallback`) --
it is not a new heuristic, just the existing structural signal read from the
saved analysis.

`editable_text_ratio_body_excl_mockup` (in `deck_scoring.py`) removes only
this first kind's source text from the denominator: a deck that legitimately
keeps a real screenshot as a picture should not be penalized for it not
being "editable text"; everything the product structurally recognized as
text -- however it was ultimately rendered -- stays in the denominator
exactly as before.

Char counts for a mockup region come from the analysis's own OCR word boxes
(`page.ocr_inventory`, indexed by `region.ocr_token_indices`) -- never an
area-proportion guess.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


def load_analysis_result(run_dir: Path) -> Optional[dict]:
    """`run_dir` is a deck's own output directory (same dir as its pptx/pdf),
    written by `generate_raster_deck.py`'s `save_analysis_result()`. Returns
    None when no analysis_result.json exists there (older runs, or
    --skip-generate runs that never reached generation)."""
    path = Path(run_dir) / "analysis_result.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _is_mockup_region(region: dict) -> bool:
    return region.get("role") == "screenshot" and not region.get("editable_fallback", False)


def _region_char_count(region: dict, ocr_inventory: list[dict]) -> int:
    indices = region.get("ocr_token_indices") or []
    words = [
        ocr_inventory[i]["text"] for i in indices
        if 0 <= i < len(ocr_inventory) and ocr_inventory[i].get("text")
    ]
    text = " ".join(words) if words else (region.get("text") or "")
    return len(re.sub(r"\s+", "", text))


def mockup_audit(analysis: dict) -> list[dict]:
    """Per structurally-classified mockup region: page, region id, bbox
    (source EMU, from the analysis's own RasterBox), reason (`review_reason`
    if the region carries one, else a fixed label), and its OCR-derived char
    count -- enough for an auditor to crop and eyeball each one."""
    audit = []
    for page in analysis.get("pages", []):
        inventory = page.get("ocr_inventory", [])
        for region in page.get("regions", []):
            if not _is_mockup_region(region):
                continue
            audit.append({
                "page": page.get("page_number"),
                "region_id": region.get("region_id"),
                "bbox": region.get("bbox"),
                "reason": region.get("review_reason") or "structure_screenshot",
                "char_count": _region_char_count(region, inventory),
            })
    return audit


def mockup_summary(analysis: dict) -> dict:
    audit = mockup_audit(analysis)
    chars_by_page: dict[int, int] = {}
    for entry in audit:
        chars_by_page[entry["page"]] = chars_by_page.get(entry["page"], 0) + entry["char_count"]
    return {
        "mockup_regions_count": len(audit),
        "mockup_text_chars": sum(entry["char_count"] for entry in audit),
        "mockup_regions": audit,
        "mockup_chars_by_page": chars_by_page,
    }
