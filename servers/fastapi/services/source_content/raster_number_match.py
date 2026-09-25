"""Matching a reading's numbers against the reads of their whole visual numbers.

A unit read confirms a run of the reading's numbers in one of two ways
(``raster_number_units.UnitRead``):

* its whole-unit read equals them (the English model may append digits for a Hangul
  glyph right after a number: "40,000원" read "40,0008");
* one of its suffix splits (``raster_number_suffix``) confirms them: the head holds
  one number, its read equals it, and the reading continues right after the number
  with the Hangul the Korean reader read in the tail ("약 7.9억원": head "7.9", tail
  "억원"). The head's glyph shape lets a read that lost a decimal point both
  readers dropped ("59" for "5.9") confirm the number when the glyphs show the
  point, and keeps such a read from confirming a reading that lost it too ("125"
  for a "12.5" on the page).
"""

from __future__ import annotations

import re
from typing import Sequence

NUMBER = re.compile(r"\d[\d,.]*%?")
_HANGUL_RUN = re.compile(r"[가-힣]*")


class TextNumber(tuple):
    """A number token of a reading as ``(number, hangul_follows)``; ``suffix`` is the
    Hangul run written right after it ("억원" in "3.9억원", "" when none)."""

    suffix: str = ""

    def __new__(cls, number: str, suffix: str = ""):
        value = super().__new__(cls, (number, bool(suffix)))
        value.suffix = suffix
        return value

    def __reduce__(self):
        return (TextNumber, (self[0], self.suffix))


def text_numbers(text: str) -> list[TextNumber]:
    """Number tokens of a reading in order, with whether a Hangul glyph follows each.

    Spaces OCR put inside a number next to a separator ("9, 860") are ignored;
    other spaces separate numbers ("Q3 2025" holds two)."""

    value = re.sub(r"(?<=\d)\s+(?=[,.%]\d?)|(?<=\d[,.])\s+(?=\d)", "", text)
    return [TextNumber(m.group(), _HANGUL_RUN.match(value, m.end()).group()) for m in NUMBER.finditer(value)]


def number_matches(number: str, read: str, hangul_follows: bool) -> bool:
    """A read confirms a number when equal; the English model may append digits for a
    Hangul glyph right after the number ("40,000원" read "40,0008")."""

    if read == number:
        return True
    return hangul_follows and read.startswith(number) and read[len(number) :].rstrip(",.").isdigit()


def number_shape(number: str) -> str:
    """Glyph kinds a number is drawn with: "d" a full-height glyph, "." / "," its separators."""

    return "".join("d" if ch.isdigit() or ch == "%" else ch if ch in ".," else "?" for ch in number)


def head_matches(number: str, read: str, shape: str) -> bool:
    """A split head read confirms a number when it equals the number and the head's
    glyphs (``shape``) show no more decimal points than the number has ("125" read
    over a "12.5" confirms neither), or when it is the number without its decimal
    points and the glyphs have exactly the number's shape after symbols only.

    Glyphs of small type may touch (one run for "84"), so an equal read needs no
    full shape; a restored point does."""

    if read == number:
        return shape.count(".") <= number.count(".")
    if "." not in number or read != number.replace(".", ""):
        return False
    kinds = number_shape(number)
    return shape.endswith(kinds) and "d" not in shape[: len(shape) - len(kinds)]


def sequence_confirmed(numbers: Sequence[tuple[str, bool]], reads: Sequence, *, allow_skip: bool) -> bool:
    """The numbers of a reading equal, in order, the numbers of its units' reads.

    Each unit confirms a contiguous run of the numbers; a wrapped or unreadable unit
    confirms nothing. With ``allow_skip`` a unit may be left out (the reading omits
    that number), but every number of the reading must be confirmed by a unit."""

    options = []
    for read in reads:
        splits = list(read.suffixed())
        # A unit read split before its unit word has a read of the number alone: the
        # digits the English model may append for Hangul are then no longer allowed
        # ("2.0억원" read "204" would otherwise confirm "20억원").
        whole = "" if splits else None
        options.append([(sequence, whole, "") for sequence in read.readings()] + splits)

    def fits(position: int, sequence: tuple[str, ...], suffix: str | None, shape: str) -> bool:
        """``suffix`` None: a whole-unit read that may carry appended digits; "": one that
        must equal the numbers; else a split head read followed by that unit word."""

        end = position + len(sequence)
        if not sequence or end > len(numbers):
            return False
        pairs = list(zip(numbers[position:end], sequence, strict=True))
        if suffix is None or suffix == "":
            lenient = suffix is None
            return all(number_matches(number[0], token, number[1] and lenient) for number, token in pairs)
        # The tail's Hangul must be what the reading writes right after the number.
        if not getattr(numbers[end - 1], "suffix", "").startswith(suffix):
            return False
        return len(pairs) == 1 and head_matches(pairs[0][0][0], pairs[0][1], shape)

    def match(position: int, unit: int) -> bool:
        if unit == len(options):
            return position == len(numbers)
        if allow_skip and match(position, unit + 1):
            return True
        return any(
            fits(position, sequence, suffix, shape) and match(position + len(sequence), unit + 1)
            for sequence, suffix, shape in options[unit]
        )

    return match(0, 0)
