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

**2026-09-25 bug fix:** `analysis_result.json` on disk is the FULL
`GET /api/v1/ppt/source-content/raster/analysis/{operation_id}` response
envelope (`{"analysis": {...pages...}, "operation_id": ..., "status": ...,
...}`), not the bare `RasterAnalysis` payload -- confirmed against real
files fetched from the endpoint (`eval-runs/baseline-2144f5034-r2/{deck1,
deck2, deck3, <external-deck>}/analysis_result.json`,
`eval-runs/baseline-2144f5034/deck4/analysis_result.json`) and matching
`generate_raster_deck.py`'s own `save_analysis_result(out_dir, ready)`
(which now saves the whole endpoint response, not just `ready["analysis"]`
-- see that module). The original version of this function returned the
envelope unchanged, so every `.get("pages", [])` call below silently saw an
empty list -- `mockup_regions_count` was always 0. `load_analysis_result()`
now unwraps `data["analysis"]` when present (falling back to `data` itself,
for any caller that already unwrapped it, e.g. a hand-built payload). Also
matches `mockup_audit/make_crops.py`'s own extraction exactly: only a
mockup region with `char_count > 0` is counted (a region whose
`ocr_token_indices` cover nothing, or the crop's own `text` is empty, adds
nothing to the ratio and isn't useful to audit).

**2026-09-25 second fix -- impossible ratio (>1.0).** `editable_text_ratio_body_excl_mockup`
used to mix two independent OCR passes: the numerator (`editable_text_chars`)
came from measuring the OUTPUT pptx's own text shapes, while the mockup
subtraction came from the ANALYSIS's OCR reading of the SOURCE image. On a
mockup-dense page these two disagree enough (different OCR pass, sometimes
different page-order correspondence) that the denominator could shrink
below the numerator, producing a mean ratio of 1.193 on the real large
external reference deck. `analysis_only_editable_ratio()` fixes this for
checker-kind decks by computing BOTH sides from the SAME source -- the
analysis's own OCR char counts, region by region -- so the numerator is
always a strict subset of the denominator and `0 <= ratio <= 1` holds by
construction, not by clamping (still asserted defensively, see
`validate_ratio`). `deck_scoring.py` uses the analogous ground-truth-side
fix (`_matched_gt_chars`, matched-and-clamped mockup subtraction) for
deck1-4; see its own docstring.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

_TEXT_ROLES = ("title", "body", "table", "unknown")


def validate_ratio(ratio: float, *, context: str, tolerance: float = 1e-9) -> float:
    """Raises ValueError (a clear, debuggable failure) if `ratio` falls
    outside [0, 1] by more than floating-point noise; otherwise clamps that
    noise away. An out-of-bounds ratio means the numerator/denominator
    aren't actually drawn from a consistent subset/superset relationship --
    a measurement bug, not a real number to report."""
    if not (-tolerance <= ratio <= 1 + tolerance):
        raise ValueError(
            f"editable_text_ratio_body_excl_mockup out of bounds ({context}): ratio={ratio!r} "
            f"-- numerator/denominator are not from a consistent source; see mockup_regions.py"
        )
    return max(0.0, min(1.0, ratio))


def load_analysis_result(run_dir: Path) -> Optional[dict]:
    """`run_dir` is a deck's own output directory (same dir as its pptx/pdf),
    written by `generate_raster_deck.py`'s `save_analysis_result()`. Returns
    None when no analysis_result.json exists there (older runs, or
    --skip-generate runs that never reached generation). Unwraps the
    `GET .../analysis/{operation_id}` response envelope
    (`{"analysis": {...}, "operation_id": ..., ...}`) to the bare
    `RasterAnalysis` dict (`{"pages": [...], ...}`); a file that's already
    just the bare analysis (no "analysis" key) is returned as-is."""
    path = Path(run_dir) / "analysis_result.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    analysis = data.get("analysis")
    return analysis if isinstance(analysis, dict) else data


def _is_mockup_region(region: dict) -> bool:
    return region.get("role") == "screenshot" and not region.get("editable_fallback", False)


def _region_ocr_text(region: dict, ocr_inventory: list[dict]) -> str:
    indices = region.get("ocr_token_indices") or []
    words = [
        ocr_inventory[i]["text"] for i in indices
        if 0 <= i < len(ocr_inventory) and ocr_inventory[i].get("text")
    ]
    return " ".join(words) if words else (region.get("text") or "")


def _region_char_count(region: dict, ocr_inventory: list[dict]) -> int:
    return len(re.sub(r"\s+", "", _region_ocr_text(region, ocr_inventory)))


def mockup_texts_by_page(analysis: dict) -> dict:
    """Page number -> list of mockup regions' own OCR text (only
    `char_count > 0`, same filter as `mockup_audit`). Used by
    `deck_scoring.py`'s ground-truth-decks path to match each mockup
    region's text against the page's exact GT text (see
    `_matched_gt_chars` there), rather than trusting the mockup region's
    own (separately-OCR'd) char count against a differently-sourced GT
    char total."""
    result: dict[int, list[str]] = {}
    for page in analysis.get("pages", []):
        inventory = page.get("ocr_inventory", [])
        texts = []
        for region in page.get("regions", []):
            if not _is_mockup_region(region):
                continue
            text = _region_ocr_text(region, inventory)
            if len(re.sub(r"\s+", "", text)) > 0:
                texts.append(text)
        if texts:
            result[page.get("page_number")] = texts
    return result


def analysis_only_editable_ratio(analysis: Optional[dict]) -> Optional[dict]:
    """`editable_text_ratio_body_excl_mockup` for checker-kind decks,
    computed ENTIRELY from the analysis's own region classification and OCR
    char counts -- never cross-referencing the output pptx's own measured
    editable-char count or a separately-OCR'd ground-truth fixture (see
    module docstring, "second fix"). Per analysis page:

      numerator = chars of regions STRUCTURE published as editable text
                  (role in title/body/table/unknown, editable_fallback False)
      denominator = chars of every OCR-text-bearing region on the page
                    (any role) MINUS this page's mockup (structure-kept
                    screenshot) chars

    Both sides use the exact same per-region char count
    (`_region_char_count`), so numerator is always a subset of denominator:
    0 <= ratio <= 1 holds by construction. Returns None when `analysis` is
    None (no analysis_result.json for this run)."""
    if not analysis:
        return None
    ratios = []
    for page in analysis.get("pages", []):
        inventory = page.get("ocr_inventory", [])
        numerator = all_text_bearing = mockup = 0
        for region in page.get("regions", []):
            chars = _region_char_count(region, inventory)
            if chars <= 0:
                continue
            all_text_bearing += chars
            role = region.get("role")
            fallback = region.get("editable_fallback", False)
            if role == "screenshot" and not fallback:
                mockup += chars
            elif role in _TEXT_ROLES and not fallback:
                numerator += chars
        denom = all_text_bearing - mockup
        if denom > 0:
            ratio = validate_ratio(numerator / denom, context=f"analysis page {page.get('page_number')}")
            ratios.append(ratio)
    return {
        "ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "pages_measured": len(ratios),
    }


def mockup_audit(analysis: dict) -> list[dict]:
    """Per structurally-classified mockup region with actual OCR-derived
    text (`char_count > 0` -- see module docstring): page, region id, bbox
    (source EMU, from the analysis's own RasterBox), reason (the region's
    own `review_reason`, usually `None` for a genuine structural screenshot
    -- matches `mockup_audit/make_crops.py`'s own `rows.json` exactly), and
    its char count -- enough for an auditor to crop and eyeball each one."""
    audit = []
    for page in analysis.get("pages", []):
        inventory = page.get("ocr_inventory", [])
        for region in page.get("regions", []):
            if not _is_mockup_region(region):
                continue
            char_count = _region_char_count(region, inventory)
            if char_count <= 0:
                continue
            audit.append({
                "page": page.get("page_number"),
                "region_id": region.get("region_id"),
                "bbox": region.get("bbox"),
                "reason": region.get("review_reason"),
                "char_count": char_count,
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


def matched_gt_chars(mockup_texts: list, gt_text: str) -> int:
    """Ground-truth-decks (deck1-4) mockup subtraction: how many of `gt_text`'s
    own characters are accounted for by this page's mockup regions' OCR
    text, via a multiset (Counter) intersection consumed once across all of
    the page's mockup regions (so two mockup regions can't both claim the
    same GT character). Bounded by construction to `len(gt_text)` -- it is
    a running subtraction FROM a fixed GT character pool, never an
    independently-sourced count that could exceed it. This replaces
    trusting each mockup region's own (separately-OCR'd) char count at face
    value against `source_char_count`, which could disagree enough with the
    exact GT text to drive `source_char_count - mockup_chars` negative."""
    remaining = Counter(re.sub(r"\s+", "", gt_text or ""))
    matched = 0
    for text in mockup_texts:
        text_counter = Counter(re.sub(r"\s+", "", text or ""))
        for ch, count in text_counter.items():
            available = remaining.get(ch, 0)
            take = min(available, count)
            if take:
                remaining[ch] = available - take
                matched += take
    return matched
