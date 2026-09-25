"""Exact-once fidelity metrics: objective duplicate/missing/cut-word detection.

Replaces "eyeballing" duplication/clipping with a token-multiset comparison
against the source page's OCR, per the coordinator's spec (2026-09-25):

  visible token multiset per output page = OCR of the rendered PDF page
  (tesseract kor+eng, high DPI) UNION the PPTX's own editable text for that
  slide -- reported as two separate signals since they catch different defect
  classes (OCR catches anything visually duplicated/rendered, including text
  baked into pictures; the PPTX-text signal catches duplication in the
  underlying editable content).

Three checks, all on normalized token multisets vs. the source page's OCR:
  1. duplicated_tokens: output_count - source_count >= 2, OR a token with
     len>=4 chars appears >=2 times in the output while the source has it
     at most once.
  2. missing_long_tokens: tokens with len>=4 present in the source OCR but
     completely absent from the output.
  3. word_cut_candidates: an output token that is a strict prefix/suffix of
     some source token with >=2 characters missing, and is not itself a
     legitimate source token (so short real words aren't flagged). When OCR
     word boxes are available, a candidate whose bounding box sits within
     CROP_EDGE_TOLERANCE_PX of a picture shape's rendered edge is flagged
     at_crop_edge=True (best-effort geometric proxy for "the image crop
     boundary sliced through this word").

Usage as a library from checker_pdf_report.py (`check_exactly_once`).
"""
from __future__ import annotations

import csv
import io
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

# Both overridable via env var so this runs unmodified outside this checkout
# (a fresh clone, a different worktree, a CI image with Tesseract installed
# elsewhere). Defaults match this repo's own bundled tessdata (eng+kor) and
# the standard Windows Tesseract-OCR install location.
TESSERACT_EXE = os.environ.get("RASTER_EVAL_TESSERACT_EXE", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA_DIR = os.environ.get(
    "RASTER_EVAL_TESSDATA_DIR",
    str(Path(__file__).resolve().parents[4] / "tessdata"),
)

# Matches source token: run of Korean syllables, run of Latin letters, or a
# number (with thousands separators / decimals / trailing %). Deliberately
# the same shape as checker.py's NUMBER_RE / char handling so results are
# comparable with the rest of the checker.
_TOKEN_RE = re.compile(r"[A-Za-z]+|[0-9][0-9,.]*%?|[가-힣]+")

MIN_LONG_TOKEN_CHARS = 4
MIN_CUT_CHARS_MISSING = 2
CROP_EDGE_TOLERANCE_PX = 10
# Calibration finding (2026-09-25, final6): a floor of 1 char produces
# massive noise on Korean OCR -- single Hangul syllables are not reliable
# word units (tesseract's word-gap segmentation varies run to run, and
# common 1-syllable particles/roots legitimately recur many times per
# dense page). Every real human-found defect sampled was >=2 characters.
# This floor does not eliminate the noise, just reduces it; see report.
MIN_TOKEN_CHARS_FOR_SIGNAL = 2


def normalize_tokens(text: str) -> Counter:
    """Tokenize + normalize (casefold Latin tokens; Korean/numbers as-is)."""
    tokens = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0)
        if tok[0].isascii() and tok[0].isalpha():
            tok = tok.lower()
        tokens.append(tok)
    return Counter(tokens)


def ocr_tokens_and_words(image_path: Path) -> tuple[Counter, list[dict]]:
    """Run tesseract TSV mode; return (token multiset, word boxes).

    Each word box: {"text": str, "left": int, "top": int, "width": int, "height": int}.
    Boxes are in source-image pixel coordinates (i.e. the DPI the PNG was rendered at).
    """
    out_base = image_path.with_suffix("")
    result = subprocess.run(
        [
            TESSERACT_EXE, str(image_path), str(out_base),
            "--tessdata-dir", TESSDATA_DIR, "-l", "kor+eng", "--psm", "3",
            # The bundled repo tessdata/ has no configs/ subdir (just the two
            # .traineddata files), so the "tsv" configfile name can't be
            # resolved relative to it; this variable form needs no configfile.
            "-c", "tessedit_create_tsv=1",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tesseract tsv failed on {image_path}: {result.stderr}")
    tsv_path = out_base.with_suffix(".tsv")
    raw = tsv_path.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw), delimiter="\t", quoting=csv.QUOTE_NONE)
    words = []
    full_text_parts = []
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            conf = -1.0
        if conf < 0:  # tesseract uses -1 for non-word aggregate rows
            continue
        words.append({
            "text": text,
            "left": int(row["left"]), "top": int(row["top"]),
            "width": int(row["width"]), "height": int(row["height"]),
        })
        full_text_parts.append(text)
    tokens = normalize_tokens(" ".join(full_text_parts))
    return tokens, words


def pptx_tokens(slide_visible_text: str) -> Counter:
    return normalize_tokens(slide_visible_text)


def duplicated_tokens(source: Counter, output: Counter) -> list[dict]:
    hits = []
    for tok, out_count in output.items():
        if len(tok) < MIN_TOKEN_CHARS_FOR_SIGNAL:
            continue
        src_count = source.get(tok, 0)
        extra = out_count - src_count
        if extra <= 0:
            continue
        is_long_repeat = len(tok) >= MIN_LONG_TOKEN_CHARS and out_count >= 2 and src_count <= 1
        if extra >= 2 or is_long_repeat:
            hits.append({"token": tok, "source_count": src_count, "output_count": out_count, "extra": extra})
    return sorted(hits, key=lambda h: -h["extra"])


def missing_long_tokens(source: Counter, output: Counter, min_len: int = MIN_LONG_TOKEN_CHARS) -> list[str]:
    return sorted(
        tok for tok, count in source.items()
        if count > 0 and len(tok) >= min_len and output.get(tok, 0) == 0
    )


def _bbox_edges(b: dict) -> tuple[float, float, float, float]:
    return b["left"], b["top"], b["left"] + b["width"], b["top"] + b["height"]


def picture_pixel_bboxes(slide, slide_w_emu: int, slide_h_emu: int,
                          page_pixel_w: float, page_pixel_h: float) -> list[tuple[float, float, float, float]]:
    """Picture shape bboxes on this slide, in the same pixel space as a PDF
    page rendered at (page_pixel_w x page_pixel_h)."""
    boxes = []
    for shape in slide.shapes:
        if shape.shape_type != 13:  # MSO_SHAPE_TYPE.PICTURE
            continue
        x0 = shape.left / slide_w_emu * page_pixel_w
        y0 = shape.top / slide_h_emu * page_pixel_h
        x1 = (shape.left + shape.width) / slide_w_emu * page_pixel_w
        y1 = (shape.top + shape.height) / slide_h_emu * page_pixel_h
        boxes.append((x0, y0, x1, y1))
    return boxes


def _near_any_edge(word_box: dict, picture_boxes: list[tuple[float, float, float, float]],
                    tolerance: float = CROP_EDGE_TOLERANCE_PX) -> bool:
    wl, wt, wr, wb = _bbox_edges(word_box)
    for (px0, py0, px1, py1) in picture_boxes:
        # word must be roughly inside/adjacent to the picture's vertical band
        if wb < py0 - tolerance or wt > py1 + tolerance:
            continue
        if abs(wl - px0) <= tolerance or abs(wr - px1) <= tolerance:
            return True
    return False


def word_cut_candidates(source: Counter, output_words: list[dict],
                         picture_boxes: list[tuple[float, float, float, float]] | None = None,
                         min_missing: int = MIN_CUT_CHARS_MISSING) -> list[dict]:
    """Output tokens that look like a truncated fragment of a source token."""
    source_vocab = [t for t in source if len(t) >= min_missing + 1]
    candidates = []
    seen = set()
    for w in output_words:
        for m in _TOKEN_RE.finditer(w["text"]):
            tok = m.group(0)
            if tok[0].isascii() and tok[0].isalpha():
                tok = tok.lower()
            if not tok or source.get(tok, 0) > 0:
                continue  # legitimate source token, not a cut
            if len(tok) < MIN_TOKEN_CHARS_FOR_SIGNAL or tok in seen:
                continue
            for src_tok in source_vocab:
                if src_tok == tok:
                    continue
                missing = len(src_tok) - len(tok)
                if missing < min_missing:
                    continue
                if src_tok.endswith(tok) or src_tok.startswith(tok):
                    at_edge = _near_any_edge(w, picture_boxes or [])
                    candidates.append({
                        "output_token": tok, "source_token": src_tok,
                        "chars_missing": missing, "at_crop_edge": at_edge,
                    })
                    seen.add(tok)
                    break
    return candidates
