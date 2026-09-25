"""Checker extension for the generalization test (2026-09-25): verifies an
auto_redesign output against EXACT original-PPTX text (not OCR), for decks
built by generalization/build_image_only_deck.py.

Because the ground truth is the real python-pptx text extraction of the
original deck (not an OCR read of a screenshot), the signals here are much
higher-precision than checker.py's OCR-based ones:
  - char recall/precision (both directions -- OCR ground truth only supports
    recall meaningfully, since OCR itself is noisy on the "precision" side)
  - numeric-token exactness: any number in the editable output that is not a
    real source number is a HARD FAILURE (fabrication), not just a suspicion
  - table cell accuracy: exact per-cell string match against source tables
  - % of source text delivered as editable vs left as pixels
  - page-content-crop / full-page-copy / duplicate-token detection, reusing
    checker.py's picture-hash and exactly_once machinery against the
    deck's own rendered source pages (source_pages/NN.png from the build step)

Usage:
  uv run --with pymupdf python ground_truth_checker.py \
      --pptx OUT.pptx --pdf OUT.pdf \
      --ground-truth ground_truth.json --source-pages-dir source_pages \
      --report-dir <dir> --label <name>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import checker as ck  # noqa: E402
import exactly_once as eo  # noqa: E402
from imghash import ahash_bits  # noqa: E402

from pptx import Presentation  # noqa: E402

NUMBER_RE = re.compile(r"\d[\d,.]*%?")


def load_ground_truth(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def char_recall_precision(source_text: str, output_text: str) -> dict:
    src = re.sub(r"\s+", "", source_text or "")
    out = re.sub(r"\s+", "", output_text or "")
    src_c, out_c = Counter(src), Counter(out)
    matched = sum(min(c, out_c.get(ch, 0)) for ch, c in src_c.items())
    recall = matched / len(src) if src else None
    precision = matched / len(out) if out else None
    return {"recall": recall, "precision": precision, "source_chars": len(src), "output_chars": len(out)}


def numeric_exactness(source_tokens: list[str], output_text: str) -> dict:
    """Hard-failure check: every numeric token in the editable output must be
    a real source number. Extra/unmatched output numbers are fabrications."""
    src_c = Counter(source_tokens)
    out_tokens = NUMBER_RE.findall(output_text or "")
    out_c = Counter(out_tokens)
    missing = []
    remaining_out = Counter(out_c)
    for tok, cnt in src_c.items():
        have = remaining_out.get(tok, 0)
        take = min(have, cnt)
        remaining_out[tok] = have - take
        if take < cnt:
            missing.extend([tok] * (cnt - take))
    fabricated = [tok for tok, cnt in remaining_out.items() for _ in range(cnt) if cnt > 0]
    return {
        "source_number_count": sum(src_c.values()),
        "output_number_count": sum(out_c.values()),
        "missing_numbers": missing,
        "fabricated_numbers": fabricated,
        "hard_failure": bool(fabricated),
    }


def table_cell_accuracy(source_tables: list[dict], output_tables: list[dict]) -> dict:
    """Best-effort table matching: pairs source/output tables by row/col shape
    in order, then compares cell text exactly (normalized whitespace)."""
    if not source_tables:
        return {"source_table_count": 0, "output_table_count": len(output_tables), "cell_accuracy": None}
    results = []
    for i, src_t in enumerate(source_tables):
        if i >= len(output_tables):
            results.append({"matched": False, "reason": "missing_table"})
            continue
        out_t = output_tables[i]
        src_cells = [re.sub(r"\s+", " ", c).strip() for row in src_t["cells"] for c in row]
        out_cells_flat = out_t.get("cells_flat", [])
        total = len(src_cells)
        correct = sum(1 for a, b in zip(src_cells, out_cells_flat, strict=False) if a == b)
        results.append({
            "matched": True, "source_rows": src_t["rows"], "source_cols": src_t["cols"],
            "output_rows": out_t.get("rows"), "output_cols": out_t.get("cols"),
            "total_cells": total, "correct_cells": correct,
            "accuracy": correct / total if total else None,
        })
    accuracies = [r["accuracy"] for r in results if r.get("accuracy") is not None]
    return {
        "source_table_count": len(source_tables), "output_table_count": len(output_tables),
        "per_table": results,
        "mean_cell_accuracy": sum(accuracies) / len(accuracies) if accuracies else None,
    }


def output_tables_for_slide(slide) -> list[dict]:
    tables = []
    for shape in slide.shapes:
        if getattr(shape, "has_table", False):
            tbl = shape.table
            cells = [[cell.text for cell in row.cells] for row in tbl.rows]
            flat = [re.sub(r"\s+", " ", c).strip() for row in cells for c in row]
            tables.append({"rows": len(tbl.rows), "cols": len(tbl.columns), "cells_flat": flat})
    return tables


def check_against_ground_truth(pptx_path: Path, gt: dict, source_pages_dir: Path) -> dict:
    prs = Presentation(str(pptx_path))
    gt_pages = {p["page_number"]: p for p in gt["pages"]}
    source_hashes = []
    source_page_paths = {}
    for pn in gt_pages:
        img_path = source_pages_dir / f"{pn:02d}.png"
        if img_path.exists():
            source_hashes.append((pn, ahash_bits(img_path.read_bytes())))
            source_page_paths[pn] = str(img_path)

    # canvas size isn't checked here: the source deck's own page size is arbitrary
    # (not necessarily A4), so check_canvas_size's A4 comparison isn't applicable.
    slides_report = []
    for idx, slide in enumerate(prs.slides, start=1):
        g = gt_pages.get(idx)
        if g is None:
            continue
        text = ck.slide_text(slide)
        text_stats = ck.slide_editable_text_stats(slide)
        pictures = ck.slide_pictures(slide, prs.slide_width, prs.slide_height, source_hashes, source_page_paths)
        out_tables = output_tables_for_slide(slide)

        cr = char_recall_precision(g["text_normalized"], text)
        ne = numeric_exactness(g["numeric_tokens"], text)
        ta = table_cell_accuracy(g["tables"], out_tables)
        out_gt_tokens = eo.normalize_tokens(g["text_normalized"])
        out_tokens = eo.normalize_tokens(text)
        dup = eo.duplicated_tokens(out_gt_tokens, out_tokens)

        editable_chars = text_stats["chars"]
        pct_editable = (editable_chars / g["char_count"] * 100) if g["char_count"] else None

        slides_report.append({
            "page_number": idx,
            "char_recall_precision": cr,
            "numeric_exactness": ne,
            "table_cell_accuracy": ta,
            "duplicated_tokens": dup,
            "editable_text_shapes": text_stats["text_shapes"],
            "editable_text_chars": editable_chars,
            "source_char_count": g["char_count"],
            "pct_source_text_editable": pct_editable,
            "full_page_copy_count": sum(1 for p in pictures if p.get("is_full_page_copy")),
            "page_content_crop_count": sum(1 for p in pictures if p.get("is_page_content_crop")),
        })

    hard_failures = [s["page_number"] for s in slides_report if s["numeric_exactness"]["hard_failure"]]
    recalls = [
        s["char_recall_precision"]["recall"] for s in slides_report
        if s["char_recall_precision"]["recall"] is not None
    ]
    precisions = [
        s["char_recall_precision"]["precision"] for s in slides_report
        if s["char_recall_precision"]["precision"] is not None
    ]
    pct_editables = [s["pct_source_text_editable"] for s in slides_report if s["pct_source_text_editable"] is not None]

    return {
        "pptx_path": str(pptx_path),
        "page_count": len(slides_report),
        "expected_page_count": gt["page_count"],
        "slides": slides_report,
        "summary": {
            "mean_char_recall": sum(recalls) / len(recalls) if recalls else None,
            "mean_char_precision": sum(precisions) / len(precisions) if precisions else None,
            "mean_pct_source_text_editable": sum(pct_editables) / len(pct_editables) if pct_editables else None,
            "numeric_hard_failure_pages": hard_failures,
            "numeric_hard_failure_count": len(hard_failures),
            "total_full_page_copies": sum(s["full_page_copy_count"] for s in slides_report),
            "total_page_content_crops": sum(s["page_content_crop_count"] for s in slides_report),
            "pages_with_duplicated_tokens": [s["page_number"] for s in slides_report if s["duplicated_tokens"]],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pptx", required=True)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--source-pages-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()

    gt = load_ground_truth(Path(args.ground_truth))
    report = check_against_ground_truth(Path(args.pptx), gt, Path(args.source_pages_dir))

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"{args.label}_ground_truth_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["summary"]
    print(f"=== {args.label} (ground-truth check) ===")
    print(f"page_count={report['page_count']}/{report['expected_page_count']}")
    print(f"mean_char_recall={s['mean_char_recall']} mean_char_precision={s['mean_char_precision']}")
    print(f"mean_pct_source_text_editable={s['mean_pct_source_text_editable']}")
    print(f"NUMERIC HARD FAILURES: {s['numeric_hard_failure_count']} pages {s['numeric_hard_failure_pages']}")
    print(f"total_full_page_copies={s['total_full_page_copies']} "
          f"total_page_content_crops={s['total_page_content_crops']}")
    print(f"pages_with_duplicated_tokens={s['pages_with_duplicated_tokens']}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
