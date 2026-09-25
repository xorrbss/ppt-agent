"""Coherence of a reflow against the source: sections, reading order, labels, composites."""

from __future__ import annotations

from types import SimpleNamespace

import test_raster_redesign_layout_fixtures as fx
from services.source_content.raster_layout_coherence import coherence_issue
from services.source_content.raster_redesign_layout import render_redesign_page
from services.source_content.raster_redesign_tree import Item, Placed

ANALYSIS = fx.analysis(fx._page(1, []))


def _item(region, kind=None):
    kind = kind or ("image" if region.role in {"screenshot", "logo"} else "text")
    return Item(kind, region)


def _at(region, x, y, w, h, kind=None):
    return Placed("image" if (kind or region.role) in {"screenshot", "logo", "image"} else "table",
                  x, y, w, h, _item(region, "image" if region.role in {"screenshot", "logo"} else "table"))


def _page(*regions):
    return SimpleNamespace(page_number=1, regions=tuple(regions))


def test_source_arrangement_scaled_uniformly_is_coherent():
    a = fx.region(1, "a", "screenshot", (100, 200, 400, 300))
    b = fx.region(1, "b", "screenshot", (500, 200, 300, 300))
    placements = [_at(a, 50, 100, 200, 150), _at(b, 250, 100, 150, 150)]
    assert coherence_issue(ANALYSIS, _page(a, b), placements) is None


def test_touching_screenshot_tiles_torn_apart_are_incoherent():
    a = fx.region(1, "a", "screenshot", (100, 200, 400, 300))
    b = fx.region(1, "b", "screenshot", (500, 200, 300, 300))
    # Same scale but b moved under a: the composite is torn.
    placements = [_at(a, 50, 100, 200, 150), _at(b, 50, 260, 150, 150)]
    assert coherence_issue(ANALYSIS, _page(a, b), placements) == "composite_torn"
    # Kept side by side but at different scales.
    placements = [_at(a, 50, 100, 200, 150), _at(b, 250, 100, 300, 300)]
    assert coherence_issue(ANALYSIS, _page(a, b), placements) == "composite_torn"


def test_distant_pictures_may_move_freely():
    a = fx.region(1, "a", "screenshot", (100, 200, 400, 300))
    b = fx.region(1, "b", "screenshot", (1100, 600, 300, 300))
    placements = [_at(a, 50, 100, 200, 150), _at(b, 50, 300, 300, 300)]
    assert coherence_issue(ANALYSIS, _page(a, b), placements) is None


def test_badge_moved_away_from_the_heading_it_touches_is_incoherent():
    badge = fx.region(1, "badge", "screenshot", (800, 180, 160, 60))
    heading = fx.region(1, "heading", "body", (970, 190, 400, 30), "하나의 통합 분석 경험", group="g7")
    body = fx.region(1, "body", "body", (970, 230, 640, 20), "직원이 질문을 입력하면 AI가 준비합니다.", group="g7")
    page = _page(badge, heading, body)
    near = [_at(badge, 400, 100, 80, 30), Placed("text", 490, 100, 300, 20, _item(heading), lines=[]),
            Placed("text", 490, 125, 300, 20, _item(body), lines=[])]
    assert coherence_issue(ANALYSIS, page, near) is None
    far = [near[1], near[2], _at(badge, 400, 600, 80, 30)]
    assert coherence_issue(ANALYSIS, page, far) == "label_detached"


def test_group_interleaved_with_another_unit_is_split():
    a1 = fx.region(1, "a1", "screenshot", (100, 100, 300, 200), group="ga")
    a2 = fx.region(1, "a2", "screenshot", (100, 320, 300, 200), group="ga")
    other = fx.region(1, "other", "screenshot", (1200, 100, 300, 200))
    placements = [_at(a1, 50, 100, 150, 100), _at(other, 50, 210, 150, 100), _at(a2, 50, 320, 150, 100)]
    assert coherence_issue(ANALYSIS, _page(a1, a2, other), placements) == "group_split"


def test_stacking_order_inverted_against_the_source_is_incoherent():
    top = fx.region(1, "top", "screenshot", (100, 100, 300, 150), group="g1")
    below = fx.region(1, "below", "screenshot", (100, 700, 300, 150), group="g2")
    placements = [_at(top, 50, 400, 150, 75), _at(below, 50, 100, 150, 75)]
    assert coherence_issue(ANALYSIS, _page(top, below), placements) == "order_inverted"
    # Side-by-side columns stacked as rows keep the reading order: allowed.
    right = fx.region(1, "right", "screenshot", (900, 100, 300, 150), group="g2")
    placements = [_at(top, 50, 100, 150, 75), _at(right, 50, 200, 150, 75)]
    assert coherence_issue(ANALYSIS, _page(top, right), placements) is None


def test_touching_tiles_reflow_as_one_composite_in_source_arrangement():
    import xml.etree.ElementTree as ET

    from services.raster_source_assets import raster_slot_id
    from services.svg_contract import SVG_NS

    analysis = _composite_page()
    result = render_redesign_page(analysis, 1)
    assert (result.composition, result.incoherence) == ("reflow", None)
    images = {n.get("href"): n for n in ET.fromstring(result.svg).iter(f"{{{SVG_NS}}}image")}
    tiles = [images[raster_slot_id(1, f"p1-tile{index}")] for index in range(3)]
    scale = float(tiles[0].get("width")) / 700
    # tile1 sits right of tile0, tile2 under both: source offsets at one scale.
    assert abs(float(tiles[1].get("x")) - float(tiles[0].get("x")) - 700 * scale) < 2
    assert abs(float(tiles[2].get("y")) - float(tiles[0].get("y")) - 300 * scale) < 2
    texts = "".join("".join(n.itertext()) for n in ET.fromstring(result.svg).iter(f"{{{SVG_NS}}}text"))
    assert "오른쪽설명문단입니다." in texts.replace(" ", "")  # the note stays editable


def _composite_page():
    regions = [fx.region(1, "title", "title", (40, 40, 700, 70), "합성 화면 페이지")]
    for index, (x, y, w, h) in enumerate([(60, 200, 700, 300), (760, 200, 500, 300), (60, 500, 1200, 350)]):
        regions.append(fx.region(1, f"tile{index}", "screenshot", (x, y, w, h)))
    regions.append(fx.region(1, "note", "body", (1300, 200, 330, 120), "오른쪽 설명 문단입니다.\n두 번째 줄입니다."))
    return fx.analysis(fx._page(1, fx._ordered(regions)))


def test_geometry_fallback_is_recorded_as_a_receipt_advisory_recovered_from_the_svg(monkeypatch):
    from services.raster_source_assets import raster_slot_id
    from services.source_content import raster_redesign_layout
    from services.source_content.raster_redesign_plan import redesign_crop_regions
    from services.source_content.raster_svg_layout import raster_page_render_advisories
    from services.svg_contract import validate_svg

    # No reflow keeps the source structure (forced): the page keeps the source arrangement.
    monkeypatch.setattr(raster_redesign_layout, "coherence_issue", lambda *args: "composite_torn")
    analysis = _composite_page()
    result = render_redesign_page(analysis, 1)
    assert (result.composition, result.incoherence) == ("geometry", "composite_torn")
    expected = {"code": "source_geometry_layout", "groupId": "source-geometry", "reason": "composite_torn"}
    assert expected in result.advisories
    assert raster_page_render_advisories(analysis, 1, result.svg) == result.advisories
    slots = {raster_slot_id(1, r.region_id) for r in redesign_crop_regions(analysis.pages[0])}
    validate_svg(result.svg, width=1188, height=840,
                 source_manifest={slot: {"required": False, "allowOmit": True} for slot in slots})


def test_overflow_geometry_records_content_overflow_and_a_coherent_reflow_records_nothing():
    import test_raster_layout_geometry as geo
    from services.source_content.raster_svg_layout import raster_page_render_advisories

    analysis = fx.analysis(geo._dense_page())
    result = render_redesign_page(analysis, 1)
    assert result.composition == "geometry"
    assert {"code": "source_geometry_layout", "groupId": "source-geometry",
            "reason": "content_overflow"} in raster_page_render_advisories(analysis, 1, result.svg)
    plain = fx.analysis(fx.semantic_hero(1))
    reflow = render_redesign_page(plain, 1)
    assert reflow.composition == "reflow" and not any(
        a["code"] in {"source_geometry_layout", "page_content_layout"} for a in reflow.advisories)
