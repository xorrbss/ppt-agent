"""Anchoring loose content to its source section and reading position before a reflow."""

from __future__ import annotations

import test_raster_redesign_layout_fixtures as fx
from services.source_content.raster_layout_anchor import anchor_items
from services.source_content.raster_redesign_plan import build_items

SIZE = (fx.SOURCE_W, fx.SOURCE_H)


def _items(*regions):
    _, _, items = build_items(fx._page(1, fx._ordered(list(regions))))
    return items


def _ids(items):
    return [(item.region.region_id.removeprefix("p1-"), item.region.group_id) for item in items]


def test_loose_screenshot_under_a_step_card_joins_that_card_after_its_text():
    items = _items(
        fx.region(1, "step0", "body", (30, 175, 380, 90), "01 신청 정보 입력\n신청자 정보를 입력합니다.", group="s0"),
        fx.region(1, "step1", "body", (435, 175, 380, 90), "02 물품 정보 입력\n물품 정보를 입력합니다.", group="s1"),
        fx.region(1, "pain", "body", (30, 800, 1500, 60), "기존 방식의 어려움을 정리합니다.", group="p"),
        fx.region(1, "shot0", "screenshot", (40, 270, 360, 420)),  # appended last by the structurer
        fx.region(1, "shot1", "screenshot", (445, 270, 360, 420)),
    )
    assert _ids(anchor_items(items, SIZE)) == [
        ("step0", "s0"), ("shot0", "s0"), ("step1", "s1"), ("shot1", "s1"), ("pain", "p"),
    ]


def test_badge_touching_a_heading_joins_its_section_before_the_heading():
    items = _items(
        fx.region(1, "head", "body", (970, 190, 400, 30), "하나의 통합 분석 경험", group="g7"),
        fx.region(1, "sub", "body", (970, 226, 640, 20), "직원이 질문을 입력하면 AI가 준비합니다.", group="g7"),
        fx.region(1, "badge", "screenshot", (800, 180, 160, 60)),
    )
    assert _ids(anchor_items(items, SIZE)) == [("badge", "g7"), ("head", "g7"), ("sub", "g7")]


def test_loose_picture_between_two_sections_keeps_its_reading_position():
    items = _items(
        fx.region(1, "top", "body", (40, 150, 1500, 60), "상단 섹션 설명입니다.", group="a"),
        fx.region(1, "bottom", "body", (40, 800, 1500, 60), "하단 섹션 설명입니다.", group="b"),
        fx.region(1, "chart", "screenshot", (300, 300, 900, 400)),  # far from both: stays loose
    )
    assert _ids(anchor_items(items, SIZE)) == [("top", "a"), ("chart", None), ("bottom", "b")]


def test_ungrouped_page_is_left_to_the_source_geometry_sections():
    items = _items(
        fx.region(1, "text", "body", (40, 150, 700, 60), "설명 문단입니다."),
        fx.region(1, "shot", "screenshot", (40, 215, 700, 400)),
    )
    assert anchor_items(items, SIZE) == items
