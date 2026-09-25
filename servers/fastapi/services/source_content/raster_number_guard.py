"""Number guard for plans built without the vision structurer.

The heuristic plan has no second reader, so every number is re-read as the whole
visual number it belongs to (``raster_number_units``): digit words are grouped
with the adjacent glyphs of the same row, the unit's box covers glyphs OCR
dropped, and the unit is read once as one line. A text region is published only
when the numbers of its text equal, in order, the reads of its units (so neither
a fragment "0" of "01" nor "2 0 %" for "20%" passes), no unit is wrapped over rows
or shared with another region, and no digit word is doubtful to the engine. A
native table must hold exactly its units' confirmed numbers. Otherwise the
region, or the table, stays pixels as one piece.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from services.source_content.raster_number_units import sequence_confirmed, text_numbers
from services.source_content.raster_text_guards import HEURISTIC_MIN_CONFIDENCE, read_number_units


def _digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _guarded(region) -> bool:
    if region.editable_fallback:
        return False
    return region.role in ("title", "body") or (region.role == "table" and region.table_cells is not None)


def _confirmed(region, words, reads) -> bool:
    digits = [k for k in region.word_indices if _digit(words[k].text)]
    if not digits:
        return True
    if reads is None:
        return False  # no local OCR engine: nothing can confirm a number
    if any(words[k].confidence is not None and words[k].confidence < HEURISTIC_MIN_CONFIDENCE for k in digits):
        return False
    carried = set(region.word_indices)
    position = {k: n for n, k in enumerate(region.word_indices)}
    mine = [read for read in reads if carried & set(read.unit.words)]
    if any(not set(read.unit.words) <= carried for read in mine):
        return False  # one visual number split across regions
    mine.sort(key=lambda read: min(position[k] for k in read.unit.words))
    if region.role != "table":
        return sequence_confirmed(text_numbers(region.text or ""), mine, allow_skip=False)
    if not all(sequence_confirmed(text_numbers(read.unit.text), (read,), allow_skip=False) for read in mine):
        return False
    published = Counter(number for row in region.table_cells for cell in row for number, _ in text_numbers(cell))
    confirmed = Counter(number for read in mine for number, _ in text_numbers(read.unit.text))
    return published == confirmed


def _pixels(region):
    if region.role == "table":
        return replace(
            region,
            text="\n".join("\t".join(row) for row in region.table_cells or ()),
            table_cells=None,
            editable_fallback=True,
            review_reason="unconfirmed_number",
        )
    return replace(
        region,
        role="screenshot",
        editable_fallback=True,
        reason="paragraph_pixels",
        review_reason="unconfirmed_number",
    )


def guard_numbers(plan, words, image, executable, tessdata):
    digit_words = sorted(
        {k for region in plan.regions if _guarded(region) for k in region.word_indices if _digit(words[k].text)}
    )
    if not digit_words:
        return plan
    reads = read_number_units(image, words, digit_words, executable=executable, tessdata=tessdata)
    regions = tuple(
        _pixels(region) if _guarded(region) and not _confirmed(region, words, reads) else region
        for region in plan.regions
    )
    return replace(plan, regions=regions)
