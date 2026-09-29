"""Exact text advances in the bundled SVG fonts, for the PPTX converter.

svg decks are authored, rendered and reviewed in the bundled families only
(``services.svg_default_fonts`` normalizes every model-authored family to one
of them), so their glyph advances are known exactly. The vendored converter
otherwise sizes single-line text frames from a per-character estimate, and a
frame narrower than the text makes renderers that ignore ``wrap="none"``
(LibreOffice) fold the line -- "68%" became "68" over "%".

``text_advance`` follows the converter's ``TextAdvance`` contract: the advance
in canvas units, or ``None`` whenever the answer would not be exact (a family
that is not bundled, a weight the family cannot render, a glyph it lacks), so
the converter keeps its estimate for that line.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from services.svg_trusted_fonts import (
    FONT_FAMILY as PRETENDARD_FAMILY,
    _nearest_declared_weight,
    declared_weights,
    read_trusted_pretendard_faces,
)
from utils.svg_template_fonts import FONT_FAMILY as NOTO_FAMILY, font_material

#: Advances are read at this pixel size and scaled linearly. FreeType rounds a
#: hinted advance to whole pixels, so a large reference keeps that rounding
#: under 1/1000 em per glyph instead of up to half a pixel at text size.
_REFERENCE_SIZE_PX = 1000
_NOTO_WEIGHT_RANGE = (100, 900)
_CSS_WEIGHT_KEYWORDS = {"normal": 400, "bold": 700}


def _primary_family(font_family: str) -> str | None:
    """The first family of a CSS stack, matched to a bundled family."""

    for candidate in font_family.split(","):
        name = candidate.strip().strip("'\"")
        if not name:
            continue
        for bundled in (NOTO_FAMILY, PRETENDARD_FAMILY):
            if name.casefold() == bundled.casefold():
                return bundled
        return None
    return None


def _css_weight(font_weight: str) -> int | None:
    raw = font_weight.strip().casefold()
    if raw in _CSS_WEIGHT_KEYWORDS:
        return _CSS_WEIGHT_KEYWORDS[raw]
    try:
        weight = float(raw)
    except ValueError:
        return None
    if not weight.is_integer() or not 1 <= weight <= 1000:
        return None
    return int(weight)


def _rendered_weight(family: str, weight: int) -> int | None:
    """The weight the renderer draws: exact on the variable face, else the
    same nearest-declared rule the trusted renderer normalizes with."""

    if family == NOTO_FAMILY:
        low, high = _NOTO_WEIGHT_RANGE
        return weight if low <= weight <= high else None
    declared = declared_weights(family)
    return None if declared is None else _nearest_declared_weight(declared, weight)


def _payload(family: str, weight: int) -> bytes:
    if family == NOTO_FAMILY:
        return font_material(NOTO_FAMILY)[1]
    return next(data for face_weight, _digest, data in read_trusted_pretendard_faces() if face_weight == weight)


@lru_cache(maxsize=16)
def _face(family: str, weight: int):
    """``(PIL face at the reference size, covered code points)``."""

    from fontTools.ttLib import TTFont
    from PIL import ImageFont

    payload = _payload(family, weight)
    face = ImageFont.truetype(BytesIO(payload), size=_REFERENCE_SIZE_PX)
    if family == NOTO_FAMILY:
        face.set_variation_by_axes([weight])
    with TTFont(BytesIO(payload), lazy=True) as font:
        coverage = frozenset(font.getBestCmap())
    return face, coverage


def text_advance(text: str, font_size: float, font_weight: str, font_family: str) -> float | None:
    """Advance of ``text`` in canvas units, or ``None`` when not exactly known."""

    family = _primary_family(font_family)
    weight = _css_weight(font_weight)
    if family is None or weight is None or not font_size > 0:
        return None
    rendered = _rendered_weight(family, weight)
    if rendered is None:
        return None
    face, coverage = _face(family, rendered)
    if any(ord(char) not in coverage for char in text):
        return None
    return face.getlength(text) * font_size / _REFERENCE_SIZE_PX


__all__ = ["text_advance"]
