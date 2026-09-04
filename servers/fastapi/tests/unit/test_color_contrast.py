"""Unit gate for ``utils.color_contrast``.

The colour maths is asserted against values taken from the WCAG 2.x
definition itself (black on white is exactly 21:1, a colour against itself is
1:1), and the derivation is asserted as properties that must hold for *any*
background, not just the backgrounds the shipped styles happen to use.
"""

from __future__ import annotations

import pytest

from utils.color_contrast import (
    AA_BODY_TEXT,
    AAA_BODY_TEXT,
    BLACK,
    WHITE,
    blend,
    contrast_ratio,
    is_dark_color,
    normalize_hex,
    panel_surface,
    readable_color_scheme,
    relative_luminance,
)


# A spread of page backgrounds: the two poles, near-poles, warm and cool
# lights, deep navies, and mid-luminance greys around the pole crossover.
BACKGROUNDS = [
    "#FFFFFF",
    "#000000",
    "#F8FAFC",
    "#FFF8ED",
    "#E8DCC4",
    "#0B0618",
    "#020617",
    "#1C2644",
    "#2D2926",
    "#767676",
    "#808080",
    "#8A8A8A",
    "#5C6BC0",
    "#C8A870",
]


def test_relative_luminance_of_the_poles() -> None:
    assert relative_luminance(WHITE) == pytest.approx(1.0)
    assert relative_luminance(BLACK) == pytest.approx(0.0)


def test_contrast_ratio_matches_the_wcag_definition() -> None:
    assert contrast_ratio(BLACK, WHITE) == pytest.approx(21.0)
    assert contrast_ratio("#123456", "#123456") == pytest.approx(1.0)
    assert contrast_ratio("#F8FAFC", "#0F172A") == pytest.approx(
        contrast_ratio("#0F172A", "#F8FAFC")
    )


def test_normalize_hex_upper_cases_valid_input_and_falls_back_otherwise() -> None:
    assert normalize_hex("#a1b2c3") == "#A1B2C3"
    assert normalize_hex("  #A1B2C3  ") == "#A1B2C3"
    assert normalize_hex("rgb(1,2,3)", "#0F172A") == "#0F172A"
    assert normalize_hex(None, "#0f172a") == "#0F172A"


def test_blend_interpolates_between_the_two_colors() -> None:
    assert blend(WHITE, BLACK, 0.0) == BLACK
    assert blend(WHITE, BLACK, 1.0) == WHITE
    assert blend(WHITE, BLACK, 0.5) == "#808080"
    # Out-of-range amounts clamp rather than extrapolate.
    assert blend(WHITE, BLACK, 2.0) == WHITE
    assert blend(WHITE, BLACK, -1.0) == BLACK


def test_blend_rejects_malformed_colors() -> None:
    with pytest.raises(ValueError):
        blend("#FFF", BLACK, 0.5)


def test_is_dark_color_agrees_with_which_pole_reads_better() -> None:
    assert is_dark_color("#000000") is True
    assert is_dark_color("#0B0618") is True
    assert is_dark_color("#FFFFFF") is False
    assert is_dark_color("#F8FAFC") is False


@pytest.mark.parametrize("background", BACKGROUNDS, ids=BACKGROUNDS)
def test_the_winning_pole_always_clears_the_aa_floor(background: str) -> None:
    """``is_dark_color`` decides by comparison, so its pole is always legible.

    The two ratios cross at a relative luminance near 0.179, where both sit at
    about 4.6:1 -- above the AA floor -- so the winner can never be below it.
    """

    pole = WHITE if is_dark_color(background) else BLACK
    assert contrast_ratio(pole, background) >= AA_BODY_TEXT


@pytest.mark.parametrize("background", BACKGROUNDS, ids=BACKGROUNDS)
def test_panel_surface_is_distinct_from_the_page(background: str) -> None:
    surface = panel_surface(background)
    assert surface != background
    channels = [
        abs(int(surface[i : i + 2], 16) - int(background[i : i + 2], 16))
        for i in (1, 3, 5)
    ]
    assert max(channels) >= 6


@pytest.mark.parametrize("background", BACKGROUNDS, ids=BACKGROUNDS)
def test_panel_surface_stays_on_the_page_side_of_the_divide(background: str) -> None:
    assert is_dark_color(panel_surface(background)) == is_dark_color(background)


@pytest.mark.parametrize("background", BACKGROUNDS, ids=BACKGROUNDS)
def test_scheme_text_is_readable_on_both_planes(background: str) -> None:
    scheme = readable_color_scheme(background)
    for text in (scheme.text_primary, scheme.text_secondary):
        for plane in (scheme.background, scheme.surface):
            assert contrast_ratio(text, plane) >= AA_BODY_TEXT


@pytest.mark.parametrize("background", BACKGROUNDS, ids=BACKGROUNDS)
def test_scheme_primary_text_reaches_aaa_where_achievable(background: str) -> None:
    """A pole-anchored ink reaches 7:1 on every background off the crossover.

    Mid-luminance greys cannot reach 7:1 against any colour, so they are only
    held to the AA floor, which the previous test already asserts.
    """

    scheme = readable_color_scheme(background)
    reachable = max(
        contrast_ratio(WHITE, background), contrast_ratio(BLACK, background)
    )
    if reachable < AAA_BODY_TEXT:
        return
    assert contrast_ratio(scheme.text_primary, scheme.background) >= AAA_BODY_TEXT
    assert contrast_ratio(scheme.text_primary, scheme.surface) >= AAA_BODY_TEXT


@pytest.mark.parametrize("background", BACKGROUNDS, ids=BACKGROUNDS)
def test_scheme_secondary_text_is_muted_but_not_illegible(background: str) -> None:
    scheme = readable_color_scheme(background)
    primary_ratio = contrast_ratio(scheme.text_primary, scheme.background)
    secondary_ratio = contrast_ratio(scheme.text_secondary, scheme.background)
    assert secondary_ratio >= AA_BODY_TEXT
    if primary_ratio > AA_BODY_TEXT * 1.5:
        assert secondary_ratio < primary_ratio


def test_scheme_is_deterministic_and_normalizes_its_input() -> None:
    first = readable_color_scheme("#f8fafc")
    second = readable_color_scheme("#F8FAFC")
    assert first == second
    assert first.background == "#F8FAFC"


def test_scheme_falls_back_when_the_background_is_unusable() -> None:
    assert readable_color_scheme("not-a-color", fallback="#0F172A").background == (
        "#0F172A"
    )


def test_light_and_dark_pages_pick_opposite_ink_poles() -> None:
    light = readable_color_scheme("#F8FAFC")
    dark = readable_color_scheme("#0B0618")
    assert relative_luminance(light.text_primary) < 0.1
    assert relative_luminance(dark.text_primary) > 0.7
    # Body ink stays near-neutral: no saturated brand colour may leak in.
    for scheme in (light, dark):
        channels = [int(scheme.text_primary[i : i + 2], 16) for i in (1, 3, 5)]
        assert max(channels) - min(channels) <= 12
