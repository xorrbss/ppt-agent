"""`wrong_number_candidates`: for checker-kind decks (ground truth is
tesseract OCR over a rendered page image, e.g. a large external/customer
reference deck) there is no exact-source-text hard-fail number check the
way `ground_truth_checker.py`'s `numeric_exactness` gives deck1-4 -- OCR
ground truth is itself noisy, so a number missing from it isn't reliably a
product fabrication. This module flags CANDIDATES instead: every number
token in the output's own editable text that doesn't appear in the matched
source page's OCR text, for a human to verify (see `save_candidate_crops`).

A token is a candidate only if it's absent from BOTH the source page at the
slide's own position AND its best-match source page (see
`deck_scoring.py`'s `_one_to_one_best_match`) -- checking both reduces
false positives on a reordered deck, where the "right" source page for a
given output slide isn't always obvious from position alone.

**Page-number footer filter (2026-09-25):** on the real large-external-deck
baseline, 25 of 27 raw candidates were exactly the output slide's own page-number
footer stamp ("02" on slide 2, "17" on slide 17, ...) -- the same false
positive `wrong_number_filter.classify_wrong_numbers` already exists to
remove from `ground_truth_checker`'s `wrong_numbers` (deck1-4). Condition 2
of that filter ("the token isn't a real source number") is automatically
satisfied here -- a token only becomes a raw candidate because it's already
absent from both source texts -- so `find_candidates` reuses the same
function with an empty source-token list, applying only its structural
conditions (matches the slide's own page number; sits in a standalone
page-number-shaped shape/run). `find_candidates` returns the FILTERED list;
`find_raw_candidates` (unfiltered) and the filtered-out audit are also
exposed for callers that want to see what was excluded and why.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

from wrong_number_filter import classify_wrong_numbers

NUMBER_RE = re.compile(r"\d[\d,.]*%?")
CONTEXT_RADIUS = 24


def normalize_number(token: str) -> str:
    """Strip thousands separators/internal whitespace so "1,234" == "1234"
    and an OCR-mangled token with a stray space still compares cleanly."""
    return re.sub(r"[,\s]", "", token)


def _source_number_set(text: str) -> set:
    return {normalize_number(t) for t in NUMBER_RE.findall(text or "")}


def _context(text: str, start: int, end: int, radius: int = CONTEXT_RADIUS) -> str:
    lo, hi = max(0, start - radius), min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def _editable_text_spans(slide):
    """Yields (shape, text) for every editable text-bearing shape on a
    slide: text frames, and native tables (using the whole table shape's
    own bbox -- an individual cell has no bbox of its own)."""
    for shape in slide.shapes:
        if shape.has_text_frame:
            text = shape.text_frame.text
            if text and text.strip():
                yield shape, text
        if getattr(shape, "has_table", False):
            cell_text = "\n".join(cell.text for row in shape.table.rows for cell in row.cells)
            if cell_text.strip():
                yield shape, cell_text


def find_raw_candidates(prs, positional_source_text: dict, best_match_source_text: dict) -> list[dict]:
    """`positional_source_text` / `best_match_source_text`:
    {output_slide_number: source_ocr_text}. Returns a list of
    {page, token, context, bbox} -- `bbox` in the output canvas' own EMU
    units (the containing shape's `.left/.top/.width/.height`). Unfiltered:
    includes page-number-footer false positives, see `find_candidates`."""
    candidates = []
    for idx, slide in enumerate(prs.slides, start=1):
        pos_numbers = _source_number_set(positional_source_text.get(idx, ""))
        bm_numbers = _source_number_set(best_match_source_text.get(idx, ""))
        for shape, text in _editable_text_spans(slide):
            for m in NUMBER_RE.finditer(text):
                token = m.group(0)
                normalized = normalize_number(token)
                if normalized in pos_numbers or normalized in bm_numbers:
                    continue
                candidates.append({
                    "page": idx,
                    "token": token,
                    "context": _context(text, m.start(), m.end()),
                    "bbox": {"x": shape.left, "y": shape.top, "width": shape.width, "height": shape.height},
                })
    return candidates


def find_candidates(prs, positional_source_text: dict, best_match_source_text: dict) -> dict:
    """Like `find_raw_candidates`, but with page-number-footer false
    positives removed (see module docstring). Returns
    `{"candidates": [...], "raw_candidates": [...], "filtered_as_page_number": [...]}`
    -- `candidates` is the actionable, human-reviewable list; `raw_candidates`
    is the unfiltered output of `find_raw_candidates`; `filtered_as_page_number`
    lists exactly what was excluded and why (page, token)."""
    raw = find_raw_candidates(prs, positional_source_text, best_match_source_text)
    by_page: dict[int, list[dict]] = {}
    for c in raw:
        by_page.setdefault(c["page"], []).append(c)

    kept, filtered_out = [], []
    for idx, slide in enumerate(prs.slides, start=1):
        page_candidates = by_page.get(idx, [])
        if not page_candidates:
            continue
        tokens = [c["token"] for c in page_candidates]
        filtered_tokens, remaining_tokens = classify_wrong_numbers(slide, prs.slide_height, idx, tokens, [])
        for token in filtered_tokens:
            filtered_out.append({"page": idx, "token": token})
        remaining_counter = Counter(remaining_tokens)
        for c in page_candidates:
            if remaining_counter.get(c["token"], 0) > 0:
                kept.append(c)
                remaining_counter[c["token"]] -= 1
    return {"candidates": kept, "raw_candidates": raw, "filtered_as_page_number": filtered_out}


def _save_scaled_crop(image_path: Path, bbox_emu: dict, canvas_w_emu: int, canvas_h_emu: int,
                       dest: Path, margin_frac: float = 0.03) -> bool:
    """Crops `image_path` to `bbox_emu` scaled proportionally from the
    output canvas' EMU size to that image's own pixel size (the same
    technique `checker_slide.py`/`exactly_once.py` use for picture bboxes
    elsewhere in this suite) -- works for both the source page PNG and the
    output's own rendered PDF page PNG, which are not the same pixel size.
    Returns False (no-op) when the source image doesn't exist."""
    from PIL import Image

    if not image_path.exists():
        return False
    with Image.open(image_path) as im:
        w, h = im.size
        x0 = bbox_emu["x"] / canvas_w_emu * w
        y0 = bbox_emu["y"] / canvas_h_emu * h
        x1 = x0 + bbox_emu["width"] / canvas_w_emu * w
        y1 = y0 + bbox_emu["height"] / canvas_h_emu * h
        mw, mh = w * margin_frac, h * margin_frac
        crop = im.convert("RGB").crop((
            max(0, int(x0 - mw)), max(0, int(y0 - mh)),
            min(w, int(x1 + mw)), min(h, int(y1 + mh)),
        ))
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest)
    return True


def save_candidate_crops(candidates: list[dict], *, out_dir: Path, canvas_width_emu: int, canvas_height_emu: int,
                          source_pages_dir: Path, source_page_by_output_slide: dict,
                          output_pages_dir: Optional[Path] = None) -> None:
    """For each candidate: a crop of the matched SOURCE page (best-match
    source page for that output slide, via `source_page_by_output_slide`)
    around the shape's bbox, and -- when `output_pages_dir` is given (pass
    `check_pdf()`'s own `report_dir/f"{label}_pdf_pages"`, already rendered
    as a side effect of exactly-once scoring) -- a crop of the OUTPUT's own
    rendered page at the same bbox, so a human can compare source vs.
    output side by side without opening either file."""
    out_dir = Path(out_dir)
    for i, candidate in enumerate(candidates):
        page, bbox = candidate["page"], candidate["bbox"]
        source_page = source_page_by_output_slide.get(page)
        if source_page is not None:
            _save_scaled_crop(
                Path(source_pages_dir) / f"{source_page:02d}.png", bbox, canvas_width_emu, canvas_height_emu,
                out_dir / f"p{page:02d}_{i:03d}_source_p{source_page:02d}.png",
            )
        if output_pages_dir is not None:
            _save_scaled_crop(
                Path(output_pages_dir) / f"{page:02d}.png", bbox, canvas_width_emu, canvas_height_emu,
                out_dir / f"p{page:02d}_{i:03d}_output.png",
            )
