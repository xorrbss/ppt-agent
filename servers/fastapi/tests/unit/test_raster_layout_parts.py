"""Page zones of a redesign page: header chrome, footer line and content (each region once)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import test_raster_redesign_layout_fixtures as fx
from services.raster_source_assets import raster_slot_id
from services.source_content.raster_layout_parts import page_parts
from services.source_content.raster_redesign_layout import render_redesign_page_svg
from services.svg_contract import SVG_NS

NS = f"{{{SVG_NS}}}"


def _image(svg, region_id):
    root = ET.fromstring(svg)
    return next(n for n in root.iter(f"{NS}image") if n.get("href") == raster_slot_id(1, region_id))


def _header_rule_y(svg):
    root = ET.fromstring(svg)
    return min(float(n.get("y1")) for n in root.iter(f"{NS}line") if n.get("y1") == n.get("y2")
               and float(n.get("y1")) < 400)


def _page_with(*extra):
    page = fx.semantic_steps(1)
    page.regions = tuple(fx._ordered([*page.regions, *extra]))
    return page


def test_header_group_member_inside_the_content_area_is_content():
    # A label the analysis grouped as "header" but that sits below the top of
    # the content (e.g. a table column heading) is content, drawn exactly once.
    stray = fx.region(1, "stray", "screenshot", (60, 230, 200, 60), group="header")
    stray.review_reason, stray.editable_fallback = "uncovered_ink", True
    page = _page_with(stray)
    analysis = fx.analysis(page)
    parts = page_parts(analysis, page)
    assert "p1-stray" in {item.region.region_id for item in parts.items}
    assert "p1-stray" not in {item.region.region_id for item in parts.header_images}
    svg = render_redesign_page_svg(analysis, 1)
    assert svg.count(raster_slot_id(1, "p1-stray")) == 1
    assert float(_image(svg, "p1-stray").get("y")) > _header_rule_y(svg)  # below the header rule


def test_header_group_keeps_members_above_the_content():
    page = fx.semantic_steps(1)
    for region in page.regions:
        if region.region_id in {"p1-kicker", "p1-subtitle"}:
            region.group_id = "header"
    parts = page_parts(fx.analysis(page), page)
    header_ids = {item.region.region_id for item in (*parts.kickers, *parts.subtitles)}
    assert {"p1-kicker", "p1-subtitle"} <= header_ids
    assert not header_ids & {item.region.region_id for item in parts.items}


def test_header_subtitle_kept_as_pixels_is_drawn_at_subtitle_scale_under_the_title():
    page = fx.semantic_steps(1)
    regions = [r for r in page.regions if r.region_id != "p1-subtitle"]
    line = fx.region(1, "px-subtitle", "screenshot", (40, 112, 700, 30), group="header")
    line.review_reason, line.editable_fallback = "low_confidence_text_pixels", True
    page.regions = tuple(fx._ordered([*regions[:2], line, *regions[2:]]))
    svg = render_redesign_page_svg(fx.analysis(page), 1)
    node = _image(svg, "p1-px-subtitle")
    height, width = int(node.get("height")), int(node.get("width"))
    # A text line at the 18.4-unit subtitle size (not a 38-unit-high logo capped at 170 wide).
    assert 18 <= height <= 40 and width > 170
    assert float(node.get("x")) < 100  # left, under the title, not in the top-right brand cluster
    assert float(node.get("y")) + height <= _header_rule_y(svg)
