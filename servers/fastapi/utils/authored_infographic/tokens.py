"""Design tokens for the archetype component library, derived from one deck's brand.

The renderer admits no CSS custom properties, so every token reaches the slide
as a literal. The library CSS is therefore a template with ``$name`` holes, and
this module is what fills them: the brand primary, the tints and the deep
shade derived from it, the neutral ramp, and the deck's font stack.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from string import Template
from typing import Mapping

_DEFAULT_PRIMARY = "#2563EB"
_DEFAULT_FONTS = "Noto Sans KR"


def _parse_hex(value: str) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError("colour must be a 3- or 6-digit hex value")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def _mix(color: str, other: str, share: float) -> str:
    """``share`` of ``other`` blended into ``color``."""
    a = _parse_hex(color)
    b = _parse_hex(other)
    return _to_hex(tuple(a[i] + (b[i] - a[i]) * share for i in range(3)))


def _shade(color: str, lightness_delta: float) -> str:
    r, g, b = (c / 255 for c in _parse_hex(color))
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    lightness = max(0.0, min(1.0, lightness + lightness_delta))
    return _to_hex(tuple(c * 255 for c in colorsys.hls_to_rgb(h, lightness, s)))


def _rgba(color: str, alpha: float) -> str:
    r, g, b = _parse_hex(color)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def normalize_primary(value: str | None) -> str:
    try:
        return _to_hex(tuple(float(c) for c in _parse_hex(value or "")))
    except ValueError:
        return _DEFAULT_PRIMARY


@dataclass(frozen=True)
class InfographicTokens:
    """Literal values the library CSS is rendered with."""

    primary: str
    primary_deep: str
    primary_tint: str
    primary_soft: str
    primary_glow: str
    on_primary: str
    on_primary_muted: str
    ink: str
    muted: str
    line: str
    surface: str
    white: str
    font: str

    def as_mapping(self) -> Mapping[str, str]:
        return {
            "primary": self.primary,
            "primary_deep": self.primary_deep,
            "primary_tint": self.primary_tint,
            "primary_soft": self.primary_soft,
            "primary_glow": self.primary_glow,
            "on_primary": self.on_primary,
            "on_primary_muted": self.on_primary_muted,
            "ink": self.ink,
            "muted": self.muted,
            "line": self.line,
            "surface": self.surface,
            "white": self.white,
            "font": self.font,
        }


def _font_stack(fonts: str | None) -> str:
    # Imported lazily: the prompt module imports the marker, which imports the
    # contracts of this package, so a top-level import would be circular.
    from utils.llm_calls.author_slide_prompt import css_font_stack

    return css_font_stack(fonts or _DEFAULT_FONTS)


def tokens_for_brand(brand: object) -> InfographicTokens:
    """Derive the library's literal tokens from a deck ``Brand``-like object.

    Only the primary hue and the font stack come from the brand; the design
    system already states that the primary is the deck's one accent, so every
    other colour is a tint or a neutral.
    """
    primary = normalize_primary(getattr(brand, "primary", None))
    return InfographicTokens(
        primary=primary,
        primary_deep=_shade(primary, -0.14),
        primary_tint=_mix(primary, "#FFFFFF", 0.88),
        primary_soft=_mix(primary, "#FFFFFF", 0.68),
        primary_glow=_rgba(primary, 0.28),
        on_primary="#FFFFFF",
        on_primary_muted=_mix(primary, "#FFFFFF", 0.82),
        ink="#0F172A",
        muted="#64748B",
        line="#D9E0EC",
        surface="#F5F8FD",
        white="#FFFFFF",
        font=_font_stack(getattr(brand, "fonts", None)),
    )


def render_css(template: str, tokens: InfographicTokens) -> str:
    """Fill a ``$name`` CSS template; a missing token is a programming error."""
    return Template(template).substitute(tokens.as_mapping())


__all__ = [
    "InfographicTokens",
    "normalize_primary",
    "render_css",
    "tokens_for_brand",
]
