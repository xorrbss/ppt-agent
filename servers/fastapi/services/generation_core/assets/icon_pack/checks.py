"""Acceptance checks for a generated icon or cutout.

Two things disqualify a picture from a pack: an opaque or haloed background,
which would paint a rectangle or a glow over the slide, and baked-in text,
which the slide cannot edit and the export cannot make native.

Transparency is a pure alpha measurement. Text is asked of the production OCR
runtime (tesseract, ``kor+eng``) when it is present. OCR on a pictogram is
noisy - calibrated on 24 real cells plus a cutout
(``artifacts/icon-pack-verification-20260903/manifest.json``) it read two to
four stray glyphs off bar charts and calendars while missing nothing that was
actually text - so the decision is not "any glyph" but two targeted rules:

* the OCR text contains one of the concept names the picture was asked to
  symbolize (the one string an image model is tempted to caption with) -
  whole, or any one of its words, since OCR misreads a syllable of a
  two-word Korean label often enough that the surviving word must count; or
* it contains a Latin word of five or more letters (pictogram noise topped
  out at four; a real caption such as ``Gateway`` reads back whole).

Hangul runs are not scored on length: Korean labels are two to four
syllables, exactly the length of OCR noise, which is why the concept-name
rule exists. When OCR is unavailable the check passes with ``method`` set to
``heuristic`` so the pack index records that it was not the verified check.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Literal, Sequence

from PIL import Image

from services.generation_core.assets.icon_pack.sheet import (
    has_alpha_channel,
    open_rgba,
    transparent_fraction,
)

#: A cutout or icon must leave at least this much of its canvas see-through.
MIN_TRANSPARENT_FRACTION = 0.20
#: ...and must actually paint something.
MAX_TRANSPARENT_FRACTION = 0.995
#: Anti-aliased edges are 1-2% of a clean picture; a soft glow or a faded
#: backdrop is far more, and would show on the slide as a halo.
MAX_PARTIAL_ALPHA_FRACTION = 0.15
#: A Latin run this long read off a picture is a word, not noise.
LATIN_RUN_THRESHOLD = 5
#: Concept names shorter than this are not matched: one syllable is noise.
_MIN_TERM_CHARS = 2
_ALPHA_FLOOR = 24
_ALPHA_CEILING = 232
_LATIN_RUNS = re.compile(r"[A-Za-z]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class TransparencyCheck:
    passed: bool
    has_alpha: bool
    transparent_fraction: float
    partial_alpha_fraction: float
    corners_transparent: bool

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "has_alpha": self.has_alpha,
            "transparent_fraction": round(self.transparent_fraction, 4),
            "partial_alpha_fraction": round(self.partial_alpha_fraction, 4),
            "corners_transparent": self.corners_transparent,
        }


def partial_alpha_fraction(image: Image.Image) -> float:
    histogram = image.getchannel("A").histogram()
    total = image.size[0] * image.size[1]
    return sum(histogram[_ALPHA_FLOOR:_ALPHA_CEILING]) / float(total) if total else 0.0


def check_transparency(payload: bytes) -> TransparencyCheck:
    if not has_alpha_channel(payload):
        return TransparencyCheck(False, False, 0.0, 0.0, False)
    image = open_rgba(payload)
    fraction = transparent_fraction(image)
    partial = partial_alpha_fraction(image)
    width, height = image.size
    alpha = image.getchannel("A")
    corners = all(
        alpha.getpixel(point) < _ALPHA_FLOOR
        for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    )
    passed = (
        corners
        and MIN_TRANSPARENT_FRACTION <= fraction <= MAX_TRANSPARENT_FRACTION
        and partial <= MAX_PARTIAL_ALPHA_FRACTION
    )
    return TransparencyCheck(passed, True, fraction, partial, corners)


TextCheckMethod = Literal["ocr", "heuristic"]


@dataclass(frozen=True)
class TextCheck:
    passed: bool
    method: TextCheckMethod
    detail: str
    matched_term: str | None = None
    longest_latin_run: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "method": self.method,
            "detail": self.detail,
            "matched_term": self.matched_term,
            "longest_latin_run": self.longest_latin_run,
        }


def _squash(text: str) -> str:
    return _WHITESPACE.sub("", text or "").casefold()


def longest_latin_run(text: str) -> int:
    return max((len(run) for run in _LATIN_RUNS.findall(text or "")), default=0)


def _needles(term: str) -> list[str]:
    """The whole term and each of its words: OCR misreads one syllable of a
    two-word label often enough that the surviving word has to count."""
    whole = _squash(term)
    words = [_squash(word) for word in (term or "").split()]
    return [n for n in [whole, *words] if len(n) >= _MIN_TERM_CHARS]


def find_drawn_text(text: str, forbidden: Sequence[str]) -> TextCheck:
    """Decide from OCR output alone; exposed so the rules are unit-testable."""
    squashed = _squash(text)
    for term in forbidden:
        if any(needle in squashed for needle in _needles(term)):
            return TextCheck(False, "ocr", "concept_name_drawn", term, longest_latin_run(text))
    run = longest_latin_run(text)
    if run >= LATIN_RUN_THRESHOLD:
        return TextCheck(False, "ocr", "latin_word_drawn", None, run)
    return TextCheck(True, "ocr", "no_text_found", None, run)


def flatten_on_white(payload: bytes) -> bytes:
    """OCR sees transparency as black; composite the picture on white first."""
    image = open_rgba(payload)
    canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
    canvas.alpha_composite(image)
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


async def check_no_text(
    payload: bytes,
    *,
    forbidden: Sequence[str] = (),
    language: str = "kor+eng",
) -> TextCheck:
    """Reject a picture in which OCR reads a concept name or a Latin word.

    ``forbidden`` is every string the model was shown for this picture - the
    concepts of a sheet, the takeaway and node labels of a cutout.
    """
    try:
        from utils.authored_ocr_quality import inspect_authored_ocr

        result = await inspect_authored_ocr(
            flatten_on_white(payload), language=language, psm=11
        )
    except Exception as error:  # noqa: BLE001 - OCR must never break a pack
        return TextCheck(True, "heuristic", f"ocr_unavailable:{type(error).__name__}")
    if not result.available or result.text is None:
        return TextCheck(True, "heuristic", f"ocr_{result.status}")
    return find_drawn_text(result.text, forbidden)


__all__ = [
    "LATIN_RUN_THRESHOLD",
    "MAX_PARTIAL_ALPHA_FRACTION",
    "MIN_TRANSPARENT_FRACTION",
    "TextCheck",
    "TransparencyCheck",
    "check_no_text",
    "check_transparency",
    "find_drawn_text",
    "flatten_on_white",
    "longest_latin_run",
    "partial_alpha_fraction",
]
