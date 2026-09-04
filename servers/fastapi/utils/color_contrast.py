"""Deterministic sRGB colour maths for readable deck tokens.

This module owns the WCAG 2.x relative-luminance and contrast-ratio
definitions plus the derivation of a readable ``surface`` / ``text_primary``
/ ``text_secondary`` trio from a single page background.  It is pure
arithmetic: no I/O, no provider calls, no randomness, so the same background
always yields the same tokens.

Why the derivation ignores a style's palette
--------------------------------------------
An authored style's ``preview.palette`` is a decorative swatch list.  Its
entries carry no readability contract and no defined order, so neither a
fixed index nor "the highest-contrast entry" is safe: the latter reliably
selects the saturated brand colour, which is wrong for body copy.  Body text
is therefore computed from the background's own luminance as a near-neutral
pole (near-black on a light page, near-white on a dark page) carrying only a
whisper of the page hue, which keeps chroma low while still reading as part
of the style.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: WCAG 2.x contrast floors for body-sized text.
AA_BODY_TEXT = 4.5
AAA_BODY_TEXT = 7.0

WHITE = "#FFFFFF"
BLACK = "#000000"

#: The smallest per-channel step that still reads as a distinct panel on a
#: projector or in print.  Below this a panel visually merges into the page.
_MIN_PANEL_CHANNEL_DELTA = 6
#: Panel tints tried in order, per side of the light/dark divide.  Dark pages
#: need a larger step because a near-black page swallows small lifts.
_LIGHT_PANEL_AMOUNTS = (0.035, 0.06, 0.10, 0.16, 0.26)
_DARK_PANEL_AMOUNTS = (0.08, 0.12, 0.18, 0.26)
#: How much of the page colour is mixed into the text pole, most tinted first.
_TEXT_TINT_AMOUNTS = (0.08, 0.04, 0.0)
#: Muting steps for the secondary text colour, most muted first.
_SECONDARY_MUTE_AMOUNTS = tuple(index / 100.0 for index in range(40, 101, 4))
#: A small margin over the AA floor so rounding to 8-bit channels cannot land
#: the secondary colour a hundredth below the threshold.
_SECONDARY_TARGET = 4.6
#: How far the accent may be pushed toward the readable pole, least first, so
#: the accent keeps as much of its brand identity as readability allows.
_ACCENT_SHIFT_AMOUNTS = tuple(index / 100.0 for index in range(0, 101, 4))


def normalize_hex(value: object, fallback: str = WHITE) -> str:
    """Return ``value`` as an upper-case ``#RRGGBB`` string, else ``fallback``."""

    text = str(value or "").strip()
    if HEX_COLOR_PATTERN.fullmatch(text):
        return text.upper()
    return fallback.upper()


def _channels(color: str) -> tuple[int, int, int]:
    text = str(color).strip()
    if not HEX_COLOR_PATTERN.fullmatch(text):
        raise ValueError(f"expected a #RRGGBB color, got {color!r}")
    return (
        int(text[1:3], 16),
        int(text[3:5], 16),
        int(text[5:7], 16),
    )


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a ``#RRGGBB`` colour."""

    linear = []
    for channel in _channels(color):
        value = channel / 255.0
        linear.append(
            value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2.x contrast ratio between two ``#RRGGBB`` colours (>= 1.0)."""

    left = relative_luminance(first)
    right = relative_luminance(second)
    lighter, darker = (left, right) if left >= right else (right, left)
    return (lighter + 0.05) / (darker + 0.05)


def blend(foreground: str, background: str, amount: float) -> str:
    """Mix ``amount`` of ``foreground`` over ``background``; deterministic hex."""

    ratio = min(1.0, max(0.0, amount))
    return "#" + "".join(
        f"{round(back + (front - back) * ratio):02X}"
        for front, back in zip(_channels(foreground), _channels(background), strict=True)
    )


def is_dark_color(color: str) -> bool:
    """Whether light text reads better than dark text on ``color``.

    Deciding by which pole wins the contrast comparison — rather than by a
    luminance cut-off — also guarantees the winning pole clears 4.5:1: the
    two ratios cross at a relative luminance of about 0.179, where both sit
    at roughly 4.6:1.
    """

    return contrast_ratio(color, WHITE) > contrast_ratio(color, BLACK)


def _channel_delta(first: str, second: str) -> int:
    return max(
        abs(left - right) for left, right in zip(_channels(first), _channels(second), strict=True)
    )


def panel_surface(background: str) -> str:
    """A panel tint one small step off ``background``.

    The step goes toward the pole opposite the page — a light page gets a
    slightly deeper panel, a dark page a slightly lifted one — because that
    direction is always available (a white page cannot become whiter) while
    the step stays small enough to keep the panel on the page's own side of
    the light/dark divide.  If that direction would cost the body text its AA
    floor (only possible for a background near the 0.179 crossover), the
    direction flips instead of the panel collapsing into the page.
    """

    page = normalize_hex(background)
    dark_page = is_dark_color(page)
    ink = WHITE if dark_page else BLACK
    amounts = _DARK_PANEL_AMOUNTS if dark_page else _LIGHT_PANEL_AMOUNTS
    # ``ink`` is also the "away from the page" pole, so it leads.
    best = page
    best_ratio = -1.0
    for pole in (ink, BLACK if dark_page else WHITE):
        for amount in amounts:
            candidate = blend(pole, page, amount)
            if _channel_delta(candidate, page) < _MIN_PANEL_CHANNEL_DELTA:
                continue
            ratio = contrast_ratio(ink, candidate)
            if ratio >= AA_BODY_TEXT:
                return candidate
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
    # Unreachable in practice: a page can sit at one pole but never at both,
    # so the widest step in one of the two directions always clears the delta.
    return best


def readable_text_color(background: str, surface: str) -> str:
    """The body-text colour for a page painted on ``background``/``surface``.

    Returns the most page-tinted near-neutral that still reaches AAA (7:1)
    against both planes, falling back to the least tinted candidate — the
    pure pole — otherwise.  ``panel_surface`` guarantees the pure pole clears
    the AA floor against both planes, so the result is never unreadable.
    """

    page = normalize_hex(background)
    panel = normalize_hex(surface)
    pole = WHITE if is_dark_color(page) else BLACK
    best = pole
    best_score = -1.0
    for amount in _TEXT_TINT_AMOUNTS:
        candidate = blend(page, pole, amount)
        score = min(
            contrast_ratio(candidate, page), contrast_ratio(candidate, panel)
        )
        if score >= AAA_BODY_TEXT:
            return candidate
        if score > best_score:
            best, best_score = candidate, score
    return best


def muted_text_color(text_primary: str, background: str, surface: str) -> str:
    """The most muted secondary text colour that still clears the AA floor.

    Mutes ``text_primary`` toward the page and stops at the first step that
    holds 4.5:1 against both planes, so secondary copy reads as subdued
    without becoming a second accessibility defect.
    """

    page = normalize_hex(background)
    panel = normalize_hex(surface)
    for amount in _SECONDARY_MUTE_AMOUNTS:
        candidate = blend(text_primary, page, amount)
        score = min(
            contrast_ratio(candidate, page), contrast_ratio(candidate, panel)
        )
        if score >= _SECONDARY_TARGET:
            return candidate
    return normalize_hex(text_primary, text_primary)


def readable_accent_color(accent: str, background: str, surface: str) -> str:
    """The accent, darkened or lightened just enough to be legible as text.

    An accent is authored to be seen as a fill or a rule, where WCAG's text
    floor does not apply, so a style is free to ship one that is far too
    light or too dark to read: measured across the shipped catalogue, 21 of
    38 accents land below 4.5:1 against their own page.  Callers that paint
    small text in the brand colour need this value instead; callers that
    fill a shape or draw a rule must keep using the accent itself, or every
    design whose accent is already fine would be flattened.

    The accent is pushed toward the same pole ``readable_text_color`` picks
    and stops at the first step clearing AA against both planes, so a
    conforming accent is returned untouched and a failing one moves as
    little as the floor allows.  Pushing toward black scales the channels
    proportionally and so holds the hue exactly; pushing toward white
    desaturates, which is unavoidable when a dark page needs a lighter mark.
    """

    brand = normalize_hex(accent)
    page = normalize_hex(background)
    panel = normalize_hex(surface, page)
    pole = WHITE if is_dark_color(page) else BLACK
    for amount in _ACCENT_SHIFT_AMOUNTS:
        candidate = blend(pole, brand, amount)
        if (
            contrast_ratio(candidate, page) >= AA_BODY_TEXT
            and contrast_ratio(candidate, panel) >= AA_BODY_TEXT
        ):
            return candidate
    # Unreachable while ``pole`` is the winning pole, which clears AA on both
    # planes by construction; fall back to body ink rather than return unread
    # colour if that ever stops holding.
    return readable_text_color(page, panel)


@dataclass(frozen=True)
class ReadableColorScheme:
    """A page background with the readable planes derived from it."""

    background: str
    surface: str
    text_primary: str
    text_secondary: str


def readable_color_scheme(
    background: str, *, fallback: str = WHITE
) -> ReadableColorScheme:
    """Derive the readable ``surface``/``text`` planes for a page background."""

    page = normalize_hex(background, fallback)
    surface = panel_surface(page)
    text_primary = readable_text_color(page, surface)
    return ReadableColorScheme(
        background=page,
        surface=surface,
        text_primary=text_primary,
        text_secondary=muted_text_color(text_primary, page, surface),
    )
