"""Per-deck scoring: turns a generated PPTX (+ optional PDF) into the score
dict `run_suite.py`'s `score`/`baseline` commands write into summary.json.

Two checker kinds (selected by deck_cfg["checker"] in suite_config.json):
  - `ground_truth_checker`: exact python-pptx source text (deck1-4 style).
  - `checker`: OCR-based source_ground_truth.json fixtures, for a deck with
    no exact ground truth (e.g. a large externally-configured reference
    deck) -- includes best-match reordering-aware recall (see
    `_one_to_one_best_match`).

Split out of the original run_suite.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; scoring logic is unchanged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from mockup_regions import load_analysis_result, mockup_summary
from suite_paths import resolve_deck_path
from wrong_number_filter import classify_wrong_numbers


def _norm_len(text: Optional[str]) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _excl_mockup_metrics(pptx_path: Path, page_source_editable: list[tuple[int, int, int]]) -> dict:
    """`page_source_editable`: (page_number, source_char_len, editable_text_chars)
    per slide, where page_number is the ANALYSIS's own source page number (for
    ground_truth_checker decks this is the same as the output slide's own
    page_number; for checker-kind decks it's the best-match matched_source_page,
    since the analysis is keyed by source order). Reads
    `<pptx_path's own dir>/analysis_result.json` (written by
    generate_raster_deck.py's save_analysis_result(), see raster_deck_analysis.py);
    returns all-None/empty fields (mockup_analysis_available=False) when that
    file isn't present -- older runs never saved it."""
    analysis = load_analysis_result(pptx_path.parent)
    if analysis is None:
        return {
            "editable_text_ratio_body_excl_mockup": None,
            "mockup_regions_count": None,
            "mockup_text_chars": None,
            "mockup_regions": [],
            "mockup_analysis_available": False,
        }
    summary = mockup_summary(analysis)
    ratios = []
    for page_number, src_len, editable in page_source_editable:
        denom = src_len - summary["mockup_chars_by_page"].get(page_number, 0)
        if denom > 0:
            ratios.append(editable / denom)
    return {
        "editable_text_ratio_body_excl_mockup": (sum(ratios) / len(ratios)) if ratios else None,
        "mockup_regions_count": summary["mockup_regions_count"],
        "mockup_text_chars": summary["mockup_text_chars"],
        "mockup_regions": summary["mockup_regions"],
        "mockup_analysis_available": True,
    }


def _load_page_content_fallback_pages(evidence_path: Optional[Path]) -> Optional[list]:
    """Reads generate_raster_deck.py's fetch_slide_evidence() output
    (slide_evidence_report.json): per slide, `has_page_content_fallback` is
    True iff the product's own raster-render-advisories include a
    'page-content' groupId. Returns 1-based page numbers, or None if the
    evidence file isn't available (e.g. --skip-generate runs)."""
    if not evidence_path or not Path(evidence_path).exists():
        return None
    data = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    pages = []
    for s in data.get("slides", []):
        if s.get("has_page_content_fallback"):
            idx = s.get("index")
            if idx is not None:
                pages.append(idx + 1)  # index is 0-based
    return sorted(pages)


def score_deck_ground_truth(deck_cfg: dict, decks_root: Path, pptx_path: Path,
                             run_report_path: Optional[Path] = None,
                             evidence_path: Optional[Path] = None) -> dict:
    import ground_truth_checker as gtc
    import checker as ck
    from pptx import Presentation

    gt = gtc.load_ground_truth(resolve_deck_path(decks_root, deck_cfg["ground_truth"]))
    source_pages_dir = resolve_deck_path(decks_root, deck_cfg["source_pages_dir"])
    report = gtc.check_against_ground_truth(Path(pptx_path), gt, source_pages_dir)

    prs = Presentation(str(pptx_path))
    canvas = ck.check_canvas_size(prs.slide_width, prs.slide_height)

    font_sizes: list[float] = []
    for slide in prs.slides:
        st = ck.slide_editable_text_stats(slide)
        font_sizes.extend(st["font_sizes_pt"])
    min_font_pt = min(font_sizes) if font_sizes else None

    gt_pages_by_number = {p["page_number"]: p for p in gt["pages"]}
    prs_slides = list(prs.slides)
    wrong_pages_raw = []
    wrong_pages_filtered = []
    filtered_as_page_number = []
    for s in report["slides"]:
        fab = s["numeric_exactness"]["fabricated_numbers"]
        if not fab:
            continue
        page_number = s["page_number"]
        wrong_pages_raw.append({"page": page_number, "fabricated": fab})
        slide_obj = prs_slides[page_number - 1]
        source_tokens = gt_pages_by_number.get(page_number, {}).get("numeric_tokens", [])
        filtered_tokens, remaining_tokens = classify_wrong_numbers(
            slide_obj, prs.slide_height, page_number, fab, source_tokens)
        if filtered_tokens:
            filtered_as_page_number.append({"page": page_number, "tokens": filtered_tokens})
        if remaining_tokens:
            wrong_pages_filtered.append({"page": page_number, "fabricated": remaining_tokens})
    wrong_count_raw = sum(len(w["fabricated"]) for w in wrong_pages_raw)
    wrong_count_filtered = sum(len(w["fabricated"]) for w in wrong_pages_filtered)

    page_content_fallback_pages_product = _load_page_content_fallback_pages(evidence_path)

    total_editable = sum(s["editable_text_chars"] for s in report["slides"])
    total_source = sum(s["source_char_count"] for s in report["slides"])
    editable_overall = (total_editable / total_source) if total_source else None
    mean_pct = report["summary"]["mean_pct_source_text_editable"]
    editable_body = (mean_pct / 100.0) if mean_pct is not None else None

    correct = total = 0
    for s in report["slides"]:
        ta = s.get("table_cell_accuracy") or {}
        for t in ta.get("per_table", []) or []:
            if t.get("matched") and t.get("total_cells"):
                correct += t["correct_cells"]
                total += t["total_cells"]
    table_cell_accuracy = (correct / total) if total else None

    duration = None
    if run_report_path and Path(run_report_path).exists():
        rr = json.loads(Path(run_report_path).read_text(encoding="utf-8"))
        duration = round(sum(t["seconds"] for t in rr.get("timings", [])), 1)

    # Page numbers here are already the analysis's own source page numbers
    # (this checker assumes positional identity, same as wrong_numbers above).
    excl_mockup = _excl_mockup_metrics(
        pptx_path, [(s["page_number"], s["source_char_count"], s["editable_text_chars"]) for s in report["slides"]]
    )

    return {
        "deck_id": deck_cfg["id"], "checker": "ground_truth_checker",
        "pptx_path": str(pptx_path),
        "page_count": report["page_count"], "expected_page_count": report["expected_page_count"],
        "page_count_ok": report["page_count"] == report["expected_page_count"],
        "canvas": canvas, "a4_ok": canvas["is_a4_landscape"],
        # `wrong_numbers` is the FILTERED (actionable) hard-fail view; `wrong_numbers_raw` is what
        # ground_truth_checker.py reports before removing product-added page-number-footer digits
        # that aren't in the source text (see classify_wrong_numbers / README). Use `wrong_numbers`
        # for gating; use `wrong_numbers_filtered_as_page_number` to audit what was excluded and why.
        "wrong_numbers": {"count": wrong_count_filtered, "pages": wrong_pages_filtered},
        "wrong_numbers_raw": {"count": wrong_count_raw, "pages": wrong_pages_raw},
        "wrong_numbers_filtered_as_page_number": filtered_as_page_number,
        "editable_text_ratio_overall": editable_overall,
        "editable_text_ratio_body_mean": editable_body,
        **excl_mockup,
        "char_recall_mean": report["summary"]["mean_char_recall"],
        "char_precision_mean": report["summary"]["mean_char_precision"],
        "table_cell_accuracy": table_cell_accuracy,
        "full_page_copies": report["summary"]["total_full_page_copies"],
        "page_content_crops": report["summary"]["total_page_content_crops"],
        "page_content_crop_pages": [s["page_number"] for s in report["slides"] if s["page_content_crop_count"] > 0],
        "page_content_fallback_pages_product": page_content_fallback_pages_product,
        "min_font_pt": min_font_pt,
        "exactly_once_pptx_text_duplicate_pages": report["summary"]["pages_with_duplicated_tokens"],
        "analysis_duration_seconds": duration,
        "_raw": report,
    }


def _one_to_one_best_match(pptx_path: Path, pptx_report: dict, gt: dict) -> dict:
    """Greedy one-to-one assignment of output slides to source pages by text
    similarity (char_recall), with a bonus when a picture on the output slide
    was independently hash-matched (full-page-copy / page-content crop) to
    that same source page. Diagnostic for decks whose output order differs
    from source order: checker.py's own per-slide fidelity is POSITIONAL
    (output slide N vs source page N), which understates fidelity when the
    deck was reordered. NOT a globally-optimal assignment (greedy by
    descending similarity) -- good enough to diagnose reordering, not a proof
    of the single best matching."""
    import checker as ck
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    output_texts = {i + 1: ck.slide_text(slide) for i, slide in enumerate(prs.slides)}
    source_texts = {p["page_number"]: p["ocr_text_normalized"] for p in gt["pages"]}
    source_numeric = {p["page_number"]: p["numeric_tokens"] for p in gt["pages"]}

    sim: dict = {}
    for oi, otext in output_texts.items():
        for sj, stext in source_texts.items():
            sim[(oi, sj)] = ck.char_recall(stext, otext)["recall"] or 0.0
    for s in pptx_report["slides"]:
        oi = s["slide_number"]
        for pic in s["pictures"]:
            if pic.get("is_full_page_copy") or pic.get("is_page_content_crop"):
                bj = pic.get("best_matching_source_page")
                if bj is not None and (oi, bj) in sim:
                    sim[(oi, bj)] = min(1.0, sim[(oi, bj)] + 0.5)

    pairs = sorted(sim.items(), key=lambda kv: -kv[1])
    used_out, used_src, mapping = set(), set(), {}
    for (oi, sj), _score in pairs:
        if oi in used_out or sj in used_src:
            continue
        mapping[oi] = sj
        used_out.add(oi)
        used_src.add(sj)
        if len(mapping) == len(output_texts):
            break

    per_slide, char_recalls, numeric_recalls = [], [], []
    total_editable = total_source = 0
    ratios = []
    for s in pptx_report["slides"]:
        oi = s["slide_number"]
        sj = mapping.get(oi)
        recall = num_recall = None
        src_len = 0
        if sj is not None:
            recall = ck.char_recall(source_texts.get(sj, ""), output_texts[oi])["recall"]
            num_recall = ck.numeric_recall(source_numeric.get(sj, []), output_texts[oi])["recall"]
            src_len = _norm_len(source_texts.get(sj, ""))
        per_slide.append({
            "output_slide": oi, "matched_source_page": sj,
            "matches_positional": sj == oi, "char_recall": recall,
        })
        if recall is not None:
            char_recalls.append(recall)
        if num_recall is not None:
            numeric_recalls.append(num_recall)
        ec = s["editable_text_chars"]
        total_editable += ec
        total_source += src_len
        if src_len:
            ratios.append(ec / src_len)

    mismatches = [p["output_slide"] for p in per_slide
                  if p["matched_source_page"] is not None and not p["matches_positional"]]
    return {
        "order_mapping": per_slide,
        "order_mismatch_count": len(mismatches),
        "order_mismatch_output_slides": mismatches,
        "char_recall_mean": (sum(char_recalls) / len(char_recalls)) if char_recalls else None,
        "numeric_recall_mean": (sum(numeric_recalls) / len(numeric_recalls)) if numeric_recalls else None,
        "editable_text_ratio_overall": (total_editable / total_source) if total_source else None,
        "editable_text_ratio_body_mean": (sum(ratios) / len(ratios)) if ratios else None,
    }


def score_deck_checker(deck_cfg: dict, decks_root: Path, pptx_path: Path, pdf_path: Optional[Path],
                        report_dir: Path, label: str, run_report_path: Optional[Path] = None,
                        evidence_path: Optional[Path] = None) -> dict:
    import checker as ck

    expected_page_count = deck_cfg.get("expected_page_count", 28)
    fixtures_dir = resolve_deck_path(decks_root, deck_cfg["fixtures_dir"])
    pptx_report = ck.check_pptx(Path(pptx_path), fixtures_dir, expected_page_count)

    pdf_report = None
    eo_report = None
    fitz_available = True
    if pdf_path and Path(pdf_path).exists():
        pdf_report = ck.check_pdf(Path(pdf_path), report_dir, label, expected_page_count)
        if pdf_report.get("error") == "pymupdf_not_available":
            fitz_available = False
            pdf_report = None
        elif pdf_report.get("pages"):
            eo_report = ck.check_exactly_once(Path(pptx_path), pdf_report["pages"], fixtures_dir)

    gt = json.loads((fixtures_dir / "source_ground_truth.json").read_text(encoding="utf-8"))
    src_pages = {p["page_number"]: p for p in gt["pages"]}
    total_editable = total_source = 0
    ratios = []
    for s in pptx_report["slides"]:
        idx = s["slide_number"]
        src_len = _norm_len(src_pages.get(idx, {}).get("ocr_text_normalized", ""))
        ec = s["editable_text_chars"]
        total_editable += ec
        total_source += src_len
        if src_len:
            ratios.append(ec / src_len)
    editable_overall_positional = (total_editable / total_source) if total_source else None
    editable_body_positional = (sum(ratios) / len(ratios)) if ratios else None

    # One-to-one best-match reordering (2026-09-25 follow-up): if the output slide
    # order differs from source order, positional recall/editable-ratio understate
    # fidelity. Recompute against the best content match; keep the positional
    # numbers too, under the *_positional keys, for continuity with earlier runs.
    best_match = _one_to_one_best_match(pptx_path, pptx_report, gt)

    # Mockup exclusion uses the best-match mapping (analysis pages are keyed by
    # SOURCE page number, same as `src_pages` here) so a reordered deck's mockup
    # chars are still subtracted from the source page that was actually matched.
    slide_to_source = {p["output_slide"]: p["matched_source_page"] for p in best_match["order_mapping"]}
    excl_mockup_input = []
    for s in pptx_report["slides"]:
        sj = slide_to_source.get(s["slide_number"])
        if sj is None:
            continue
        src_len = _norm_len(src_pages.get(sj, {}).get("ocr_text_normalized", ""))
        excl_mockup_input.append((sj, src_len, s["editable_text_chars"]))
    excl_mockup = _excl_mockup_metrics(pptx_path, excl_mockup_input)

    summary = pptx_report["summary"]
    duration = None
    if run_report_path and Path(run_report_path).exists():
        rr = json.loads(Path(run_report_path).read_text(encoding="utf-8"))
        duration = round(sum(t["seconds"] for t in rr.get("timings", [])), 1)

    page_content_fallback_pages_product = _load_page_content_fallback_pages(evidence_path)

    result = {
        "deck_id": deck_cfg["id"], "checker": "checker",
        "pptx_path": str(pptx_path),
        "page_count": pptx_report["page_count"], "expected_page_count": pptx_report["expected_page_count"],
        "page_count_ok": pptx_report["page_count_ok"],
        "canvas": pptx_report["canvas"], "a4_ok": pptx_report["canvas"]["is_a4_landscape"],
        "wrong_numbers": {
            "count": None, "pages": [],
            "note": ("not computed for this deck: ground truth here is OCR text, not exact source "
                     "text, so extra numbers in the output cannot be safely called fabrications. "
                     "See char_recall_mean / numeric_recall_mean instead."),
        },
        "editable_text_ratio_overall": best_match["editable_text_ratio_overall"],
        "editable_text_ratio_body_mean": best_match["editable_text_ratio_body_mean"],
        "editable_text_ratio_overall_positional": editable_overall_positional,
        "editable_text_ratio_body_mean_positional": editable_body_positional,
        **excl_mockup,
        "char_recall_mean": best_match["char_recall_mean"],
        "char_recall_mean_positional": summary["mean_char_recall"],
        "char_precision_mean": None,
        "numeric_recall_mean": best_match["numeric_recall_mean"],
        "numeric_recall_mean_positional": summary["mean_numeric_recall"],
        "order_mapping": best_match["order_mapping"],
        "order_mismatch_count_best_match": best_match["order_mismatch_count"],
        "order_mismatch_output_slides_best_match": best_match["order_mismatch_output_slides"],
        "order_mismatch_slides_positional": summary["order_mismatch_slides"],
        "table_cell_accuracy": None,
        "native_tables_total": summary["total_native_tables"],
        "full_page_copies": summary["total_full_page_copies"],
        "page_content_crops": summary["total_page_content_crops"],
        "page_content_crop_pages": summary["page_content_crop_slides"],
        "page_content_fallback_pages_product": page_content_fallback_pages_product,
        "min_font_pt": summary["deck_min_explicit_font_pt"],
        "exactly_once": None,
        "fitz_available": fitz_available,
        "analysis_duration_seconds": duration,
        "_raw": {"pptx": pptx_report, "pdf": pdf_report, "exactly_once": eo_report},
    }
    if eo_report:
        result["exactly_once"] = {
            "ocr_duplicate_pages": eo_report["summary"]["pages_with_ocr_duplicates"],
            "pptx_text_duplicate_pages": eo_report["summary"]["pages_with_pptx_text_duplicates"],
            "word_cut_candidate_pages": eo_report["summary"]["pages_with_word_cut_candidates"],
            "high_confidence_cut_pages": eo_report["summary"]["pages_with_high_confidence_cuts"],
        }
    return result


def score_one_deck(deck_cfg: dict, decks_root: Path, pptx_path: Path, pdf_path: Optional[Path],
                    report_dir: Path, label: str, run_report_path: Optional[Path] = None,
                    evidence_path: Optional[Path] = None) -> dict:
    report_dir.mkdir(parents=True, exist_ok=True)
    if deck_cfg["checker"] == "ground_truth_checker":
        result = score_deck_ground_truth(deck_cfg, decks_root, pptx_path, run_report_path, evidence_path)
    elif deck_cfg["checker"] == "checker":
        result = score_deck_checker(deck_cfg, decks_root, pptx_path, pdf_path, report_dir, label,
                                     run_report_path, evidence_path)
    else:
        raise ValueError(f"unknown checker kind: {deck_cfg['checker']}")

    raw = result.pop("_raw", None)
    if raw is not None:
        (report_dir / f"{label}_raw_report.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def find_run_outputs(deck_out_dir: Path) -> tuple[Optional[Path], Optional[Path], Optional[Path], Optional[Path]]:
    """Returns (pptx_path, pdf_path, run_report_path, evidence_path) for a deck's launch output dir."""
    run_report = deck_out_dir / "run_report.json"
    run_report = run_report if run_report.exists() else None
    evidence = deck_out_dir / "slide_evidence_report.json"
    evidence = evidence if evidence.exists() else None
    manifest_path = deck_out_dir / "manifest.json"
    if not manifest_path.exists():
        return None, None, run_report, evidence
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pid = manifest.get("presentation_id")
    if not pid:
        return None, None, run_report, evidence
    pptx = deck_out_dir / f"{pid}.pptx"
    pdf = deck_out_dir / f"{pid}.pdf"
    return (pptx if pptx.exists() else None), (pdf if pdf.exists() else None), run_report, evidence
