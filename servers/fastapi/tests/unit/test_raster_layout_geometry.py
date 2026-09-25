"""Source-geometry composition: dense pages keep their arrangement block by block, not as one picture."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

import test_raster_redesign_layout_fixtures as fx
from services.raster_source_assets import _approved_crop_regions, raster_slot_id
from services.source_content.raster_redesign_layout import render_redesign_page
from services.source_content.raster_svg_layout import raster_page_render_advisories
from services.svg_contract import SVG_NS

NS = f"{{{SVG_NS}}}"
LONG = "\n".join(f"{index}. 매우 조밀한 원본 문장이 페이지 전체에 이어집니다" for index in range(14))


def _dense_page(*extra):
    regions = [fx.region(1, "title", "title", (40, 40, 700, 70), "원본 배치를 유지하는 조밀한 페이지")]
    for index in range(6):
        x = 30 + index * 270
        regions.append(fx.region(1, f"text{index}", "body", (x, 150, 260, 380), f"항목 {index}\n{LONG}",
                                 group=f"g{index}", heading=1))
        regions.append(fx.region(1, f"shot{index}", "screenshot", (x, 540, 260, 300), group=f"g{index}"))
    return fx._page(1, fx._ordered([*regions, *extra]))


def _texts(svg):
    return {node.get("id"): "".join(node.itertext()) for node in ET.fromstring(svg).iter(f"{NS}text")}


def _hrefs(svg):
    return [node.get("href") for node in ET.fromstring(svg).iter(f"{NS}image")]


def test_ungrouped_label_that_fits_its_source_box_stays_editable():
    label = fx.region(1, "label", "body", (40, 860, 300, 50), "요약 지표")
    analysis = fx.analysis(_dense_page(label))
    result = render_redesign_page(analysis, 1)
    assert "page-content" not in {a["groupId"] for a in result.advisories}
    assert "요약 지표" in _texts(result.svg).values()
    assert not any(a["groupId"].startswith("region-") for a in result.advisories)


def test_text_that_would_overlap_a_group_crop_is_shown_as_its_own_crop_once():
    # An ungrouped caption inside group g0's source box: g0's crop already shows
    # its pixels, so it may not also be drawn as editable text on top of them.
    caption = fx.region(1, "caption", "body", (40, 500, 240, 30), "겹치는 캡션")
    analysis = fx.analysis(_dense_page(caption))
    result = render_redesign_page(analysis, 1)
    groups = [a["groupId"] for a in result.advisories]
    assert "region-p1-caption" in groups and "page-content" not in groups
    assert "겹치는 캡션" not in _texts(result.svg).values()
    assert raster_page_render_advisories(analysis, 1, result.svg) == result.advisories
    hrefs = _hrefs(result.svg)
    assert len(hrefs) == len(set(hrefs))  # every slot drawn once
    page = analysis.pages[0]
    pin = SimpleNamespace(approved_region_ids=tuple(r.region_id for r in page.regions), full_page_preserve_pages=(),
                          automatic_preserve_pages=(), automatic_redesign_pages=(1,))
    slots = {raster_slot_id(1, r.region_id) for r in _approved_crop_regions(analysis, pin, 1, result.svg)}
    assert slots == set(hrefs)  # export cuts exactly the crops the SVG uses


def test_geometry_crops_follow_the_source_positions():
    analysis = fx.analysis(_dense_page())
    result = render_redesign_page(analysis, 1)
    root = ET.fromstring(result.svg)
    images = {node.get("href"): node for node in root.iter(f"{NS}image")}
    from services.source_content.raster_redesign_fallback import fallback_region_id

    first = images[raster_slot_id(1, fallback_region_id(1, "g0"))]
    second = images[raster_slot_id(1, fallback_region_id(1, "g1"))]
    scale = float(first.get("width")) / 260
    # 270 source px apart -> 270 * scale target units apart; no frame, no gap policy.
    assert abs(float(second.get("x")) - float(first.get("x")) - 270 * scale) < 1.5
    assert first.get("y") == second.get("y")
