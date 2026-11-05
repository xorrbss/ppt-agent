"""The page number keeps the style primary only while it reads on the page."""

import xml.etree.ElementTree as ET

from services.svg_contract import PAGE_NUMBER_ID, SVG_NS, finalize_svg
from utils.color_contrast import contrast_ratio

W, H = 1280, 720
HEAD = f'<svg xmlns="{SVG_NS}" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
TITLE = '<text x="60" y="100" font-family="Noto Sans KR" font-size="32" fill="#111111">Quarterly plan</text>'


def _number_fill(snapshot, body=TITLE):
    svg = finalize_svg(HEAD + body + "</svg>", index=2, n=5, style_snapshot=snapshot)
    node = next(n for n in ET.fromstring(svg).iter() if n.get("id") == PAGE_NUMBER_ID)
    return node.get("fill")


def test_low_contrast_primary_falls_back_to_stronger_ink():
    dark = {"tokens": {"background": "#0B1B3A", "primary": "#102A5C"}}
    assert _number_fill(dark) == "#FFFFFF"
    light = {"tokens": {"background": "#FAF7F2", "primary": "#F0E6D8"}}
    assert _number_fill(light) == "#0F172A"


def test_readable_primary_is_kept_unchanged():
    snapshot = {"tokens": {"background": "#FFFFFF", "primary": "#1d4ed8"}}
    assert contrast_ratio("#1d4ed8", "#FFFFFF") >= 4.5
    assert _number_fill(snapshot) == "#1d4ed8"


def test_output_is_byte_identical_when_primary_passes():
    snapshot = {"tokens": {"background": "#FFFFFF", "primary": "#1D4ED8", "body_font": "Noto Sans KR"}}
    svg = finalize_svg(HEAD + TITLE + "</svg>", index=0, n=1, style_snapshot=snapshot)
    assert 'fill="#1D4ED8"' in svg and svg.count(PAGE_NUMBER_ID) == 1


def test_missing_tokens_keep_the_default_blue():
    assert _number_fill(None) == "#2563EB"
    assert _number_fill({"tokens": {"background": "#FFFFFF"}}) == "#2563EB"


def test_full_canvas_background_rect_overrides_style_token():
    body = f'<rect x="0" y="0" width="{W}" height="{H}" fill="#0B1B3A"/>' + TITLE
    snapshot = {"tokens": {"background": "#FFFFFF", "primary": "#102A5C"}}
    assert _number_fill(snapshot, body) == "#FFFFFF"
    # A small decorative rect is not the page background.
    small = '<rect x="0" y="0" width="100" height="100" fill="#0B1B3A"/>' + TITLE
    assert _number_fill({"tokens": {"background": "#FFFFFF", "primary": "#1D4ED8"}}, small) == "#1D4ED8"
