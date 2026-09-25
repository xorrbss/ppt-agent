"""Tests for the SVG driver's deterministic fallback page.

Covers: contract validity across content shapes (Korean, empty, very long,
markup-hostile characters), determinism, page-number/group markers, eyebrow
omission, style-token colour propagation, and the height budget.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from services.generation_core.drivers.svg.fallback import (
    FALLBACK_GROUP_ID,
    fallback_page_svg,
)
from services.svg_contract import PAGE_NUMBER_ID, SVG_NS, validate_svg

WIDTH, HEIGHT = 1280, 720
_KOREAN_CONTENT = (
    "분기 실적 요약\n"
    "- 매출은 전년 대비 12% 성장했다\n"
    "- 신규 고객 확보 비용이 감소했다\n"
    "- 해외 채널 매출 비중이 늘었다\n"
    "- 재고 회전율이 개선되었다\n"
    "- 다음 분기 목표는 두 자릿수 성장 유지다\n"
)


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def _text_elements(svg: str) -> list[ET.Element]:
    root = ET.fromstring(svg)
    return [node for node in root.iter(_tag("text"))]


def test_typical_korean_outline_is_contract_valid():
    svg = fallback_page_svg(
        content=_KOREAN_CONTENT, role="business review", index=0, count=5,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    result = validate_svg(svg, width=WIDTH, height=HEIGHT)
    assert result["syntax_valid"] is True
    assert "매출" in result["text"]


def test_empty_content_is_contract_valid():
    svg = fallback_page_svg(
        content="", role="", index=0, count=1,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    result = validate_svg(svg, width=WIDTH, height=HEIGHT)
    assert result["syntax_valid"] is True
    assert result["text"].strip() != ""


def test_many_long_bullets_truncate_and_stay_within_height():
    content = "긴 제목입니다\n" + "\n".join(
        "- 이것은 매우 긴 불릿 항목입니다 " * 6 + str(i) for i in range(30)
    )
    svg = fallback_page_svg(
        content=content, role="detail", index=2, count=10,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    validate_svg(svg, width=WIDTH, height=HEIGHT)  # must not raise
    root = ET.fromstring(svg)
    texts = [node for node in root.iter(_tag("text")) if node.get("id") != PAGE_NUMBER_ID]
    max_y = max(float(node.get("y")) for node in texts)
    assert max_y <= HEIGHT - 72
    last_body_text = texts[-1].text or ""
    assert last_body_text.endswith("…")


def test_markup_hostile_characters_survive_round_trip():
    content = 'title <tag> & "quotes\' here\n- a < b & c > d "quoted" \'text\''
    svg = fallback_page_svg(
        content=content, role="", index=0, count=1,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    result = validate_svg(svg, width=WIDTH, height=HEIGHT)
    assert "<" in result["text"] or "&" in result["text"] or '"' in result["text"]


def test_deterministic_same_inputs_same_bytes():
    kwargs = dict(
        content=_KOREAN_CONTENT, role="ops", index=1, count=4,
        width=WIDTH, height=HEIGHT,
        style_snapshot={"tokens": {"primary": "#112233", "body_font": "Pretendard"}},
    )
    first = fallback_page_svg(**kwargs)
    second = fallback_page_svg(**kwargs)
    assert first == second


def test_page_number_present_and_group_id_present():
    svg = fallback_page_svg(
        content=_KOREAN_CONTENT, role="ops", index=3, count=7,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    root = ET.fromstring(svg)
    ids = {node.get("id") for node in root.iter() if node.get("id")}
    assert PAGE_NUMBER_ID in ids
    assert FALLBACK_GROUP_ID in ids
    page_number_node = next(node for node in root.iter(_tag("text")) if node.get("id") == PAGE_NUMBER_ID)
    assert page_number_node.text == "04"


def test_eyebrow_omitted_when_role_empty():
    with_role = fallback_page_svg(
        content="제목\n- 본문", role="설명", index=0, count=1,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    without_role = fallback_page_svg(
        content="제목\n- 본문", role="", index=0, count=1,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    root_with = ET.fromstring(with_role)
    root_without = ET.fromstring(without_role)
    assert any(node.text == "설명" for node in root_with.iter(_tag("text")))
    assert not any(node.text == "설명" for node in root_without.iter(_tag("text")))


def test_accent_colour_from_style_snapshot_tokens():
    accent = "#AA5500"
    svg = fallback_page_svg(
        content="제목\n- 본문", role="role", index=0, count=1,
        width=WIDTH, height=HEIGHT,
        style_snapshot={"tokens": {"primary": accent}},
    )
    root = ET.fromstring(svg)
    fills = {node.get("fill") for node in root.iter() if node.get("fill")}
    assert accent in fills


def test_no_body_text_exceeds_height_budget_except_page_number():
    svg = fallback_page_svg(
        content=_KOREAN_CONTENT, role="ops", index=0, count=1,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    root = ET.fromstring(svg)
    for node in root.iter(_tag("text")):
        if node.get("id") == PAGE_NUMBER_ID:
            continue
        assert float(node.get("y")) <= HEIGHT - 72


@pytest.mark.parametrize("width,height", [(1280, 720), (960, 540)])
def test_various_canvas_sizes_are_contract_valid(width, height):
    svg = fallback_page_svg(
        content=_KOREAN_CONTENT, role="ops", index=0, count=2,
        width=width, height=height, style_snapshot=None,
    )
    validate_svg(svg, width=width, height=height)
