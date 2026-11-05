"""The page number keeps the style primary only while it reads on the page.

The readable-ink rule applies only to a deck whose frozen style snapshot pins it
(``PAGE_NUMBER_INK_KEY``, set by a fresh svg admission). A stored snapshot without
the pin keeps the unconditional style primary, so recovery replays stay byte-identical.
"""

import xml.etree.ElementTree as ET

import pytest

from services.svg_contract import (
    PAGE_NUMBER_ID, PAGE_NUMBER_INK_KEY, PAGE_NUMBER_INK_READABLE, SVG_NS, finalize_svg,
)
from utils.color_contrast import contrast_ratio

W, H = 1280, 720
HEAD = f'<svg xmlns="{SVG_NS}" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
TITLE = '<text x="60" y="100" font-family="Noto Sans KR" font-size="32" fill="#111111">Quarterly plan</text>'


def _pinned(tokens):
    return {"tokens": tokens, PAGE_NUMBER_INK_KEY: PAGE_NUMBER_INK_READABLE}


def _number_fill(snapshot, body=TITLE):
    svg = finalize_svg(HEAD + body + "</svg>", index=2, n=5, style_snapshot=snapshot)
    node = next(n for n in ET.fromstring(svg).iter() if n.get("id") == PAGE_NUMBER_ID)
    return node.get("fill")


def test_low_contrast_primary_falls_back_to_stronger_ink():
    assert _number_fill(_pinned({"background": "#0B1B3A", "primary": "#102A5C"})) == "#FFFFFF"
    assert _number_fill(_pinned({"background": "#FAF7F2", "primary": "#F0E6D8"})) == "#0F172A"


def test_unpinned_snapshot_keeps_the_primary_byte_for_byte():
    # A stored deck's snapshot carries no pin: its replay must reproduce the stored bytes,
    # which used the style primary whatever its contrast.
    tokens = {"background": "#0B1B3A", "primary": "#102A5C", "body_font": "Noto Sans KR"}
    stored = finalize_svg(HEAD + TITLE + "</svg>", index=2, n=5, style_snapshot={"tokens": tokens})
    assert 'fill="#102A5C"' in stored
    assert _number_fill({"tokens": tokens}) == "#102A5C"
    pinned = finalize_svg(HEAD + TITLE + "</svg>", index=2, n=5, style_snapshot=_pinned(tokens))
    assert pinned == stored.replace('fill="#102A5C"', 'fill="#FFFFFF"')


def test_readable_primary_is_kept_unchanged():
    tokens = {"background": "#FFFFFF", "primary": "#1d4ed8"}
    assert contrast_ratio("#1d4ed8", "#FFFFFF") >= 4.5
    assert _number_fill(_pinned(tokens)) == "#1d4ed8"


def test_output_is_byte_identical_when_primary_passes():
    tokens = {"background": "#FFFFFF", "primary": "#1D4ED8", "body_font": "Noto Sans KR"}
    pinned = finalize_svg(HEAD + TITLE + "</svg>", index=0, n=1, style_snapshot=_pinned(tokens))
    unpinned = finalize_svg(HEAD + TITLE + "</svg>", index=0, n=1, style_snapshot={"tokens": tokens})
    assert pinned == unpinned
    assert 'fill="#1D4ED8"' in pinned and pinned.count(PAGE_NUMBER_ID) == 1


def test_missing_tokens_keep_the_default_blue():
    assert _number_fill(None) == "#2563EB"
    assert _number_fill(_pinned({"background": "#FFFFFF"})) == "#2563EB"


def test_full_canvas_background_rect_overrides_style_token():
    body = f'<rect x="0" y="0" width="{W}" height="{H}" fill="#0B1B3A"/>' + TITLE
    assert _number_fill(_pinned({"background": "#FFFFFF", "primary": "#102A5C"}), body) == "#FFFFFF"
    # A small decorative rect is not the page background.
    small = '<rect x="0" y="0" width="100" height="100" fill="#0B1B3A"/>' + TITLE
    assert _number_fill(_pinned({"background": "#FFFFFF", "primary": "#1D4ED8"}), small) == "#1D4ED8"


def test_unknown_pin_is_refused():
    with pytest.raises(ValueError, match="svg_page_number_ink_invalid"):
        _number_fill({"tokens": {"primary": "#1D4ED8"}, PAGE_NUMBER_INK_KEY: "svg-page-number-ink/v0"})
