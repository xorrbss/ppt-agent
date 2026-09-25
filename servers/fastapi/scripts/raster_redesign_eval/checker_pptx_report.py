"""Full-deck PPTX check against OCR source-page fixtures (`checker` kind in
suite_config.json -- used for a large, externally-configured reference deck
where there is no exact python-pptx ground truth, only OCR text).

Split out of the original checker.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; scoring logic is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation

from checker_slide import (
    LOW_RECALL_THRESHOLD,
    MIN_FONT_PT,
    check_canvas_size,
    slide_editable_text_stats,
    slide_pictures,
    slide_tables,
)
from checker_text import char_recall, numeric_recall, scan_suspicious_fragments, slide_text
from imghash import ahash_bits


def _resolve_source_image_path(fixtures_dir: Path, page: dict) -> Path:
    """`source_ground_truth.json.pages[*].image_path` is an ABSOLUTE path baked
    in at fixture-build time (e.g. a session-local scratch dir that may no
    longer exist once the fixture is copied to a durable location). Fall back
    to `<fixtures_dir>/source_pages/<NN>.png` -- where fixtures are actually
    shipped from in this suite -- when the stored path doesn't resolve.
    Scoring logic itself (hashing, matching, thresholds) is unchanged."""
    stored = Path(page["image_path"])
    if stored.exists():
        return stored
    candidate = fixtures_dir / "source_pages" / stored.name
    if candidate.exists():
        return candidate
    return fixtures_dir / "source_pages" / f"{page['page_number']:02d}.png"


def check_pptx(pptx_path: Path, fixtures_dir: Path, expected_page_count: int = 28) -> dict:
    ground_truth = json.loads((fixtures_dir / "source_ground_truth.json").read_text(encoding="utf-8"))
    source_pages = ground_truth["pages"]
    source_hashes = []
    source_page_paths = {}
    for page in source_pages:
        resolved = _resolve_source_image_path(fixtures_dir, page)
        img_bytes = resolved.read_bytes()
        source_hashes.append((page["page_number"], ahash_bits(img_bytes)))
        source_page_paths[page["page_number"]] = str(resolved)

    prs = Presentation(str(pptx_path))
    canvas = check_canvas_size(prs.slide_width, prs.slide_height)

    slides_report = []
    for idx, slide in enumerate(prs.slides, start=1):
        text = slide_text(slide)
        text_stats = slide_editable_text_stats(slide)
        tables = slide_tables(slide)
        pictures = slide_pictures(slide, prs.slide_width, prs.slide_height, source_hashes, source_page_paths)

        # best-matching source page by character recall (fallback ordering check)
        best_match, best_recall = None, -1.0
        for page in source_pages:
            r = char_recall(page["ocr_text_normalized"], text)["recall"] or 0.0
            if r > best_recall:
                best_recall = r
                best_match = page["page_number"]
        # if a full-page copy picture matched a source page directly, prefer that
        full_page_matches = [p for p in pictures if p.get("is_full_page_copy")]
        if full_page_matches:
            best_match = full_page_matches[0]["best_matching_source_page"]

        expected_page = source_pages[idx - 1]
        fidelity_char = char_recall(expected_page["ocr_text_normalized"], text)
        fidelity_numbers = numeric_recall(expected_page["numeric_tokens"], text)

        font_sizes = text_stats["font_sizes_pt"]
        min_font = min(font_sizes) if font_sizes else None
        suspicious = scan_suspicious_fragments(text)

        slides_report.append(
            {
                "slide_number": idx,
                "editable_text_shapes": text_stats["text_shapes"],
                "editable_text_chars": text_stats["chars"],
                "runs_without_explicit_font_size": text_stats["runs_without_explicit_size"],
                "min_explicit_font_pt": min_font,
                "runs_below_9_5pt": sum(1 for s in font_sizes if s < MIN_FONT_PT),
                "native_tables": tables,
                "pictures": pictures,
                "full_page_copy_count": sum(1 for p in pictures if p.get("is_full_page_copy")),
                "page_content_crop_count": sum(1 for p in pictures if p.get("is_page_content_crop")),
                "best_matching_source_page_by_content": best_match,
                "expected_source_page": idx,
                "order_matches_source": best_match == idx,
                "fidelity_char_recall_vs_expected_page": fidelity_char,
                "fidelity_numeric_recall_vs_expected_page": fidelity_numbers,
                "suspicious_fragments": suspicious,
            }
        )

    total_full_page_copies = sum(s["full_page_copy_count"] for s in slides_report)
    total_page_content_crops = sum(s["page_content_crop_count"] for s in slides_report)
    page_content_crop_slides = [s["slide_number"] for s in slides_report if s["page_content_crop_count"] > 0]
    total_editable_text_shapes = sum(s["editable_text_shapes"] for s in slides_report)
    total_editable_chars = sum(s["editable_text_chars"] for s in slides_report)
    total_native_tables = sum(len(s["native_tables"]) for s in slides_report)
    slides_with_zero_text = sum(1 for s in slides_report if s["editable_text_shapes"] == 0)
    order_mismatches = [s["slide_number"] for s in slides_report if not s["order_matches_source"]]
    all_font_sizes = [s["min_explicit_font_pt"] for s in slides_report if s["min_explicit_font_pt"] is not None]
    deck_min_font_pt = min(all_font_sizes) if all_font_sizes else None
    slides_with_subminimum_font = [s["slide_number"] for s in slides_report if s["runs_below_9_5pt"] > 0]
    total_suspicious_fragments = sum(len(s["suspicious_fragments"]) for s in slides_report)
    slides_with_suspicious_fragments = [s["slide_number"] for s in slides_report if s["suspicious_fragments"]]

    char_recalls = [
        s["fidelity_char_recall_vs_expected_page"]["recall"]
        for s in slides_report
        if s["fidelity_char_recall_vs_expected_page"]["recall"] is not None
    ]
    numeric_recalls = [
        s["fidelity_numeric_recall_vs_expected_page"]["recall"]
        for s in slides_report
        if s["fidelity_numeric_recall_vs_expected_page"]["recall"] is not None
    ]

    return {
        "pptx_path": str(pptx_path),
        "page_count": len(slides_report),
        "expected_page_count": expected_page_count,
        "page_count_ok": len(slides_report) == expected_page_count,
        "canvas": canvas,
        "slides": slides_report,
        "summary": {
            "total_full_page_copies": total_full_page_copies,
            "total_page_content_crops": total_page_content_crops,
            "page_content_crop_slides": page_content_crop_slides,
            "total_editable_text_shapes": total_editable_text_shapes,
            "total_editable_text_chars": total_editable_chars,
            "total_native_tables": total_native_tables,
            "slides_with_zero_editable_text": slides_with_zero_text,
            "order_mismatch_slides": order_mismatches,
            "deck_min_explicit_font_pt": deck_min_font_pt,
            "slides_with_font_below_9_5pt": slides_with_subminimum_font,
            "mean_char_recall": sum(char_recalls) / len(char_recalls) if char_recalls else None,
            "min_char_recall": min(char_recalls) if char_recalls else None,
            "mean_numeric_recall": sum(numeric_recalls) / len(numeric_recalls) if numeric_recalls else None,
            "min_numeric_recall": min(numeric_recalls) if numeric_recalls else None,
            "low_char_recall_slides": [
                s["slide_number"] for s in slides_report
                if (s["fidelity_char_recall_vs_expected_page"]["recall"] or 0) < LOW_RECALL_THRESHOLD
            ],
            "low_numeric_recall_slides": [
                s["slide_number"] for s in slides_report
                if (s["fidelity_numeric_recall_vs_expected_page"]["recall"] or 0) < LOW_RECALL_THRESHOLD
            ],
            "total_suspicious_fragments": total_suspicious_fragments,
            "slides_with_suspicious_fragments": slides_with_suspicious_fragments,
        },
    }
