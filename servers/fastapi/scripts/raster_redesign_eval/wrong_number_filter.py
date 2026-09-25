"""Wrong-number footer false-positive classification (2026-09-25 follow-up).

`ground_truth_checker.py`'s `numeric_exactness` over-reports "fabricated"
numbers for any deck whose auto_redesign output adds its own page-number
footer (e.g. "01", "02", ...) that wasn't already baked into the *original*
deck's text. `ground_truth_checker.py` itself is left unmodified (scoring
logic stays identical); `classify_wrong_numbers()` here splits each page's
raw `fabricated_numbers` into `filtered` (excluded false positives) and
`remaining` (kept, real hard fails).

`fabricated` is `numeric_exactness()`'s own multiset EXCESS -- for a token
that appears N times in the output and M times in source, `fabricated`
already contains it exactly `max(0, N-M)` times, not N times (`missing`
gets the opposite excess, `max(0, M-N)`). So `classify_wrong_numbers` is
**count-based by construction**: it filters only up to that excess count,
never touching the M occurrences that already matched real source content.
A token is filtered from that excess, one occurrence at a time, when it:

  1. equals the *output slide's own* 1-based page number, zero-padded or
     not (e.g. "4" or "04" for slide 4); and
  2. appears as a standalone page-number-shaped shape/run on that slide --
     either the *entire* text of some shape, or a short (<=4 char) run
     inside a shape sitting in the top/bottom 12% band of the slide
     (`_footer_candidate_counter`), capped at `min(available candidates,
     excess count)` so a legitimately-repeated source number is never
     over-filtered.

**2026-09-25 count-based fix:** an earlier version of condition 1 above
also required the token to be ABSENT from the page's source numeric tokens
at all (`tok not in set(source_numeric_tokens)`), on the theory that a
token appearing anywhere in source must be a legitimate repeat. That
double-counted `numeric_exactness()`'s own multiset accounting and broke
on a real case: deck3 slide 14's editable text has "14" three times ("PART
IV - REVENUE BRIDGE - 14" kicker, "14 / 20", and the redesign's own
bottom-right page-number stamp) while the source page has it twice (the
kicker and "14 / 20" only) -- `fabricated_numbers` already correctly
narrows this to a single excess "14" (3-2), but the old set-membership
check refused to filter it at all, since "14" is *present* in source (just
not three times). Removed: `fabricated`'s own multiset excess already
means "not explained by source count", so no additional source-membership
check is needed or correct here.

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


def classify_wrong_numbers(slide, slide_height: int, page_number: int, fabricated: list) -> tuple:
    """Splits a slide's raw `fabricated_numbers` EXCESS list (see module
    docstring -- already source-count-aware) into (filtered, remaining):
    `filtered` is the portion of each token's excess that equals the
    slide's own 1-based page number AND is explained by a standalone
    page-number-shaped shape/run (`_footer_candidate_counter`), capped at
    however many such candidates actually exist. Everything else stays in
    `remaining` (a real hard fail)."""
    forms = _page_number_forms(page_number)
    candidates = _footer_candidate_counter(slide, slide_height)
    fab_counter = Counter(fabricated)
    filtered: list = []
    remaining: list = []
    for tok, cnt in fab_counter.items():
        if tok in forms:
            avail = candidates.get(tok, 0)
            take = min(avail, cnt)
            filtered.extend([tok] * take)
            remaining.extend([tok] * (cnt - take))
        else:
            remaining.extend([tok] * cnt)
    return filtered, remaining


# --------------------------------------------------------------------------
# source-side (ground-truth) footer exclusion, for the number-gate metrics
# (source_numbers_total / correct_numbers_editable / correct_numbers_pixel,
# 2026-09-25 follow-up)
# --------------------------------------------------------------------------

_FOOTER_FRACTION_RE = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})(?!\d)")


def gt_footer_token_counts(text: str, page_number: int, total_pages: int) -> Counter:
    """Counts tokens belonging to a "N / total" page-footer stamp matching
    THIS page's own position (e.g. "01 / 15" on page 1 of a 15-page deck --
    the documented deck1 pattern, see README "Wrong-number footer false
    positives"). ground_truth.json's `numeric_tokens` come from a flat,
    position-blind whole-page regex over the source text (no shape/bbox
    info survives into that fixture, unlike the output pptx's real shapes
    `classify_wrong_numbers` can inspect), so this is a text-pattern
    heuristic, not a structural one: only the exact "this page / this
    deck's page count" fraction is excluded, not any number that merely
    happens to equal the page number, so a real content number is never
    dropped by coincidence."""
    counts: Counter = Counter()
    for m in _FOOTER_FRACTION_RE.finditer(text or ""):
        left, right = m.group(1), m.group(2)
        try:
            left_n, right_n = int(left), int(right)
        except ValueError:
            continue
        if left_n == page_number and right_n == total_pages:
            counts[left] += 1
            counts[right] += 1
    return counts


def number_gate_counts(gt_numeric_tokens: list, gt_text_normalized: str, page_number: int,
                        total_pages: int, output_number_tokens: Counter) -> tuple:
    """For the "how many correct numbers went to pixels" gate: returns
    (source_numbers_total, correct_numbers_editable) for one page.
    `source_numbers_total` is the page's real GT number-token count with its
    own page-number footer excluded (`gt_footer_token_counts`).
    `correct_numbers_editable` is how many of those are found, as a
    multiset, in `output_number_tokens` (the output slide's own editable
    text number tokens -- same matching as `ground_truth_checker.
    numeric_exactness`, just against the footer-filtered source list, so a
    footer digit is never counted as either a correct-editable or a
    went-to-pixels number)."""
    raw = Counter(gt_numeric_tokens)
    footer = gt_footer_token_counts(gt_text_normalized, page_number, total_pages)
    filtered = raw - footer  # Counter subtraction floors each count at 0
    total = sum(filtered.values())
    matched = sum(min(c, output_number_tokens.get(tok, 0)) for tok, c in filtered.items())
    return total, matched
