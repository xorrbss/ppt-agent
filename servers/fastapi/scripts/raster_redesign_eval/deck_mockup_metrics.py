"""`editable_text_ratio_body_excl_mockup` computation for both checker kinds.

Split out of deck_scoring.py (2026-09-25 follow-up) to keep that module
under the repo's file-size limit; logic is unchanged from what deck_scoring.py
had inline.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from mockup_regions import (
    analysis_only_editable_ratio,
    load_analysis_result,
    matched_gt_chars,
    mockup_summary,
    mockup_texts_by_page,
    validate_ratio,
)


def _norm_len(text: Optional[str]) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def mockup_audit_fields(analysis: Optional[dict]) -> dict:
    """The audit-facing fields shared by both deck kinds (region count,
    total chars, per-region list) -- always from `mockup_summary`, never
    affected by which ratio formula is used. `None`/empty when there's no
    analysis_result.json for this run."""
    if analysis is None:
        return {"mockup_regions_count": None, "mockup_text_chars": None, "mockup_regions": [],
                "mockup_analysis_available": False}
    summary = mockup_summary(analysis)
    return {
        "mockup_regions_count": summary["mockup_regions_count"],
        "mockup_text_chars": summary["mockup_text_chars"],
        "mockup_regions": summary["mockup_regions"],
        "mockup_analysis_available": True,
    }


def excl_mockup_metrics_ground_truth(pptx_path: Path, page_source_editable: list) -> dict:
    """`editable_text_ratio_body_excl_mockup` for `ground_truth_checker`
    decks (deck1-4). `page_source_editable`: (page_number, gt_text,
    editable_text_chars) per slide (page_number == the output slide's own
    page_number; this checker assumes positional identity, see
    `score_deck_ground_truth`).

    2026-09-25 fix: the mockup subtraction used to be each mockup region's
    own (separately-OCR'd, from the raster analysis) char count, taken at
    face value against `source_char_count` (the EXACT ground-truth char
    count) -- two different sources of truth for the same page, which could
    disagree enough to drive the denominator negative (or, after the first
    clamp-with-continue fix, produce a >1.0 ratio when the same thing
    happened on the checker-kind path). Now the subtraction is
    `matched_gt_chars`: how many of the page's own EXACT GT characters are
    actually accounted for by the mockup regions' OCR text (a multiset
    intersection against the GT text itself, consumed once across all of
    the page's mockup regions) -- bounded to `len(gt_text)` by construction,
    so `gt_chars - matched <= gt_chars` and can never go negative. The
    numerator (`editable_text_chars`, measured on the OUTPUT pptx) can
    still in principle exceed that denominator -- unrelated to mockup
    content: `editable_text_chars` counts every text shape on the OUTPUT
    slide, which can exceed the page's exact GT char count even with ZERO
    mockup regions present (observed on the real baseline: deck1 page 2,
    editable=256 vs gt=234, no mockup region on that page at all -- a
    pre-existing characteristic of `editable_text_ratio_body_mean` too,
    just never asserted there). `validate_ratio` turns this into a clear,
    per-page error rather than a silently-impossible ratio; when it fires
    for any page, the WHOLE deck's `editable_text_ratio_body_excl_mockup`
    is reported as `None` with the violating pages listed in
    `editable_text_ratio_body_excl_mockup_errors` -- visible as data in the
    score dict rather than aborting the rest of this deck's scoring (wrong
    numbers, number-gate, ...), which don't depend on this metric."""
    analysis = load_analysis_result(pptx_path.parent)
    result = mockup_audit_fields(analysis)
    if analysis is None:
        result["editable_text_ratio_body_excl_mockup"] = None
        result["editable_text_ratio_body_excl_mockup_errors"] = []
        return result
    mockup_texts = mockup_texts_by_page(analysis)
    ratios, errors = [], []
    for page_number, gt_text, editable in page_source_editable:
        gt_chars = _norm_len(gt_text)
        subtracted = matched_gt_chars(mockup_texts.get(page_number, []), gt_text)
        denom = gt_chars - subtracted
        if denom <= 0:
            continue
        try:
            ratios.append(validate_ratio(editable / denom, context=f"ground_truth page {page_number}"))
        except ValueError as exc:
            errors.append({
                "page": page_number, "editable_text_chars": editable, "gt_chars": gt_chars,
                "mockup_chars_subtracted": subtracted, "denominator": denom, "detail": str(exc),
            })
    result["editable_text_ratio_body_excl_mockup"] = (
        None if errors else ((sum(ratios) / len(ratios)) if ratios else None)
    )
    result["editable_text_ratio_body_excl_mockup_errors"] = errors
    return result


def excl_mockup_metrics_checker(pptx_path: Path) -> dict:
    """`editable_text_ratio_body_excl_mockup` for `checker`-kind decks (OCR
    ground truth, e.g. a large external reference deck): computed ENTIRELY
    from the analysis's own region classification (see
    `mockup_regions.analysis_only_editable_ratio`), never cross-referencing
    the output pptx's own measured editable-char count or the separately
    OCR'd `source_ground_truth.json` fixture -- mixing those was the root
    cause of a >1.0 mean ratio (1.193 observed on the real baseline).
    Trade-off: this measures what STRUCTURE decided at analysis time, not
    what actually survived to the exported pptx (a later render-time
    page-content/dense-group fallback, tracked separately via
    `page_content_fallback_pages_product`, isn't reflected here)."""
    analysis = load_analysis_result(pptx_path.parent)
    result = mockup_audit_fields(analysis)
    computed = analysis_only_editable_ratio(analysis)
    result["editable_text_ratio_body_excl_mockup"] = computed["ratio"] if computed else None
    return result
