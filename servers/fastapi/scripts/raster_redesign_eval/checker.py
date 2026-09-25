"""Output checker for an auto_redesign PPTX/PDF pair, run against a source
deck's page images + OCR text fixtures (`fixtures_dir/source_ground_truth.json`
+ `fixtures_dir/source_pages/*.png`).

Verifies:
  - canvas size (A4 landscape) and page count/order
  - per-slide editable text (shape/char counts), native tables, pictures
  - whether any picture is actually a full-page copy of a source slide image
    (perceptual hash match covering most of the page -> the negative-control
    defect)
  - minimum explicit font size (>= 9.5pt requirement)
  - text fidelity: character-level recall + numeric-token recall of each
    output slide's editable text against the matched source page's OCR
    ground truth
  - (optional) PDF: page count/size, and renders each page to PNG for later
    visual review

This module is a thin facade: the actual checks live in checker_text.py
(text/number recall, slide text extraction), checker_slide.py (canvas size,
per-slide stats, picture hash matching) checker_pptx_report.py (full-deck
PPTX report), and checker_pdf_report.py (PDF + exactly-once report). Split
out of a single 559-line checker.py (2026-09-25 eval suite) to keep each
module under this repo's file-size limit; scoring logic is unchanged.

Usage (from servers/fastapi, so python-pptx/Pillow/numpy resolve; add
`uv run --with pymupdf` for the --pdf path):
  uv run python <this file> --pptx OUT.pptx [--pdf OUT.pdf] \
      --fixtures <fixtures-dir> --report-dir <dir> --label my_run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from checker_pdf_report import check_exactly_once, check_pdf
from checker_pptx_report import check_pptx
from checker_slide import (  # noqa: F401  (re-exported for callers that do `import checker as ck`)
    A4_LANDSCAPE_MM,
    A4_PORTRAIT_MM,
    CONTENT_CROP_AREA_MIN_FRACTION,
    CONTENT_CROP_HASH_MAX_DIST,
    EMU_PER_MM,
    FULL_PAGE_AREA_MIN_FRACTION,
    FULL_PAGE_HASH_MAX_DIST,
    LOW_RECALL_THRESHOLD,
    MIN_FONT_PT,
    PICTURE_SHAPE_TYPE,
    SIZE_TOLERANCE,
    check_canvas_size,
    slide_editable_text_stats,
    slide_pictures,
    slide_tables,
)
from checker_text import (  # noqa: F401  (re-exported for callers that do `import checker as ck`)
    NUMBER_RE,
    char_recall,
    normalize_whitespace,
    numeric_recall,
    scan_suspicious_fragments,
    slide_text,
)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pptx", required=True)
    parser.add_argument("--pdf", default=None)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--expected-page-count", type=int, default=28)
    parser.add_argument("--skip-exactly-once", action="store_true",
                         help="skip the OCR-based duplicate/missing/cut-word metric (slow: one "
                              "tesseract call per page)")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = Path(args.fixtures)

    report = {"label": args.label, "pptx": check_pptx(Path(args.pptx), fixtures_dir, args.expected_page_count)}
    if args.pdf:
        report["pdf"] = check_pdf(Path(args.pdf), report_dir, args.label, args.expected_page_count)
        if not args.skip_exactly_once and report["pdf"].get("pages"):
            report["exactly_once"] = check_exactly_once(Path(args.pptx), report["pdf"]["pages"], fixtures_dir)

    out_path = report_dir / f"{args.label}_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["pptx"]["summary"]
    print(f"=== {args.label} ===")
    print(f"page_count={report['pptx']['page_count']} (ok={report['pptx']['page_count_ok']})")
    print(f"canvas={report['pptx']['canvas']}")
    print(f"total_full_page_copies={s['total_full_page_copies']} (must be 0)")
    print(f"total_page_content_crops={s['total_page_content_crops']} slides={s['page_content_crop_slides']}")
    print(f"total_editable_text_shapes={s['total_editable_text_shapes']}")
    print(f"total_editable_text_chars={s['total_editable_text_chars']}")
    print(f"total_native_tables={s['total_native_tables']}")
    print(f"slides_with_zero_editable_text={s['slides_with_zero_editable_text']} / {args.expected_page_count}")
    print(f"order_mismatch_slides={s['order_mismatch_slides']}")
    print(f"deck_min_explicit_font_pt={s['deck_min_explicit_font_pt']}")
    print(f"slides_with_font_below_9.5pt={s['slides_with_font_below_9_5pt']}")
    print(f"mean_char_recall={s['mean_char_recall']}")
    print(f"min_char_recall={s['min_char_recall']}")
    print(f"mean_numeric_recall={s['mean_numeric_recall']}")
    print(f"min_numeric_recall={s['min_numeric_recall']}")
    print(f"low_char_recall_slides(<{LOW_RECALL_THRESHOLD})={s['low_char_recall_slides']}")
    print(f"low_numeric_recall_slides(<{LOW_RECALL_THRESHOLD})={s['low_numeric_recall_slides']}")
    print(f"total_suspicious_fragments={s['total_suspicious_fragments']} "
          f"slides={s['slides_with_suspicious_fragments']}")
    for slide in report["pptx"]["slides"]:
        if slide["suspicious_fragments"]:
            print(f"  slide {slide['slide_number']}: {slide['suspicious_fragments']}")
    if "pdf" in report:
        p = report["pdf"]
        print(f"pdf page_count={p.get('page_count')} ok={p.get('page_count_ok')}")
    if "exactly_once" in report:
        eos = report["exactly_once"]["summary"]
        print(f"exactly_once: ocr_dup_pages={eos['pages_with_ocr_duplicates']}")
        print(f"exactly_once: pptx_text_dup_pages={eos['pages_with_pptx_text_duplicates']}")
        print(f"exactly_once: word_cut_pages={eos['pages_with_word_cut_candidates']}")
        print(f"exactly_once: high_confidence_cut_pages={eos['pages_with_high_confidence_cuts']}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
