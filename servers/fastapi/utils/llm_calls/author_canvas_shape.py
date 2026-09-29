"""Adapt 1280x720-worded authoring rules to the deck's actual canvas.

Every prompt profile states its rules against a 1280x720 slide, including
frozen historical ones, so the adaptation is a transform over whatever rules
text a profile supplies rather than placeholders inside that text. The
1280x720 canvas itself is left untouched, byte for byte.
"""

from __future__ import annotations

from math import gcd
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_SIZE_PX, DEFAULT_CANVAS_WIDTH_PX

BASELINE_SIZE = DEFAULT_CANVAS_SIZE_PX

BASELINE = "baseline"
PORTRAIT = "portrait"
SQUARE = "square"
ULTRAWIDE = "ultrawide"
WIDE_LANDSCAPE = "wide-landscape"

_SHAPE_WORDS = {PORTRAIT: "portrait ", SQUARE: "square ", ULTRAWIDE: "ultrawide "}


def classify_canvas_shape(width: int, height: int) -> str:
    if (width, height) == BASELINE_SIZE:
        return BASELINE
    ratio = width / height
    if ratio < 0.95:
        return PORTRAIT
    if ratio <= 1.1:
        return SQUARE
    if ratio >= 1.9:
        return ULTRAWIDE
    return WIDE_LANDSCAPE


def aspect_ratio_label(width: int, height: int) -> str:
    divisor = gcd(width, height)
    a, b = width // divisor, height // divisor
    return f"{a}:{b}" if a <= 32 and b <= 32 else f"{width}x{height}"


def baseline_replacements(width: int, height: int) -> tuple[tuple[str, str], ...]:
    shape = classify_canvas_shape(width, height)
    if shape == BASELINE:
        return ()
    label = aspect_ratio_label(width, height)
    return (
        ("single 16:9 slide", f"single {label} {_SHAPE_WORDS.get(shape, '')}slide"),
        ("1280x720px", f"{width}x{height}px"),
        ("1280x720", f"{width}x{height}"),
        ("y=648px", f"y={height - 72}px"),
        ("y=688px", f"y={height - 32}px"),
        ("y=749", f"y={height + 29}"),
        ("on a 720px canvas", f"on a {height}px canvas"),
        ("an element is never 1280px wide", f"an element is never {width}px wide"),
        # The rules wrap "a 72px inset on each side leaves\n  1136px" across a
        # line, so only the number itself can be matched.
        ("1136px", f"{width - 144}px"),
        ("width:1280px", f"width:{width}px"),
    )


BASELINE_TOKENS: tuple[str, ...] = tuple(
    old for old, _ in baseline_replacements(DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX)
)


def apply_canvas_tokens(rules: str, width: int, height: int) -> str:
    for old, new in baseline_replacements(width, height):
        rules = rules.replace(old, new)
    return rules


def composition_clause(width: int, height: int) -> str:
    shape = classify_canvas_shape(width, height)
    if shape == BASELINE:
        return ""
    size = f"{width}x{height}"
    lead = f"\n- {shape.upper()} COMPOSITION (authoritative for this deck): use the full {size} canvas"
    if shape == PORTRAIT:
        return (
            f"{lead} without rotation or landscape scaling. Place cover titles "
            "and subtitles toward the top; use one readable vertical body column; stack "
            "comparisons and three-card groups vertically; keep images and charts within "
            f"the {width - 128}px safe content width with captions below; and compose closing "
            "messages and next actions as a vertical sequence.\n"
        )
    if shape == SQUARE:
        return (
            f"{lead}. Build each slide on one focal column or a 2x2 grid, never three or "
            "more columns; set comparisons as a side-by-side pair or stack them; keep every "
            f"line of text within the {width - 128}px safe content width; and order the page "
            "vertically: headline, then the figure, then the takeaway.\n"
        )
    if shape == ULTRAWIDE:
        return (
            f"{lead}. Never leave one long empty horizontal band: divide the width into "
            "2-3 columns, or a wide diagram with a narrow text rail beside it; keep each "
            "paragraph at most about 560px wide; and run the headline across the full width "
            "or over the first column only.\n"
        )
    return (
        f"{lead}. It is narrower for its height than a 16:9 slide, so prefer one or two "
        "columns over three, and keep the same margins rather than setting smaller type "
        "to fit more across.\n"
    )


__all__ = [
    "BASELINE_TOKENS",
    "aspect_ratio_label",
    "apply_canvas_tokens",
    "baseline_replacements",
    "classify_canvas_shape",
    "composition_clause",
]
