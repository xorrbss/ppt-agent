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


def _body_words(svg: str) -> str:
    texts = [node.text or "" for node in _text_elements(svg) if node.get("id") != PAGE_NUMBER_ID]
    return " ".join(text.removeprefix("• ") for text in texts)


def test_many_long_bullets_keep_every_word_and_stay_within_height():
    # The fallback used to cut this outline to ten 150-character bullets; it is
    # the complete-content rescue now, so every line survives, set smaller.
    bullets = ["이것은 매우 긴 불릿 항목입니다 " * 6 + str(i) for i in range(30)]
    content = "긴 제목입니다\n" + "\n".join("- " + bullet for bullet in bullets)
    svg = fallback_page_svg(
        content=content, role="detail", index=2, count=10,
        width=WIDTH, height=HEIGHT, style_snapshot=None,
    )
    validate_svg(svg, width=WIDTH, height=HEIGHT)  # must not raise
    root = ET.fromstring(svg)
    texts = [node for node in root.iter(_tag("text")) if node.get("id") != PAGE_NUMBER_ID]
    max_y = max(float(node.get("y")) for node in texts)
    assert max_y <= HEIGHT - 72
    assert "…" not in _body_words(svg)
    words = "".join(_body_words(svg).split())
    for bullet in bullets:
        assert "".join(bullet.split()) in words


def test_a_long_title_is_drawn_whole_without_a_cut():
    title = "제목 " * 40 + "끝"
    svg = fallback_page_svg(content=title + "\n- 본문", role="", index=0, count=1,
                            width=WIDTH, height=HEIGHT, style_snapshot=None)
    assert "".join(title.split()) in "".join(_body_words(svg).split())


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


def _page_texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [node.text or "" for node in root.iter(f"{{{SVG_NS}}}text") if node.get("id") != PAGE_NUMBER_ID]


def test_markdown_syntax_is_shown_as_plain_text_and_title_is_not_repeated():
    title = "예산 배분, 국내 시장 특성, 2027년 실행 로드맵"
    content = (
        f"## {title}\n"
        f"# {title}\n"
        "### **핵심** 요약\n"
        "- 매출 *12%* 성장\n"
        "* 비용은 `절감` 방향\n"
        "> 인용 문장\n"
        "---\n"
        "자세한 내용은 [보고서](https://example.com/a)\n"
    )
    svg = fallback_page_svg(content=content, role="content", index=0, count=1,
                            width=WIDTH, height=HEIGHT, style_snapshot=None)
    validate_svg(svg, width=WIDTH, height=HEIGHT)
    texts = _page_texts(svg)
    joined = " ".join(texts)
    for literal in ("#", "**", "`", "[", "](", "---"):
        assert literal not in joined
    assert [t for t in texts if title in t] == [title]  # title once, no body copy
    body = "".join(joined.split())
    for words in ("핵심요약", "매출12%성장", "비용은절감방향", "인용문장", "보고서(https://example.com/a)"):
        assert words in body
    assert any(t == "• 매출 12% 성장" for t in texts)


def test_markers_keep_numbers_and_emphasis_inside_words():
    svg = fallback_page_svg(content="제목\n- -5% 변동\n- snake_case_name 와 2*3=6", role="", index=0, count=1,
                            width=WIDTH, height=HEIGHT, style_snapshot=None)
    body = _body_words(svg)
    assert "-5% 변동" in body
    assert "snake_case_name" in body and "2*3=6" in body


def test_a_long_title_that_duplicates_the_first_body_line_is_drawn_once():
    title = "제목 " * 40 + "끝"
    svg = fallback_page_svg(content=f"## {title}\n{title}\n- 본문", role="", index=0, count=1,
                            width=WIDTH, height=HEIGHT, style_snapshot=None)
    assert "".join(_body_words(svg).split()).count("".join(title.split())) == 1


def _navy_snapshot():
    return {"tokens": {"primary": "#00A3E0", "background": "#051C2C", "body_font": "Noto Sans KR",
                       "palette": ["#051C2C", "#FFFFFF", "#2E3338", "#00A3E0", "#A7B0B7"]}}


def test_fallback_page_takes_the_deck_background_and_a_legible_text_colour():
    from services.generation_core.drivers.svg.fallback import _contrast

    svg = fallback_page_svg(content=_KOREAN_CONTENT, role="content", index=1, count=4,
                            width=1280, height=720, style_snapshot=_navy_snapshot())
    root = ET.fromstring(svg)
    background = next(node for node in root.iter() if node.tag.endswith("rect"))
    assert background.get("fill") == "#051C2C"
    fills = {node.get("fill") for node in root.iter() if node.tag.endswith("text")
             and node.get("id") != "svg-system-page-number"}
    assert fills and all(_contrast(fill, "#051C2C") >= 7 for fill in fills)
    assert "CONTENT" not in svg  # the page type is not shown as an eyebrow


def test_a_low_contrast_accent_falls_back_to_the_text_colour():
    from services.generation_core.drivers.svg.fallback import _page_colors

    assert _page_colors({"background": "#FFFFFF", "primary": "#F4F4F4"})[2] == _page_colors(
        {"background": "#FFFFFF"})[1]
    assert _page_colors({})[0] == "#FFFFFF"


def test_a_cover_fallback_is_cover_shaped_and_keeps_every_line():
    content = "# 사내 업무 자동화 도입 계획\n- 경영지원본부\n- 2026년 11월\n- 추가 1\n- 추가 2"
    svg = fallback_page_svg(content=content, role="cover", index=0, count=4, width=1280, height=720,
                            style_snapshot=_navy_snapshot())
    texts = [node for node in ET.fromstring(svg).iter() if node.tag.endswith("text")
             and node.get("id") != "svg-system-page-number"]
    joined = " ".join("".join(node.itertext()) for node in texts)
    for line in ("사내 업무 자동화 도입 계획", "경영지원본부", "2026년 11월", "추가 1", "추가 2"):
        assert line in joined
    title = next(node for node in texts if "자동화" in "".join(node.itertext()))
    assert title.get("font-size") == "44" and int(title.get("y")) > 200
