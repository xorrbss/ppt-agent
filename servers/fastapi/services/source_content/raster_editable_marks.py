"""Pixel grounding for punctuation marks the OCR dropped or misread.

Tesseract routinely loses small separator marks: a middle dot between words
("PART II · GEOGRAPHY" read as "PART II GEOGRAPHY"), a list bullet, or reads
them as another symbol ("-", "*", "."). The model transcription keeps them, so
the two readers "disagree" on clean text and the line stays pixels. A mark the
model adds is still never trusted on its own ("구성해드리:" was a hallucinated
colon); it is grounded only by the source itself:

* an inserted mark at a boundary between two OCR words (or just before the
  first / after the last word) is grounded when that gap holds exactly one
  small, isolated ink glyph of the mark's shape;
* a middle dot / bullet the OCR read as another single symbol ("-", "*", ".")
  counts as the same mark for the comparison (same position, both readers saw a
  mark); whether its glyph really is a dot is judged by its pixels before the line
  is published (``raster_symbol_grounding``).

Marks between two digits are never grounded here: a separator inside a number
("1.5" vs "1·5") is a number question for the number gate. ``grounded_view``
returns the transcript with grounded marks rewritten to what the OCR read there,
for the agreement comparison only; the published text is still the model's
transcript and still passes every number and symbol guard.
"""

from __future__ import annotations

import difflib

from services.source_content.raster_symbol_grounding import gap_holds, reading_chars
from services.source_content.raster_text_correction import normalized

DOT_MARKS = "·"  # normalized form of middle dots and bullets (•, ㆍ, ・, ∙)
COLON_MARKS = ":;"
# Single OCR glyphs a middle dot or bullet is commonly read as.
DOT_MISREADS = frozenset("-.*'`,°")


def _between_digits(reading: str, start: int, end: int) -> bool:
    before = reading[start - 1] if start > 0 else ""
    after = reading[end] if end < len(reading) else ""
    return before.isdigit() and after.isdigit()


def grounded_view(page, carried: tuple[int, ...], transcript: str) -> str:
    """The normalized transcript with every pixel-grounded mark rewritten to the
    OCR's reading at that spot (removed where the OCR read nothing)."""

    chars = reading_chars(page.words, carried, normalized)
    reading = "".join(ch for ch, _ in chars)
    text = normalized(transcript)
    if not chars or not text:
        return text
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, reading, text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        segment = text[j1:j2]
        if tag == "equal" or len(segment) != 1 or segment not in DOT_MARKS + COLON_MARKS:
            out.append(segment)
            continue
        if tag == "insert" and not _between_digits(reading, i1, i2):
            left = chars[i1 - 1][1] if i1 > 0 else None
            right = chars[i1][1] if i1 < len(chars) else None
            if left != right and gap_holds(
                page,
                page.words[left].bbox if left is not None else None,
                page.words[right].bbox if right is not None else None,
                segment,
            ):
                continue  # the pixels show the mark the OCR dropped
        if (
            tag == "replace"
            and segment in DOT_MARKS
            and i2 - i1 == 1
            and reading[i1] in DOT_MISREADS
            and not _between_digits(reading, i1, i2)
        ):
            out.append(reading[i1])  # the OCR saw a mark here, only its shape differs
            continue
        out.append(segment)
    return "".join(out)
