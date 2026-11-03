"""Compound paths keep their holes; multi-colour drawings keep their palette colours and opacity."""

from __future__ import annotations

import io
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

from services.generation_core.drivers.svg.embed_assets import icon_group, rasterize_icon
from services.generation_core.drivers.svg.icon_flatten import FlattenedIcon, flatten_icon_svg
from services.svg_contract import SVG_NS, validate_svg

# A solid square with a square hole, drawn as one path of two subpaths wound in
# opposite directions (how filled icon sets cut their holes).
HOLED = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="currentColor">'
         '<path d="M10 10H90V90H10Z M35 35V65H65V35Z"/></svg>')
DRAWING = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
           '<rect x="10" y="10" width="80" height="80" rx="8" fill="#0E7C7B"/>'
           '<circle cx="150" cy="50" r="30" fill="#F2545B" opacity="0.5"/>'
           '<path d="M10 95 C 60 60 140 60 190 95" fill="none" stroke="#1d2b37" stroke-width="4"/></svg>')
PALETTE = ["#0E7C7B", "#F2545B", "#1D2B36"]


def _page(*groups: ET.Element) -> str:
    ET.register_namespace("", SVG_NS)
    body = "".join(ET.tostring(group, encoding="unicode") for group in groups)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="80" y="80" font-family="Noto Sans KR" font-size="32" fill="#112233">T</text>'
            f"{body}</svg>")


def test_a_filled_path_with_a_hole_stays_one_element_with_both_rings():
    icon = flatten_icon_svg(HOLED, color="#112233")
    assert [primitive["tag"] for primitive in icon.primitives] == ["path"]
    assert len(icon.primitives[0]["subpaths"]) == 2 and icon.point_count == 8
    group = icon_group(icon, x=100, y=100, width=100, height=100)
    path = next(node for node in group.iter() if node.tag.endswith("path"))
    assert path.get("d").count("M") == 2 and path.get("d").count("Z") == 2
    validate_svg(_page(group))
    assert FlattenedIcon.from_json(icon.to_json()) == icon


def test_the_preview_leaves_the_hole_transparent():
    image = Image.open(io.BytesIO(rasterize_icon(flatten_icon_svg(HOLED, color="#112233"), size=100)))
    assert image.getpixel((50, 50))[3] == 0
    assert image.getpixel((20, 20))[3] == 255


def test_a_multicolour_drawing_keeps_palette_colours_opacity_and_stroke_colour():
    drawing = flatten_icon_svg(DRAWING, color="#1D2B36", point_budget=None, palette=PALETTE)
    assert drawing.multicolor and drawing.snapped_paints == 1  # #1d2b37 moved onto #1D2B36
    fills = {primitive.get("fill") for primitive in drawing.primitives}
    assert {"#0E7C7B", "#F2545B"} <= fills
    circle = next(primitive for primitive in drawing.primitives if primitive["tag"] == "circle")
    assert circle["opacity"] == 0.5
    stroked = [primitive for primitive in drawing.primitives if primitive.get("stroke") not in (None, "none")]
    assert stroked and all(primitive["stroke"] == "#1D2B36" for primitive in stroked)
    group = icon_group(drawing, x=0, y=200, width=400, height=200)
    validate_svg(_page(group))
    rendered = Image.open(io.BytesIO(rasterize_icon(drawing, size=200))).convert("RGBA")
    assert rendered.getpixel((50, 100)) == (14, 124, 123, 255)  # the teal square (y offset 50), opaque
    assert FlattenedIcon.from_json(drawing.to_json()) == drawing


def test_monochrome_mode_still_inks_every_paint_and_now_keeps_opacity():
    icon = flatten_icon_svg(DRAWING, color="#123456", point_budget=None)
    assert not icon.multicolor
    assert {primitive.get("fill") for primitive in icon.primitives} <= {"#123456", "none"}
    assert any(primitive.get("opacity") == 0.5 for primitive in icon.primitives)


def test_a_compound_path_exports_as_one_custom_shape(tmp_path):
    from workers.public_svg_export_worker import VENDOR_ROOT

    source = tmp_path / "slide-1.svg"
    source.write_text(_page(icon_group(flatten_icon_svg(HOLED, color="#112233"), x=200, y=200,
                                       width=200, height=200)), encoding="utf-8")
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
        assert not any(name.startswith("ppt/media/") for name in package.namelist())
    shapes = re.findall(r"<a:custGeom>.*?</a:custGeom>", slide, re.S)
    assert len(shapes) == 1 and shapes[0].count("<a:moveTo>") == 2
