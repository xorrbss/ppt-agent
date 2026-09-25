"""Wrong-number footer false-positive classification (2026-09-25 follow-up).

`ground_truth_checker.py`'s `numeric_exactness` over-reports "fabricated"
numbers for any deck whose auto_redesign output adds its own page-number
footer (e.g. "01", "02", ...) that wasn't already baked into the *original*
deck's text. `ground_truth_checker.py` itself is left unmodified (scoring
logic stays identical); `classify_wrong_numbers()` here splits each page's
raw `fabricated_numbers` into `filtered` (excluded false positives) and
`remaining` (kept, real hard fails), applying all three conditions together,
never on value alone:

  1. the token equals the *output slide's own* 1-based page number, zero-
     padded or not (e.g. "4" or "04" for slide 4);
  2. that page's real source ground truth does NOT contain the token (so a
     deck whose original text legitimately included that number, e.g. as
     part of a real page range, is not filtered); and
  3. the token appears as a standalone page-number-shaped shape/run on that
     slide -- either the *entire* text of some shape, or a short (<=4 char)
     run inside a shape sitting in the top/bottom 12% band of the slide.

See README.md for the reasoning and worked examples.
"""
from __future__ import annotations

import re
from collections import Counter

_DIGITS_RE = re.compile(r"^\d{1,4}$")
FOOTER_BAND_FRACTION = 0.12  # top/bottom 12% of the slide


def _page_number_forms(page_number: int) -> set:
    return {str(page_number), f"{page_number:02d}"}


def _footer_candidate_counter(slide, slide_height: int) -> Counter:
    """Counts, per numeric string value, how many shapes/runs on this slide
    could plausibly BE a standalone page-number stamp: either a shape whose
    entire text is just that number, or a short (<=4 char) run of just that
    number inside a shape sitting in the top/bottom 12% band of the slide."""
    counter: Counter = Counter()
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        parts = [run.text for para in shape.text_frame.paragraphs for run in para.runs if run.text]
        whole = "".join(parts).strip()
        if whole and _DIGITS_RE.match(whole):
            counter[whole] += 1
            continue  # whole-shape match already counted; don't also count its runs below

        try:
            top, height = shape.top, shape.height
        except Exception:
            top = height = None
        if top is None or height is None or not slide_height:
            continue
        center_frac = (top + height / 2) / slide_height
        in_band = center_frac <= FOOTER_BAND_FRACTION or center_frac >= (1 - FOOTER_BAND_FRACTION)
        if not in_band:
            continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                t = (run.text or "").strip()
                if t and len(t) <= 4 and _DIGITS_RE.match(t):
                    counter[t] += 1
    return counter


def classify_wrong_numbers(slide, slide_height: int, page_number: int,
                            fabricated: list, source_numeric_tokens: list) -> tuple:
    """Splits a slide's raw `fabricated_numbers` list into (filtered, remaining):
    `filtered` are tokens that equal the slide's own 1-based page number (zero-
    padded or not), are NOT present in that page's real source numeric tokens,
    AND appear as a standalone page-number-shaped shape/run per
    `_footer_candidate_counter`. Everything else stays in `remaining` (a real
    hard fail)."""
    forms = _page_number_forms(page_number)
    src_set = set(source_numeric_tokens)
    candidates = _footer_candidate_counter(slide, slide_height)
    fab_counter = Counter(fabricated)
    filtered: list = []
    remaining: list = []
    for tok, cnt in fab_counter.items():
        if tok in forms and tok not in src_set:
            avail = candidates.get(tok, 0)
            take = min(avail, cnt)
            filtered.extend([tok] * take)
            remaining.extend([tok] * (cnt - take))
        else:
            remaining.extend([tok] * cnt)
    return filtered, remaining
