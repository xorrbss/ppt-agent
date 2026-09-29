"""Rewrite the 16:9-worded style-brief lines the svg author receives.

Every ``authored_styles/*.yaml`` brief states its type sizes and composition
against a 1280x720 slide ("At 1280x720, ...", "Compose for a fixed 16:9
canvas"). The HTML author rewrites its own rules through
``utils.llm_calls.author_canvas_shape.apply_canvas_tokens``; nothing did the
same for the svg ``common_guidance`` lines, so an A4 or square svg deck was
told to compose at 1280x720. This module is the svg counterpart, applied at
the single point where every svg brief line passes
(``project_style.project_style``). The 1280x720 baseline is left byte for
byte untouched: ``svg_brief_replacements`` is empty there, so the 16:9
goldens and every 16:9 ``style_digest`` do not move.

Pixel type sizes that follow a rewritten phrase ("body 22-28px") are not
rescaled, matching the HTML path; the body-font floor is the only size
authority. Only ``common_guidance`` is rewritten: ``svg_overrides`` are the
style author's explicit svg text and pass through unchanged.

Design: ``docs/svg-canvas-brief-rewrite-and-experiment-plan-20260929.md`` §1.3-1.5.
"""
from __future__ import annotations

from utils.llm_calls.author_canvas_shape import (
    BASELINE,
    aspect_ratio_label,
    classify_canvas_shape,
    shape_word,
)

SVG_CANVAS_BRIEF_VERSION = "svg-canvas-brief/v1"


def svg_brief_replacements(width: int, height: int) -> tuple[tuple[str, str], ...]:
    """R1..R9 for this canvas; ``()`` at the 1280x720 baseline.

    Specific phrases first (they carry the shape word), then the bare tokens
    as a safety net so a future YAML edit cannot reintroduce an anchor
    unnoticed.
    """
    if classify_canvas_shape(width, height) == BASELINE:
        return ()
    size = f"{width}x{height}"
    label = aspect_ratio_label(width, height)
    sw = shape_word(width, height)
    return (
        # R1 (class A): "At 1280x720, use the Korean-safe stack ..."
        ("At 1280x720, ", f"At {size}, "),
        # R2 (class B): "... At a 1280x720 canvas, target body 22-28px ..."
        ("At a 1280x720 canvas, ", f"At a {size} {sw}canvas, "),
        # R3 (class D): signal "Scale on the 1280x720 canvas: display 120px ..."
        ("Scale on the 1280x720 canvas:", f"Scale on the {size} canvas:"),
        # R4-R6 (class C): composition anchors.
        ("Compose for a fixed 16:9 canvas", f"Compose for a fixed {label} {sw}canvas"),
        ("Work on a disciplined 16:9 grid", f"Work on a disciplined {label} {sw}grid"),
        ("Use a wide 16:9 corporate grid", f"Use a {label} {sw}corporate grid"),
        # R7-R9: residual tokens.
        ("1280x720", size),
        ("16:9", label),
        ("1280 by 720", f"{width} by {height}"),
    )


def adapt_brief_line(text: str, width: int, height: int) -> str:
    """Apply the replacement table to one ``common_guidance`` line."""
    for old, new in svg_brief_replacements(width, height):
        text = text.replace(old, new)
    return text


def reference_caveat(width: int, height: int) -> str:
    """Rule line telling the author that style references are on other page shapes.

    Conditioned on the canvas shape only (not on whether references exist),
    so every deck on a given canvas carries identical rules text. Empty at
    the 1280x720 baseline. Appended after ``composition_clause``, which ends
    with a newline.
    """
    if classify_canvas_shape(width, height) == BASELINE:
        return ""
    return (
        "- REFERENCE IMAGES: the attached style references are art-direction samples drawn on other "
        "page shapes (mostly 16:9). Take palette, type, component treatment and imagery tone from them; "
        f"never their proportions, column count or frame. Fill the {width}x{height} canvas; do not "
        "letterbox it to match a reference.\n"
    )


__all__ = [
    "SVG_CANVAS_BRIEF_VERSION",
    "adapt_brief_line",
    "reference_caveat",
    "svg_brief_replacements",
]
