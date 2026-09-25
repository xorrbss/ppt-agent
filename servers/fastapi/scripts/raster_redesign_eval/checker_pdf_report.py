"""PDF-rendering checks: page geometry + exactly-once (OCR duplicate/missing/
word-cut) detection against source-page OCR fixtures.

Split out of the original checker.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; scoring logic is unchanged.

`check_pdf`/`check_exactly_once` need PyMuPDF (`fitz`), which is not a
project dependency (see ../README.md): run via
`uv run --with pymupdf python ...`.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pptx import Presentation

import exactly_once as eo
from checker_slide import A4_LANDSCAPE_MM, SIZE_TOLERANCE
from checker_text import slide_text
from imghash import image_pixel_size


def check_pdf(pdf_path: Path, report_dir: Path, label: str, expected_page_count: int = 28) -> dict:
    try:
        import fitz  # pymupdf
    except ImportError:
        return {"error": "pymupdf_not_available"}

    pages_dir = report_dir / f"{label}_pdf_pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc, start=1):
        rect = page.rect  # in points (1/72 in)
        width_mm = rect.width * 25.4 / 72
        height_mm = rect.height * 25.4 / 72
        target_w, target_h = A4_LANDSCAPE_MM
        is_a4_landscape = (
            abs(width_mm - target_w) / target_w <= SIZE_TOLERANCE
            and abs(height_mm - target_h) / target_h <= SIZE_TOLERANCE
        )
        pix = page.get_pixmap(dpi=150)
        png_path = pages_dir / f"{i:02d}.png"
        pix.save(str(png_path))
        pages.append(
            {
                "page_number": i,
                "width_mm": round(width_mm, 2),
                "height_mm": round(height_mm, 2),
                "is_a4_landscape": is_a4_landscape,
                "png_path": str(png_path),
            }
        )
    doc.close()
    return {
        "pdf_path": str(pdf_path),
        "page_count": len(pages),
        "expected_page_count": expected_page_count,
        "page_count_ok": len(pages) == expected_page_count,
        "pages": pages,
    }


def check_exactly_once(pptx_path: Path, pdf_pages: list[dict], fixtures_dir: Path) -> dict:
    """Objective exactly-once fidelity metric (2026-09-25 spec): per page,
    compare the visible token multiset (OCR of the rendered PDF page, plus
    the PPTX's own editable text as a structural cross-check) against the
    source page's OCR token multiset. See exactly_once.py for the full
    method and its calibration caveats (Korean OCR segmentation noise).

    `pdf_pages` is check_pdf()'s ["pages"] list (needs page_number + png_path).
    """
    ground_truth = json.loads((fixtures_dir / "source_ground_truth.json").read_text(encoding="utf-8"))
    source_tokens_by_page = {
        p["page_number"]: eo.normalize_tokens(p["ocr_text_normalized"]) for p in ground_truth["pages"]
    }
    prs = Presentation(str(pptx_path))
    png_by_page = {p["page_number"]: Path(p["png_path"]) for p in pdf_pages}

    per_page = []
    for idx, slide in enumerate(prs.slides, start=1):
        src = source_tokens_by_page.get(idx, Counter())
        png_path = png_by_page.get(idx)
        if png_path is None or not png_path.exists():
            continue
        ocr_tok, ocr_words = eo.ocr_tokens_and_words(png_path)
        text_tok = eo.pptx_tokens(slide_text(slide))
        pic_boxes = eo.picture_pixel_bboxes(
            slide, prs.slide_width, prs.slide_height,
            *image_pixel_size(png_path.read_bytes()),
        )
        per_page.append({
            "page_number": idx,
            "ocr_duplicated_tokens": eo.duplicated_tokens(src, ocr_tok),
            "pptx_text_duplicated_tokens": eo.duplicated_tokens(src, text_tok),
            "missing_long_tokens": eo.missing_long_tokens(src, ocr_tok),
            "word_cut_candidates": eo.word_cut_candidates(src, ocr_words, pic_boxes),
        })
    summary = {
        "pages_with_ocr_duplicates": [p["page_number"] for p in per_page if p["ocr_duplicated_tokens"]],
        "pages_with_pptx_text_duplicates": [p["page_number"] for p in per_page if p["pptx_text_duplicated_tokens"]],
        "pages_with_word_cut_candidates": [p["page_number"] for p in per_page if p["word_cut_candidates"]],
        "pages_with_high_confidence_cuts": [
            p["page_number"] for p in per_page
            if any(c["at_crop_edge"] for c in p["word_cut_candidates"])
        ],
        "note": (
            "Korean OCR word-segmentation varies between independent tesseract "
            "runs of visually similar text; duplicated_tokens uses a >=2-char "
            "floor to cut noise but still has a nonzero false-positive floor "
            "(measured ~4/page on a known-non-duplicated negative control). "
            "word_cut_candidates is high-recall/low-precision -- most hits are "
            "ordinary Korean verb/noun endings split differently by OCR, not "
            "real crop truncation; at_crop_edge=True does not reliably raise "
            "precision (spot-checked). Treat missing_long_tokens as the most "
            "reliable of the four signals."
        ),
    }
    return {"pages": per_page, "summary": summary}
