"""Round stroke caps and joins: admitted by the contract, kept by icons, exported as DrawingML."""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from services.generation_core.drivers.svg.embed_assets import icon_group
from services.generation_core.drivers.svg.icon_flatten import FlattenedIcon, flatten_icon_svg
from services.svg_contract import SVG_NS, validate_svg

ROUND_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000000" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M4 12h16"/><polyline points="6 6 12 2 18 6"/></svg>'
)


def _page(body: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="80" y="80" font-family="Noto Sans KR" font-size="32" fill="#112233">Title</text>'
            f'{body}</svg>')


@pytest.mark.parametrize("attributes", [
    'stroke-linecap="round"', 'stroke-linejoin="round"', 'stroke-linecap="butt" stroke-linejoin="miter"',
])
def test_round_and_default_caps_and_joins_are_admitted(attributes):
    body = (f'<g stroke="#112233" stroke-width="4" fill="none" {attributes}>'
            '<polyline points="100,100 200,160 300,100"/><line x1="100" y1="300" x2="400" y2="300"/></g>')
    validate_svg(_page(body))


@pytest.mark.parametrize(("key", "value"), [
    ("stroke-linecap", "square"), ("stroke-linejoin", "bevel"), ("stroke-linejoin", "arcs"),
    ("stroke-linecap", "Round"), ("stroke-linecap", ""),
])
def test_unmeasured_caps_and_joins_stay_rejected(key, value):
    body = f'<line x1="100" y1="300" x2="400" y2="300" stroke="#112233" stroke-width="4" {key}="{value}"/>'
    with pytest.raises(ValueError, match=f"svg_contract_unsupported_stroke_line:{key}"):
        validate_svg(_page(body))


def test_a_flattened_icon_keeps_its_round_caps_and_joins():
    icon = flatten_icon_svg(ROUND_ICON, color="#112233")
    assert (icon.line_cap, icon.line_join) == ("round", "round")
    group = icon_group(icon, x=100, y=100, width=64, height=64)
    assert group.get("stroke-linecap") == "round" and group.get("stroke-linejoin") == "round"
    validate_svg(_page(ET.tostring(group, encoding="unicode")))


def test_an_icon_without_round_paint_keeps_the_svg_defaults():
    icon = flatten_icon_svg(ROUND_ICON.replace(' stroke-linecap="round" stroke-linejoin="round"', ""),
                            color="#112233")
    assert (icon.line_cap, icon.line_join) == ("butt", "miter")
    group = icon_group(icon, x=0, y=0, width=64, height=64)
    assert "stroke-linecap" not in group.attrib and "stroke-linejoin" not in group.attrib


def test_icon_json_round_trips_and_reads_payloads_written_before_round_caps():
    icon = flatten_icon_svg(ROUND_ICON, color="#112233")
    assert FlattenedIcon.from_json(icon.to_json()) == icon
    legacy = icon.to_json()
    legacy.pop("line_cap")
    legacy.pop("line_join")
    restored = FlattenedIcon.from_json(legacy)
    assert (restored.line_cap, restored.line_join) == ("butt", "miter")
    plain = flatten_icon_svg(ROUND_ICON.replace(' stroke-linecap="round" stroke-linejoin="round"', ""),
                             color="#112233")
    assert "line_cap" not in plain.to_json() and "line_join" not in plain.to_json()


def test_round_caps_and_joins_reach_the_pptx_as_drawingml(tmp_path):
    from workers.public_svg_export_worker import VENDOR_ROOT

    group = icon_group(flatten_icon_svg(ROUND_ICON, color="#112233"), x=200, y=200, width=128, height=128)
    ET.register_namespace("", SVG_NS)
    source = tmp_path / "slide-1.svg"
    source.write_text(_page(ET.tostring(group, encoding="unicode")), encoding="utf-8")
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        from svg_to_pptx.conversion_context import ConversionContext

        target = tmp_path / "deck.pptx"
        assert create_pptx_with_native_svg(
            [source], target, verbose=False, transition=None, use_native_shapes=True, workers=1,
            image_optimize=False, pptx_structure="flat", use_compat_mode=False,
            conversion_context=ConversionContext(1280, 720, 12192000, 6858000, legacy=False))
    finally:
        sys.path.remove(str(VENDOR_ROOT))
    with zipfile.ZipFile(target) as package:
        slide = package.read("ppt/slides/slide1.xml").decode("utf-8")
    lines = [line for line in re.findall(r"<a:ln\b[^>]*>.*?</a:ln>", slide, re.S) if "<a:solidFill>" in line]
    assert len(lines) == 2, "both icon strokes became DrawingML lines"
    assert all('cap="rnd"' in line and "<a:round/>" in line for line in lines)
    assert not any(name.startswith("ppt/media/") for name in zipfile.ZipFile(target).namelist())
