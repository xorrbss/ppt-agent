"""Icon flattening and embedding for the SVG generation driver."""
from __future__ import annotations

import io
import math
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from services.generation_core.drivers.svg.embed_assets import (
    ICON_POINT_BUDGET,
    FlattenedIcon,
    IconFlattenBudgetExceeded,
    apply_vector_icon_quality,
    flatten_icon_svg,
    icon_group,
    rasterize_icon,
    replace_vector_icon_images,
)
from services.svg_contract import SVG_NS, validate_svg

INK = "#1F4E79"
LIBRARY = Path(__file__).resolve().parents[4] / "assets" / "infographics" / "monochrome-v1" / "originals"
ICON_FILES = sorted(LIBRARY.glob("*.svg"))


def _tinted(path: Path) -> str:
    # Mirrors services/authored_icon_pack/library.py::lookup_reusable_icons.
    return path.read_text(encoding="utf-8").replace("#000000", INK).replace("currentColor", INK)


def _canvas(*children: ET.Element) -> ET.Element:
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": "1280", "height": "720", "viewBox": "0 0 1280 720"})
    title = ET.SubElement(root, f"{{{SVG_NS}}}text", {"x": "40", "y": "60", "font-family": "Noto Sans KR",
                                                      "font-size": "32", "fill": "#111111"})
    title.text = "Title"
    root.extend(children)
    return root


def _svg(body: str, view: str = "0 0 24 24") -> str:
    return (f'<svg xmlns="{SVG_NS}" viewBox="{view}" fill="none" stroke="#000" stroke-width="2">'
            f"{body}</svg>")


@pytest.fixture(scope="module")
def library_icons() -> dict[str, FlattenedIcon]:
    assert len(ICON_FILES) == 300
    return {path.name: flatten_icon_svg(_tinted(path), color=INK, point_budget=None) for path in ICON_FILES}


def test_every_library_icon_flattens_and_budget_fits_the_majority(library_icons):
    counts = sorted(icon.point_count for icon in library_icons.values())
    over = [name for name, icon in library_icons.items() if icon.point_count > ICON_POINT_BUDGET]
    p90 = counts[int(0.9 * len(counts))]
    print(f"\nicon points: min={counts[0]} median={statistics.median(counts)} p90={p90} "
          f"max={counts[-1]} budget={ICON_POINT_BUDGET} over_budget={len(over)}/300 {over}")
    assert len(over) <= 15  # the large majority must take the vector path
    for icon in library_icons.values():
        assert icon.color == INK and icon.primitives and icon.stroke_width > 0
        assert all(item["tag"] in {"polyline", "polygon", "rect", "circle", "ellipse", "line"}
                   for item in icon.primitives)
        assert FlattenedIcon.from_json(icon.to_json()) == icon


def test_every_library_icon_group_passes_the_contract(library_icons):
    for name, icon in library_icons.items():
        group = icon_group(icon, x=100.5, y=200.25, width=96, height=64, element_id="asset-icon")
        assert group.tag == f"{{{SVG_NS}}}g"
        for node in group.iter():
            assert "scale" not in node.get("transform", "")
        try:
            validate_svg(ET.tostring(_canvas(group), encoding="unicode"))
        except ValueError as error:  # pragma: no cover - diagnostic
            pytest.fail(f"{name}: {error}")


def test_icon_group_contains_and_centres_the_icon():
    icon = flatten_icon_svg(_svg('<rect x="0" y="0" width="24" height="24"/>'), color="#abc")
    group = icon_group(icon, x=10, y=20, width=200, height=100)
    assert group.get("transform") == "translate(60,20)"
    rect = group[0]
    assert (rect.get("width"), rect.get("height")) == ("100", "100")
    assert group.get("stroke") == "#AABBCC" and group.get("stroke-width") == "8.33"


def test_over_budget_raises():
    svg = _tinted(LIBRARY / "process-06-gear-pair.svg")
    with pytest.raises(IconFlattenBudgetExceeded):
        flatten_icon_svg(svg, color=INK, point_budget=20)
    assert flatten_icon_svg(svg, color=INK, point_budget=None).point_count > 20


@pytest.mark.parametrize("body", [
    '<text x="1" y="2">A</text>', '<image href="x.png" width="4" height="4"/>',
    '<use href="#a"/>', '<path d="M0 0L4 4" stroke="url(#g)"/>', '<path style="fill:red" d="M0 0L1 1"/>',
])
def test_unsupported_content_is_rejected(body):
    with pytest.raises(ValueError):
        flatten_icon_svg(_svg(body), color=INK)


def test_declarations_are_rejected():
    with pytest.raises(ValueError, match="icon_declaration_forbidden"):
        flatten_icon_svg('<!DOCTYPE svg [<!ENTITY a "b">]>' + _svg('<path d="M0 0L1 1"/>'), color=INK)


def test_rasterize_produces_alpha_png_with_ink(library_icons):
    png = rasterize_icon(library_icons["ai-01-neural-network.svg"], size=128)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.mode == "RGBA" and image.size == (128, 128)
    for corner in [(0, 0), (127, 0), (0, 127), (127, 127)]:
        assert image.getpixel(corner)[3] == 0
    pixels = [image.getpixel((x, y)) for y in range(128) for x in range(128)]
    assert sum(pixel[3] == 255 for pixel in pixels) > 500
    assert {pixel[:3] for pixel in pixels} == {(0x1F, 0x4E, 0x79)}


def test_replace_vector_icon_images_keeps_position_and_order(library_icons):
    icon = library_icons["finance-01-coin-stack.svg"]

    def image(slot: str, x: str) -> ET.Element:
        return ET.Element(f"{{{SVG_NS}}}image", {"x": x, "y": "300", "width": "120", "height": "120",
                                                 "href": slot, "preserveAspectRatio": "xMidYMid meet"})

    layer = ET.Element(f"{{{SVG_NS}}}g", {"id": "asset-icon-1"})
    rect = ET.SubElement(layer, f"{{{SVG_NS}}}rect", {"x": "0", "y": "0", "width": "10", "height": "10"})
    layer.extend([image("icon-1", "100"), image("icon-1", "400")])
    unknown = image("source-slot-a", "700")
    root = _canvas(layer, unknown)
    replaced = replace_vector_icon_images(root, {"icon-1": icon})
    assert replaced == {"icon-1"}
    assert layer[0] is rect and [child.tag for child in layer[1:]] == [f"{{{SVG_NS}}}g"] * 2
    ids = [layer[1].get("id"), layer[2].get("id")]
    assert len(set(ids + ["asset-icon-1"])) == 3 and all(value.startswith("asset-icon-1") for value in ids)
    assert layer[1].get("transform") == "translate(100,300)"
    assert layer[2].get("transform") == "translate(400,300)"
    assert root[-1] is unknown and unknown.get("href") == "source-slot-a"
    root.remove(unknown)
    validate_svg(ET.tostring(root, encoding="unicode"))


def test_replace_ignores_unknown_hrefs():
    root = _canvas(ET.Element(f"{{{SVG_NS}}}image", {"x": "0", "y": "0", "width": "10", "height": "10",
                                                     "href": "other"}))
    before = ET.tostring(root)
    assert replace_vector_icon_images(root, {}) == set()
    assert ET.tostring(root) == before


def test_quality_noop_without_offer():
    for payload in ({}, {"assetOffer": {}}, {"assetOffer": {"vectorIcons": {"offered": [], "used": []}}}):
        quality = {"passed": True}
        apply_vector_icon_quality(quality, payload)
        assert quality == {"passed": True}


def test_quality_records_omitted_icons():
    quality = {"passed": True, "failures": [{"kind": "other"}]}
    apply_vector_icon_quality(quality, {"assetOffer": {"vectorIcons": {"offered": ["b", "a", "c"], "used": ["b"]}}})
    assert quality["passed"] is False
    assert quality["assetOffer"] == {"offered": ["a", "b", "c"], "used": ["b"], "omitted": ["a", "c"]}
    assert quality["failures"][-1] == {"kind": "asset_icon_omitted", "slotIds": ["a", "c"]}


def test_quality_all_used_leaves_passed_untouched():
    quality = {"passed": True}
    apply_vector_icon_quality(quality, {"assetOffer": {"vectorIcons": {"offered": ["a"], "used": ["a"]}}})
    assert quality == {"passed": True, "assetOffer": {"offered": ["a"], "used": ["a"], "omitted": []}}


def test_arc_samples_stay_within_tolerance():
    tolerance = 0.05
    icon = flatten_icon_svg(_svg('<path d="M 0 12 A 12 12 0 0 0 24 12"/>'), color=INK, tolerance=tolerance)
    (arc,) = icon.primitives
    points = arc["points"]
    assert arc["tag"] == "polyline" and points[0] == [0, 12] and points[-1] == [24, 12]
    assert len(points) > 8
    for x, y in points:
        assert abs(math.hypot(x - 12, y - 12) - 12) < 1e-3
        assert y >= 12 - 1e-6  # sweep-flag 0 from (0,12) to (24,12) is the lower half
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        assert 12 - math.hypot((ax + bx) / 2 - 12, (ay + by) / 2 - 12) <= tolerance + 1e-6


def test_arc_radii_are_scaled_up_when_too_small():
    icon = flatten_icon_svg(_svg('<path d="M 0 12 A 1 1 0 0 1 24 12"/>'), color=INK)
    for x, y in icon.primitives[0]["points"]:
        assert abs(math.hypot(x - 12, y - 12) - 12) < 1e-3 and y <= 12 + 1e-6


def test_transforms_are_baked():
    body = ('<g transform="translate(10 20) scale(2)"><circle cx="1" cy="2" r="3"/>'
            '<path d="M1 1h2v3z"/><rect x="0" y="0" width="1" height="1"/></g>'
            '<g transform="scale(2 1)"><circle cx="5" cy="5" r="1"/></g>')
    icon = flatten_icon_svg(_svg(body, "0 0 64 64"), color=INK)
    circle, path, rect, ellipse = icon.primitives
    assert circle == {"tag": "circle", "cx": 12, "cy": 24, "r": 6, "fill": "none"}
    assert path["tag"] == "polyline" and path["points"] == [[12, 22], [16, 22], [16, 28], [12, 22]]
    assert rect == {"tag": "rect", "x": 10, "y": 20, "width": 2, "height": 2, "fill": "none"}
    assert ellipse == {"tag": "ellipse", "cx": 10, "cy": 5, "rx": 2, "ry": 1, "fill": "none",
                       "stroke-width": pytest.approx(2 * math.sqrt(2), abs=1e-4)}
    assert icon.stroke_width == 4  # stroke-width 2 under scale(2)


def test_filled_closed_path_becomes_polygon_and_dots_become_circles():
    icon = flatten_icon_svg(_svg('<path fill="#000" d="M2 2h6v6z"/><path d="M12 12h.01"/>'), color=INK)
    polygon, dot = icon.primitives
    assert polygon == {"tag": "polygon", "points": [[2, 2], [8, 2], [8, 8]], "fill": INK}
    assert dot["tag"] == "circle" and dot["r"] == 1 and dot["stroke"] == "none" and dot["fill"] == INK


def test_rounded_rect_becomes_polygon_outline():
    icon = flatten_icon_svg(_svg('<rect x="2" y="2" width="20" height="10" rx="3"/>'), color=INK)
    (outline,) = icon.primitives
    assert outline["tag"] == "polyline" and outline["points"][0] == outline["points"][-1]
    xs = [x for x, _ in outline["points"]]
    assert min(xs) == pytest.approx(2) and max(xs) == pytest.approx(22)


def test_template_redesign_icons_get_body_ids(library_icons, monkeypatch):
    # A template-redesign body may only carry body- IDs; a live template deck
    # failed every icon page with svg_template_body_namespace_required.
    from services.generation_core.drivers.svg import embed_assets
    from services.svg_templates import generation

    icon = library_icons["finance-01-coin-stack.svg"]
    root = _canvas(ET.Element(f"{{{SVG_NS}}}image", {"x": "10", "y": "10", "width": "48", "height": "48",
                                                     "href": "icon-1", "preserveAspectRatio": "xMidYMid meet"}))
    replace_vector_icon_images(root, {"icon-1": icon}, id_prefix="body-asset-")
    assert [node.get("id") for node in root.iter(f"{{{SVG_NS}}}g") if node.get("transform")] == ["body-asset-icon-1"]

    monkeypatch.setattr(generation, "template_selection", lambda snapshot: snapshot.get("selection"))
    assert embed_assets.vector_icon_id_prefix({"selection": {"mode": "template-redesign"}}) == "body-asset-"
    assert embed_assets.vector_icon_id_prefix({"selection": {"mode": "free-design"}}) == "asset-"
    assert embed_assets.vector_icon_id_prefix({}) == "asset-"
    assert embed_assets.vector_icon_id_prefix(None) == "asset-"
