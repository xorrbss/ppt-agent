"""Regression gate for ``services.source_content.pipeline._style_tokens``.

Background (the bug this test exists to catch forever): ``_style_tokens`` used
to set ``text_primary`` to ``preview_palette[1]`` -- an arbitrary index into a
style's *decorative* preview swatch list that has no readability contract --
and hardcoded ``surface="#FFFFFF"`` / ``text_secondary="#334155"`` regardless
of the style's actual background. For ``authored_styles/default.yaml`` the
palette is ``[#F8FAFC, #FFFFFF, #0F172A, #64748B, #2563EB]``, so
``text_primary`` resolved to ``#FFFFFF`` -- white text on the ``#F8FAFC``
off-white background -- and real 35-slide decks rendered with invisible
titles and body text. Measuring all 39 styles under ``authored_styles/``
against the derived tokens found 17 with contrast below WCAG's 4.5:1 body
threshold.

The Composer offline evaluation harness (``scripts/composer_offline_eval*``)
never caught this because it defaulted to its own hardcoded ``NEUTRAL_TOKENS``
placeholder palette: reaching ``_style_tokens`` at all required passing
``--style``, which routine runs did not. That harness now defaults to the
style the product generates with, but a CLI default is not a gate -- this
test is, and it covers every style rather than one.

This test is the independent oracle for the fix: it reimplements WCAG
contrast math from scratch (it must NOT import any production color-contrast
helper -- otherwise a bug shared between the test and the implementation
would make the gate agree with the defect it exists to catch), loads every
real style file through the real loader, and asserts every derived token pair
that must stay readable actually is.
"""

from __future__ import annotations

import pytest

from services.source_content.pipeline import _style_tokens
from utils.authored_styles import AUTHORED_STYLES_DIRECTORY, load_authored_styles


# --- Independent WCAG contrast oracle -------------------------------------------
#
# Implemented here from the WCAG 2.x spec (sRGB -> linear -> relative
# luminance -> (L_light + 0.05) / (L_dark + 0.05)), deliberately NOT reusing
# ``utils/color_contrast.py`` or any other production helper.


def _srgb_channel_to_linear(channel_8bit: int) -> float:
    channel = channel_8bit / 255.0
    if channel <= 0.03928:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    assert len(value) == 6, f"expected a #RRGGBB color, got {hex_color!r}"
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    r_lin, g_lin, b_lin = (_srgb_channel_to_linear(c) for c in (r, g, b))
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(color_a: str, color_b: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colors, always >= 1.0."""

    luminance_a = _relative_luminance(color_a)
    luminance_b = _relative_luminance(color_b)
    lighter, darker = max(luminance_a, luminance_b), min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


# Sanity checks on the oracle itself, independent of any style fixture.
def test_contrast_ratio_black_on_white_is_maximal() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, rel=1e-3)


def test_contrast_ratio_identical_colors_is_one() -> None:
    assert contrast_ratio("#123456", "#123456") == pytest.approx(1.0, rel=1e-9)


def test_contrast_ratio_is_symmetric() -> None:
    assert contrast_ratio("#F8FAFC", "#FFFFFF") == pytest.approx(
        contrast_ratio("#FFFFFF", "#F8FAFC"), rel=1e-9
    )


WCAG_AA_BODY_TEXT_MINIMUM = 4.5


def _all_style_ids() -> list[str]:
    styles = load_authored_styles(AUTHORED_STYLES_DIRECTORY)
    assert styles, "expected authored_styles/*.yaml to yield at least one style"
    return [style.id for style in styles]


_STYLE_IDS = _all_style_ids()


def _style_by_id(style_id: str):
    styles = load_authored_styles(AUTHORED_STYLES_DIRECTORY)
    by_id = {style.id: style for style in styles}
    return by_id[style_id]


@pytest.mark.parametrize("style_id", _STYLE_IDS, ids=_STYLE_IDS)
def test_style_tokens_meet_wcag_contrast(style_id: str) -> None:
    """Every real authored style must derive readable text tokens.

    ``text_primary`` and ``text_secondary`` must each meet the WCAG AA body
    text minimum (4.5:1) against both ``background`` and ``surface`` -- the
    two surfaces a slide's text is actually painted on. ``surface`` must also
    stay visually distinct from ``background``, or panels disappear.
    """

    style = _style_by_id(style_id)
    tokens = _style_tokens(style)

    checks = (
        ("text_primary", tokens.text_primary, "background", tokens.background),
        ("text_primary", tokens.text_primary, "surface", tokens.surface),
        ("text_secondary", tokens.text_secondary, "background", tokens.background),
        ("text_secondary", tokens.text_secondary, "surface", tokens.surface),
    )
    for text_role, text_color, surface_role, surface_color in checks:
        ratio = contrast_ratio(text_color, surface_color)
        assert ratio >= WCAG_AA_BODY_TEXT_MINIMUM, (
            f"style '{style_id}': {text_role}={text_color} against "
            f"{surface_role}={surface_color} measures {ratio:.2f}:1, below the "
            f"WCAG AA body-text minimum of {WCAG_AA_BODY_TEXT_MINIMUM}:1 "
            f"(background={tokens.background}, surface={tokens.surface}, "
            f"text_primary={tokens.text_primary}, "
            f"text_secondary={tokens.text_secondary})"
        )

    assert tokens.surface != tokens.background, (
        f"style '{style_id}': surface={tokens.surface} is identical to "
        f"background={tokens.background}; panels would be invisible "
        f"(background={tokens.background}, surface={tokens.surface})"
    )


def test_default_style_text_primary_is_not_white_on_off_white_background() -> None:
    """Pin the specific regression: default style's palette-index bug.

    ``authored_styles/default.yaml`` has ``preview.palette`` =
    ``[#F8FAFC, #FFFFFF, #0F172A, #64748B, #2563EB]``. The buggy
    ``_style_tokens`` picked ``palette[1]`` = ``#FFFFFF`` as ``text_primary``,
    which is the same color as the deck's own ``#FFFFFF`` preview swatch and
    near-identical to the ``#F8FAFC`` background -- invisible body text on
    real decks. This test pins both the specific bad value and the general
    readability property so the regression cannot silently return.
    """

    style = _style_by_id("default")
    tokens = _style_tokens(style)

    assert tokens.text_primary.upper() != "#FFFFFF", (
        f"style 'default': text_primary resolved to white ({tokens.text_primary}), "
        "the palette-index defect this test exists to catch "
        "(background=#F8FAFC, preview.palette[1]=#FFFFFF)"
    )
    ratio = contrast_ratio(tokens.text_primary, "#F8FAFC")
    assert ratio >= WCAG_AA_BODY_TEXT_MINIMUM, (
        f"style 'default': text_primary={tokens.text_primary} against the "
        f"documented background #F8FAFC measures {ratio:.2f}:1, below the "
        f"WCAG AA body-text minimum of {WCAG_AA_BODY_TEXT_MINIMUM}:1"
    )
