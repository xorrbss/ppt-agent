"""G10 — the deck's design tokens as LITERAL values in the slide prompt.

An authored slide is written alone and in parallel, so nothing in its prompt
can see the deck around it. Until now the only colour it was told was the brand
primary; the style's own ``preview.palette`` (six hexes), its ``design_tokens``
background, and every spacing decision reached the model as prose inside a
style brief, if at all. Two slides of the same deck therefore picked different
greens, different radii and different gutters, which is what the 2026-09-05
palette-conformance instrument was added to measure.

This module states those values ONCE, as literals the model copies. It
deliberately does not emit CSS custom properties: ``renderer_css_policy``
reserves ``--*`` as a design change of its own, and a token block is worth
nothing if the admission layer has to change to accept it.

The type tokens are read from ``author_slide_brief.type_ramp`` rather than
restated, so the TYPE SCALE paragraph and the token block can never disagree.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from utils.llm_calls.author_slide_brief import type_ramp


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: Neutral fallbacks for a deck whose style declares nothing. They are the same
#: values ``DEFAULT_STYLE_BRIEF`` names, so the default deck's tokens and its
#: brief describe one palette.
_FALLBACK_INK = "#0F172A"
_FALLBACK_ACCENT = "#2563EB"
_FALLBACK_SURFACE = "#F8FAFC"
_LIGHT_HAIRLINE = "#E2E8F0"
_DARK_HAIRLINE = "#2A2F36"

#: Geometry tokens. One radius family and one spacing family per deck is the
#: whole point of the block, so these are constants rather than per-style
#: guesses no style file currently carries.
_RADIUS_PX = 12
_GUTTER_PX = 24
_PAD_PX = 28


def _normalized_hex(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate.upper() if _HEX.fullmatch(candidate) else None


def _relative_luminance(hex_color: str) -> float:
    """sRGB relative luminance, used only to order a palette light-to-dark."""
    red, green, blue = (
        int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)
    )
    channels = tuple(
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in (red, green, blue)
    )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _palette(style: Any | None) -> list[str]:
    raw = getattr(style, "preview_palette", None) if style is not None else None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    seen: list[str] = []
    for item in raw:
        normalized = _normalized_hex(item)
        if normalized is not None and normalized not in seen:
            seen.append(normalized)
    return seen


def _first_explicit(*candidates: Any) -> str | None:
    for candidate in candidates:
        normalized = _normalized_hex(candidate)
        if normalized is not None:
            return normalized
    return None


def _second_accent(palette: Sequence[str], accent: str, ink: str, surface: str) -> str:
    """The palette's next signal colour after the accent.

    Palette order is the style author's own ordering, so the first entry that
    is not already spoken for is the one they would have picked. A style with
    no second colour reuses the accent rather than inventing a hue, which is
    exactly what the block tells the model not to do.
    """
    for candidate in palette:
        if candidate not in {accent, ink, surface}:
            return candidate
    return accent


def deck_tokens(style: Any | None, brand: Any) -> dict[str, str]:
    """The deck's literal token values, resolved from style, palette and brand."""
    palette = _palette(style)
    accent = (
        _first_explicit(
            getattr(style, "primary_color", None) if style is not None else None,
            getattr(style, "preview_accent", None) if style is not None else None,
            getattr(brand, "primary", None),
        )
        or _FALLBACK_ACCENT
    )
    surface = (
        _first_explicit(
            getattr(style, "background_color", None) if style is not None else None,
            getattr(style, "preview_bg", None) if style is not None else None,
        )
        or (max(palette, key=_relative_luminance) if palette else None)
        or _FALLBACK_SURFACE
    )
    ink = (
        min(palette, key=_relative_luminance)
        if palette
        else None
    ) or _FALLBACK_INK
    if ink == accent or ink == surface:
        ink = _FALLBACK_INK
    ramp = type_ramp(getattr(brand, "verbosity", None))
    return {
        "ink": ink,
        "accent": accent,
        "accent-2": _second_accent(palette, accent, ink, surface),
        "surface": surface,
        "hairline": (
            _DARK_HAIRLINE if _relative_luminance(surface) < 0.4 else _LIGHT_HAIRLINE
        ),
        "radius": f"{_RADIUS_PX}px",
        "gutter": f"{_GUTTER_PX}px",
        "pad": f"{_PAD_PX}px",
        "t-display": f"{ramp.display}px",
        "t-h2": f"{ramp.lead}px",
        "t-body": f"{ramp.body}px",
        "t-label": f"{ramp.label}px",
    }


def deck_tokens_block(style: Any | None, brand: Any) -> str:
    """The DECK TOKENS paragraph appended to every slide's design system."""
    values = "; ".join(f"{name} {value}" for name, value in deck_tokens(style, brand).items())
    return (
        "\nDECK TOKENS (this deck's own values): "
        f"{values}.\n"
        "Use these exact values for colour, radius and spacing; any other hue is an error. "
        "Tints and shades of the ink and of the two accents are allowed, a new hue is not, "
        "and radius/gutter/pad are one family for the whole deck. Write the values "
        "literally in the CSS: do not declare CSS custom properties."
    )


__all__ = ["deck_tokens", "deck_tokens_block"]
