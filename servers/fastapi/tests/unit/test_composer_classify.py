"""Deterministic classifier: manifest unit -> ComposerSlidePlan."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from services.composer.classify import classify_unit
from services.composer.contracts import Classifier, DeckContext
from services.composer.plan import ComposerComponent, ComposerSlidePlan, Paragraph
from services.source_content.source_runs import parse_source_binding_id, project_source_runs

PAGE_EMU = (12192000, 6858000)  # 16:9 at 13.333 in
PAGE_W, PAGE_H = PAGE_EMU
UNIT = "slide-1"


def geo(x: float, y: float, cx: float, cy: float) -> dict[str, int]:
    """Rectangle in page shares -> EMU."""

    return {"x": int(x * PAGE_W), "y": int(y * PAGE_H), "cx": int(cx * PAGE_W), "cy": int(cy * PAGE_H)}


def text_block(
    number: int,
    text: str,
    order: int,
    *,
    geometry: Mapping[str, int] | None = None,
    role: str = "body",
    bullets: bool = False,
    levels: list[int] | None = None,
    bullet_kind: str = "character",
    **enrichment: Any,
) -> dict[str, Any]:
    """A stored-manifest text block ``slide-1:text:<number>``; ``enrichment`` adds workstream-A fields."""

    lines = text.split("\n")
    paragraphs = [
        {
            "runs": [line],
            "text": line,
            "bullet": bullets,
            "bulletKind": bullet_kind if bullets else None,
            "level": (levels or [0] * len(lines))[index],
        }
        for index, line in enumerate(lines)
    ]
    runs: list[Any] = []
    for index, paragraph in enumerate(paragraphs):
        runs.extend(paragraph["runs"])
        if index + 1 < len(paragraphs):
            runs.append("\n")
    block_id = f"{UNIT}:text:{number}"
    payload: dict[str, Any] = {"text": text, "paragraphs": paragraphs, "runs": runs, "shapeName": block_id}
    if geometry is not None:
        payload["geometry"] = dict(geometry)
    payload.update(enrichment)
    return {"source_block_id": block_id, "kind": "text", "semantic_role": role, "order": order, "payload": payload}


def image_block(number: int, order: int, geometry: Mapping[str, int], asset_id: str = "scma_1") -> dict[str, Any]:
    payload = {"assetId": asset_id, "contentDigest": "sha256:0", "sizeBytes": 1, "occurrence": 1}
    payload["geometry"] = dict(geometry)
    return {
        "source_block_id": f"{UNIT}:image:{number}",
        "kind": "image",
        "semantic_role": "image",
        "order": order,
        "payload": payload,
    }


def shape_block(
    number: int, order: int, geometry: Mapping[str, int], *, preset: str | None, role: str = "shape"
) -> dict[str, Any]:
    payload = {"shapeName": f"shape {number}", "preset": preset, "geometry": dict(geometry)}
    block_id = f"{UNIT}:shape:{number}"
    return {"source_block_id": block_id, "kind": "shape", "semantic_role": role, "order": order, "payload": payload}


def table_block(number: int, order: int, cells: list[list[str]], merged: list | None = None) -> dict[str, Any]:
    rows = [
        [{"row": r, "column": c, "text": text, "paragraphs": []} for c, text in enumerate(row)]
        for r, row in enumerate(cells)
    ]
    payload = {"rows": rows, "cells": cells, "mergedCells": merged or [], "geometry": geo(0.05, 0.3, 0.9, 0.4)}
    block_id = f"{UNIT}:table:{number}"
    return {"source_block_id": block_id, "kind": "table", "semantic_role": "table", "order": order, "payload": payload}


def unit(*blocks: Mapping[str, Any]) -> dict[str, Any]:
    return {"source_unit_id": UNIT, "source_index": 0, "blocks": list(blocks)}


def context(**overrides: Any) -> dict[str, Any]:
    base = DeckContext(page_count=5, repeated_texts=frozenset(), source_page_emu=PAGE_EMU).as_mapping()
    base.update(overrides)
    return base


def plan_for(*blocks: Mapping[str, Any], **overrides: Any) -> ComposerSlidePlan:
    return classify_unit(unit=unit(*blocks), slide_index=0, deck_context=context(**overrides))


def kinds_of(plan: ComposerSlidePlan) -> list[str]:
    return [component.kind for component in plan.components]


def furniture_of(plan: ComposerSlidePlan) -> list[str | None]:
    return [component.furniture_kind for component in plan.furniture]


def all_paragraphs(component: ComposerComponent) -> list[Paragraph]:
    found = list(component.paragraphs)
    found.extend(paragraph for group in component.groups for paragraph in group)
    found.extend(header for header in component.group_headers if header is not None)
    found.extend(cell for row in component.table_rows for cell in row)
    return found


def bindings_by_block(plan: ComposerSlidePlan) -> dict[str, list[tuple[int | None, int | None]]]:
    spans: dict[str, list[tuple[int | None, int | None]]] = {}
    for component in [plan.title, plan.subtitle, *plan.components, *plan.furniture]:
        if component is None:
            continue
        for paragraph in all_paragraphs(component):
            block_id, start, end = parse_source_binding_id(paragraph.source.binding_id)
            assert block_id == paragraph.source.block_id
            assert (start, end) == (paragraph.source.run_start, paragraph.source.run_end)
            spans.setdefault(block_id, []).append((start, end))
    return spans


TITLE = text_block(1, "2026 사업 계획", 0, geometry=geo(0.05, 0.05, 0.9, 0.12), role="title")
BULLETS = "• 프로필 사진\n• 한 줄 소개\n• 링크 정리\n• 태그 선택\n• 게시"
BODY_BOX = geo(0.05, 0.3, 0.9, 0.3)


# --- Rule 1: bindings partition exactly -------------------------------------------


def test_multi_run_block_binds_every_run_exactly_once() -> None:
    plan = plan_for(TITLE, text_block(2, BULLETS, 1, geometry=geo(0.05, 0.25, 0.9, 0.6)))
    projection = project_source_runs(BULLETS)
    assert projection.run_count == 5
    (component,) = plan.components
    assert component.kind == "bullets"
    assert sorted(bindings_by_block(plan)["slide-1:text:2"]) == [(index, index) for index in range(5)]
    assert [paragraph.source.binding_id for paragraph in component.paragraphs] == [
        f"slide-1:text:2:run:{index}" for index in range(5)
    ]
    assert [paragraph.text for paragraph in component.paragraphs] == [projection.span_text(i, i) for i in range(5)]
    assert all(paragraph.bullet for paragraph in component.paragraphs)


def test_single_paragraph_block_binds_as_the_whole_block() -> None:
    plan = plan_for(TITLE, text_block(2, "하나의 문장으로 된 본문입니다.", 1, geometry=BODY_BOX))
    assert plan.title is not None
    (title_paragraph,) = plan.title.paragraphs
    assert title_paragraph.source.binding_id == "slide-1:text:1"
    assert parse_source_binding_id(title_paragraph.source.binding_id) == ("slide-1:text:1", None, None)
    (body,) = plan.components
    assert body.kind == "paragraph" and body.paragraphs[0].source.binding_id == "slide-1:text:2"


def test_whitespace_gap_on_one_line_stays_one_binding_but_arrows_split() -> None:
    plan = plan_for(
        TITLE,
        text_block(2, "Day 1  업체 10곳 찾기", 1, geometry=geo(0.05, 0.3, 0.9, 0.1)),
        text_block(3, "영업 → 상담 → 제안", 2, geometry=geo(0.05, 0.5, 0.9, 0.1)),
    )
    spans = bindings_by_block(plan)
    assert spans["slide-1:text:2"] == [(None, None)]
    assert sorted(spans["slide-1:text:3"]) == [(0, 0), (1, 1), (2, 2)]


def test_text_runs_join_back_to_the_paragraph_and_carry_run_styles() -> None:
    runs = ["핵심 ", "메시지 → 다음 ", "단계"]
    styles = [
        {"sizePt": 20, "bold": True, "italic": False, "color": None},
        {"sizePt": 20, "bold": False, "italic": False, "color": None},
        {"sizePt": 20, "bold": True, "italic": False, "color": "#FF0000"},
    ]
    block = text_block(2, "핵심 메시지 → 다음 단계", 1, geometry=geo(0.05, 0.3, 0.9, 0.1), runs=runs, runStyles=styles)
    plan = plan_for(TITLE, block)
    (process,) = plan.components
    assert process.kind == "process"
    steps = [group[0] for group in process.groups]
    assert [step.text for step in steps] == ["핵심 메시지", "다음 단계"]
    for step in steps:
        assert "".join(run.text for run in step.runs) == step.text
    assert [(run.text, run.bold) for run in steps[0].runs] == [("핵심 ", True), ("메시지", False)]
    assert [(run.text, run.bold, run.color) for run in steps[1].runs] == [
        ("다음 ", False, None),
        ("단계", True, "#FF0000"),
    ]
    assert process.source_size_pt == 20


# --- Rule 2: furniture -------------------------------------------------------------


@pytest.mark.parametrize(("placeholder", "expected"), [("sldNum", "page_number"), ("ftr", "footer"), ("dt", "date")])
def test_furniture_by_placeholder_type(placeholder: str, expected: str) -> None:
    block = text_block(9, "회사 소개서 2026", 5, geometry=geo(0.4, 0.5, 0.2, 0.05), placeholderType=placeholder)
    plan = plan_for(TITLE, block)
    assert plan.components == []
    (furniture,) = plan.furniture
    assert (furniture.kind, furniture.semantic_role, furniture.furniture_kind) == ("furniture", "furniture", expected)


def test_furniture_by_legacy_footer_role_without_enrichment() -> None:
    plan = plan_for(TITLE, text_block(9, "Company Confidential Footer", 5, role="source_note"))
    assert furniture_of(plan) == ["footer"]


@pytest.mark.parametrize("text", ["3 / 12", "12 of 43", "p. 7", "Page 3", "7 페이지"])
def test_furniture_by_page_number_pattern_anywhere(text: str) -> None:
    plan = plan_for(TITLE, text_block(9, text, 5, geometry=geo(0.4, 0.5, 0.2, 0.05)))
    assert furniture_of(plan) == ["page_number"]


def test_bare_number_is_a_page_number_only_at_the_edge_or_when_small() -> None:
    edge = plan_for(TITLE, text_block(9, "- 7 -", 5, geometry=geo(0.45, 0.94, 0.1, 0.04)))
    assert furniture_of(edge) == ["page_number"]
    kpi = plan_for(TITLE, text_block(9, "45", 5, geometry=geo(0.4, 0.4, 0.2, 0.2), dominantSizePt=72))
    assert kpi.furniture == [] and kinds_of(kpi) == ["paragraph"]
    unknown = plan_for(TITLE, text_block(9, "7", 5))
    assert furniture_of(unknown) == ["page_number"]


def test_furniture_by_repeated_text_is_header_or_footer_by_position() -> None:
    header = text_block(8, "㈜ 아이비즈  |  사업 계획", 4, geometry=geo(0.05, 0.01, 0.5, 0.05))
    footer = text_block(9, "㈜ 아이비즈  |  사업 계획", 5, geometry=geo(0.05, 0.7, 0.5, 0.05))
    plan = plan_for(TITLE, header, footer, repeated_texts=["㈜ 아이비즈 | 사업 계획"])
    assert furniture_of(plan) == ["running_header", "footer"]


def test_furniture_by_geometry_needs_short_text_in_the_bottom_band() -> None:
    short = text_block(8, "www.ibizsoftware.net", 4, geometry=geo(0.05, 0.94, 0.3, 0.04))
    long_text = (
        "이 문장은 페이지 하단에 있지만 육십 자를 넘는 본문이라 "
        "가구가 아니라 내용으로 남아야 하는 긴 문장입니다. 계속됩니다."
    )
    plan = plan_for(TITLE, short, text_block(9, long_text, 5, geometry=geo(0.05, 0.93, 0.9, 0.06)))
    assert furniture_of(plan) == ["footer"]
    assert [component.component_id for component in plan.components] == ["slide-1:text:9"]


@pytest.mark.parametrize("text", ["CONFIDENTIAL", "Confidential", "[대외비]", "기밀", "Strictly Confidential"])
def test_furniture_confidential_marks(text: str) -> None:
    plan = plan_for(TITLE, text_block(9, text, 5, geometry=geo(0.8, 0.02, 0.15, 0.05)))
    assert furniture_of(plan) == ["confidential"]


# --- Rule 3: title and subtitle ------------------------------------------------------


def test_title_and_subtitle_by_placeholder_type() -> None:
    title = text_block(1, "제목 텍스트", 1, geometry=geo(0.05, 0.4, 0.9, 0.1), placeholderType="ctrTitle")
    subtitle = text_block(2, "부제 텍스트", 2, geometry=geo(0.05, 0.52, 0.9, 0.08), placeholderType="subTitle")
    body = text_block(3, "가장 위에 있는 본문 문장입니다.", 0, geometry=geo(0.05, 0.05, 0.9, 0.1))
    plan = plan_for(body, title, subtitle)
    assert plan.title is not None and plan.title.component_id == "slide-1:text:1"
    assert plan.title.semantic_role == "title" and plan.title.kind == "title"
    assert plan.subtitle is not None and plan.subtitle.component_id == "slide-1:text:2"
    assert [component.component_id for component in plan.components] == ["slide-1:text:3"]


def test_title_by_largest_size_in_the_upper_page_and_subtitle_directly_below() -> None:
    heading = text_block(1, "시장 현황", 0, geometry=geo(0.05, 0.08, 0.9, 0.1), dominantSizePt=32)
    sub = text_block(2, "2026년 상반기 기준", 1, geometry=geo(0.05, 0.19, 0.9, 0.06), dominantSizePt=20)
    body = text_block(3, "본문 첫 단락입니다.", 2, geometry=geo(0.05, 0.35, 0.9, 0.2), dominantSizePt=16)
    big_low = text_block(4, "48%", 3, geometry=geo(0.05, 0.6, 0.3, 0.2), dominantSizePt=60)
    plan = plan_for(body, big_low, heading, sub)
    assert plan.title is not None and plan.title.component_id == "slide-1:text:1"
    assert plan.title.source_size_pt == 32
    assert plan.subtitle is not None and plan.subtitle.component_id == "slide-1:text:2"
    assert {component.component_id for component in plan.components} == {"slide-1:text:3", "slide-1:text:4"}


def test_title_falls_back_to_the_first_short_block_without_sizes() -> None:
    plan = plan_for(text_block(1, "고객 여정", 0), text_block(2, "인지 → 고려 → 구매", 1))
    assert plan.title is not None and plan.title.component_id == "slide-1:text:1"
    assert plan.subtitle is None
    assert kinds_of(plan) == ["process"]


def test_no_fallback_title_when_the_first_block_is_multi_line() -> None:
    plan = plan_for(text_block(1, "첫 줄\n둘째 줄", 0), text_block(2, "본문", 1))
    assert plan.title is None
    assert kinds_of(plan) == ["paragraph", "paragraph"]


def test_large_wordless_number_is_not_a_title_and_becomes_a_kpi_component() -> None:
    """A bare '2026' at the top of the page, however large, carries no words."""

    year = text_block(1, "2026", 0, geometry=geo(0.3, 0.05, 0.3, 0.15), dominantSizePt=80)
    body = text_block(2, "연간 사업 계획을 안내드립니다.", 1, geometry=geo(0.05, 0.3, 0.9, 0.2), dominantSizePt=18)
    plan = plan_for(year, body)
    assert plan.title is not None and plan.title.component_id == "slide-1:text:2"
    kpi_components = [component for component in plan.components if component.kind == "kpi"]
    assert [component.component_id for component in kpi_components] == ["slide-1:text:1"]
    assert kpi_components[0].semantic_role == "body" and kpi_components[0].emphasis == "accent"


def test_kpi_callouts_are_excluded_from_title_and_the_cover_still_classifies() -> None:
    """The reported defect: three KPI numbers must not out-rank the real title."""

    proposal_title = text_block(
        1,
        "AI 자율개발 플랫폼 기반 SI 프로젝트 구축 제안",
        0,
        geometry=geo(0.05, 0.08, 0.65, 0.08),
        dominantSizePt=22,
    )
    company = text_block(2, "㈜아이비즈소프트웨어", 1, geometry=geo(0.05, 0.17, 0.3, 0.05), dominantSizePt=14)
    kpi_percent = text_block(3, "52%", 2, geometry=geo(0.645, 0.08, 0.28, 0.08), dominantSizePt=44)
    kpi_weeks_a = text_block(4, "82주", 3, geometry=geo(0.645, 0.20, 0.28, 0.08), dominantSizePt=44)
    kpi_weeks_b = text_block(5, "39주", 4, geometry=geo(0.645, 0.30, 0.28, 0.08), dominantSizePt=44)
    plan = plan_for(proposal_title, company, kpi_percent, kpi_weeks_a, kpi_weeks_b)
    assert plan.title is not None
    assert plan.title.paragraphs[0].text == "AI 자율개발 플랫폼 기반 SI 프로젝트 구축 제안"
    assert plan.subtitle is not None and plan.subtitle.paragraphs[0].text == "㈜아이비즈소프트웨어"
    kpi_components = [component for component in plan.components if component.kind == "kpi"]
    assert {component.paragraphs[0].text for component in kpi_components} == {"52%", "82주", "39주"}
    assert all(component.semantic_role == "body" and component.emphasis == "accent" for component in kpi_components)
    assert plan.archetype == "cover"


def test_signed_percent_kpi_is_not_a_title_candidate() -> None:
    delta = text_block(1, "+18%", 0, geometry=geo(0.3, 0.05, 0.2, 0.1), dominantSizePt=50)
    body = text_block(2, "전년 대비 매출 성장 현황 보고", 1, geometry=geo(0.05, 0.25, 0.9, 0.1), dominantSizePt=20)
    plan = plan_for(delta, body)
    assert plan.title is not None and plan.title.component_id == "slide-1:text:2"
    kpi = next(component for component in plan.components if component.component_id == "slide-1:text:1")
    assert kpi.kind == "kpi"


def test_prefers_the_wider_title_over_a_narrow_larger_callout() -> None:
    narrow_big = text_block(1, "혁신 성장", 0, geometry=geo(0.60, 0.05, 0.20, 0.10), dominantSizePt=40)
    wide_real_title = text_block(
        2, "지속가능한 성장을 위한 디지털 전환 전략 제안", 1, geometry=geo(0.05, 0.20, 0.80, 0.10), dominantSizePt=34
    )
    plan = plan_for(narrow_big, wide_real_title)
    assert plan.title is not None and plan.title.component_id == "slide-1:text:2"


# --- Rule 4: comparison ----------------------------------------------------------------


def test_comparison_from_two_columns_with_headers() -> None:
    bold = [{"sizePt": 20, "bold": True, "italic": False, "color": None}]
    left_head = text_block(2, "기존 방식", 1, geometry=geo(0.05, 0.25, 0.42, 0.08), dominantSizePt=20, runStyles=bold)
    left_body = text_block(3, "수작업 취합\n오류 잦음", 2, geometry=geo(0.05, 0.35, 0.42, 0.4), dominantSizePt=16)
    right_head = text_block(4, "새 방식", 3, geometry=geo(0.53, 0.25, 0.42, 0.08), dominantSizePt=20, runStyles=bold)
    right_body = text_block(5, "자동 수집\n검증 내장", 4, geometry=geo(0.53, 0.35, 0.42, 0.4), dominantSizePt=16)
    plan = plan_for(TITLE, right_body, right_head, left_body, left_head)
    assert plan.subtitle is None
    (comparison,) = plan.components
    assert comparison.kind == "comparison" and plan.archetype == "comparison"
    assert [header.text for header in comparison.group_headers if header] == ["기존 방식", "새 방식"]
    assert [[paragraph.text for paragraph in group] for group in comparison.groups] == [
        ["수작업 취합", "오류 잦음"],
        ["자동 수집", "검증 내장"],
    ]
    spans = bindings_by_block(plan)
    assert spans["slide-1:text:2"] == [(None, None)] and sorted(spans["slide-1:text:3"]) == [(0, 0), (1, 1)]
    assert comparison.source_rect is not None and comparison.source_rect.x == pytest.approx(0.05)


def test_two_columns_of_unequal_height_are_not_a_comparison() -> None:
    label = text_block(2, "요약", 1, geometry=geo(0.05, 0.25, 0.2, 0.06))
    body = text_block(3, "오른쪽 본문 첫 단락입니다.\n둘째 단락입니다.", 2, geometry=geo(0.3, 0.25, 0.4, 0.6))
    assert kinds_of(plan_for(TITLE, label, body)) == ["paragraph", "paragraph"]


def test_four_columns_are_not_a_comparison() -> None:
    columns = [
        text_block(index + 2, f"항목 {index + 1}", index + 1, geometry=geo(0.05 + 0.23 * index, 0.3, 0.2, 0.3))
        for index in range(4)
    ]
    assert kinds_of(plan_for(TITLE, *columns)) == ["paragraph"] * 4


# --- Rule 5: process -----------------------------------------------------------------------


def test_process_from_arrow_separated_runs_and_vertical_arrow_lines() -> None:
    flow = text_block(2, "영업 → 상담 → 제안", 1, geometry=geo(0.05, 0.3, 0.9, 0.1))
    vertical = text_block(3, "문제 제시\n↓\n해결·정보", 2, geometry=geo(0.05, 0.5, 0.9, 0.3))
    plan = plan_for(TITLE, flow, vertical)
    assert kinds_of(plan) == ["process", "process"]
    first, second = plan.components
    assert [group[0].text for group in first.groups] == ["영업", "상담", "제안"]
    assert [group[0].source.binding_id for group in first.groups] == [f"slide-1:text:2:run:{i}" for i in range(3)]
    assert [group[0].text for group in second.groups] == ["문제 제시", "해결·정보"]
    assert plan.archetype == "process"


def test_process_from_ordinal_blocks_attaches_descriptions_below() -> None:
    steps = [
        text_block(2, "① 조사", 1, geometry=geo(0.05, 0.3, 0.25, 0.08)),
        text_block(4, "② 설계", 3, geometry=geo(0.37, 0.3, 0.25, 0.08)),
        text_block(6, "③ 실행", 5, geometry=geo(0.69, 0.3, 0.25, 0.08)),
    ]
    details = [
        text_block(3, "고객 인터뷰 20건", 2, geometry=geo(0.05, 0.4, 0.25, 0.2)),
        text_block(5, "프로토타입 2종", 4, geometry=geo(0.37, 0.4, 0.25, 0.2)),
        text_block(7, "파일럿 운영", 6, geometry=geo(0.69, 0.4, 0.25, 0.2)),
    ]
    plan = plan_for(TITLE, *details, *steps)
    (process,) = plan.components
    assert process.kind == "process"
    assert [[paragraph.text for paragraph in group] for group in process.groups] == [
        ["① 조사", "고객 인터뷰 20건"],
        ["② 설계", "프로토타입 2종"],
        ["③ 실행", "파일럿 운영"],
    ]


def test_process_from_arrow_shapes_between_aligned_blocks() -> None:
    blocks = [
        text_block(2, "입력", 1, geometry=geo(0.05, 0.4, 0.2, 0.15)),
        text_block(4, "처리", 3, geometry=geo(0.4, 0.4, 0.2, 0.15)),
        text_block(6, "출력", 5, geometry=geo(0.75, 0.4, 0.2, 0.15)),
    ]
    arrows = [
        shape_block(3, 2, geo(0.26, 0.45, 0.13, 0.05), preset="rightArrow"),
        shape_block(5, 4, geo(0.61, 0.47, 0.13, 0.0), preset=None, role="connector"),
    ]
    plan = plan_for(TITLE, *arrows, *blocks)
    (process,) = plan.components
    assert process.kind == "process"
    assert [group[0].text for group in process.groups] == ["입력", "처리", "출력"]
    # Three equal columns without arrows read as a comparison, not a flow.
    assert kinds_of(plan_for(TITLE, *blocks)) == ["comparison"]


# --- Rule 6: lists -------------------------------------------------------------------------


def test_list_from_bullet_paragraphs_keeps_levels() -> None:
    block = text_block(2, "첫 항목\n하위 항목\n둘째 항목", 1, geometry=BODY_BOX, bullets=True, levels=[0, 1, 0])
    plan = plan_for(TITLE, block)
    (bullets,) = plan.components
    assert bullets.kind == "bullets" and plan.archetype == "list"
    assert [(paragraph.bullet, paragraph.level, paragraph.bullet_kind) for paragraph in bullets.paragraphs] == [
        (True, 0, "character"),
        (True, 1, "character"),
        (True, 0, "character"),
    ]


def test_numbered_list_from_enumerator_markers_and_auto_number() -> None:
    markers = plan_for(TITLE, text_block(2, "1. 준비\n2. 실행\n3. 점검", 1, geometry=BODY_BOX)).components[0]
    assert markers.kind == "numbered"
    assert [paragraph.bullet_kind for paragraph in markers.paragraphs] == ["enumerator"] * 3
    auto = text_block(3, "계획\n실행", 2, geometry=BODY_BOX, bullets=True, bullet_kind="auto-number")
    assert kinds_of(plan_for(TITLE, auto)) == ["numbered"]


# --- Rules 7-8: tables, charts, images ----------------------------------------------------------


def test_table_cells_all_bind_to_the_table_block_as_a_whole() -> None:
    merged = [{"row": 0, "column": 0, "rowSpan": 1, "columnSpan": 2}]
    plan = plan_for(TITLE, table_block(2, 1, [["항목", "값"], ["", "A  →  B"], ["합계", "3"]], merged=merged))
    (component,) = plan.components
    assert component.kind == "table" and plan.archetype == "table"
    texts = [[cell.text for cell in row] for row in component.table_rows]
    assert texts == [["항목", "값"], ["", "A → B"], ["합계", "3"]]
    cells = [cell for row in component.table_rows for cell in row]
    assert {(cell.source.block_id, cell.source.run_start, cell.source.run_end) for cell in cells} == {
        ("slide-1:table:2", None, None)
    }
    assert {cell.source.binding_id for cell in cells} == {"slide-1:table:2"}
    assert {parse_source_binding_id(cell.source.binding_id) for cell in cells} == {("slide-1:table:2", None, None)}
    assert all("".join(run.text for run in cell.runs) == cell.text for cell in cells)
    assert plan.text_chars == len("2026 사업 계획") + sum(len(cell.text) for cell in cells)
    assert any("merged cells" in note for note in plan.notes)


def test_chart_emits_no_component_but_a_note() -> None:
    payload = {"chartType": "bar", "series": []}
    chart = {"source_block_id": "slide-1:chart:2", "kind": "chart", "semantic_role": "chart", "order": 1}
    chart["payload"] = payload
    plan = plan_for(TITLE, chart)
    assert plan.components == []
    assert any("slide-1:chart:2" in note for note in plan.notes)


def test_image_component_fields_and_caption_below_it() -> None:
    image = image_block(2, 1, geo(0.1, 0.2, 0.4, 0.5), asset_id="scma_deadbeef")
    caption = text_block(3, "그림 1. 매출 추이", 2, geometry=geo(0.1, 0.72, 0.4, 0.05))
    plan = plan_for(TITLE, image, caption)
    (picture,) = plan.images
    assert (picture.kind, picture.semantic_role) == ("image", "image")
    assert (picture.asset_id, picture.occurrence_id) == ("scma_deadbeef", "slide-1:image:2")
    assert picture.aspect_ratio == pytest.approx((0.4 * PAGE_W) / (0.5 * PAGE_H), rel=1e-3)
    assert picture.source_rect is not None
    assert (picture.source_rect.x, picture.source_rect.y) == pytest.approx((0.1, 0.2), rel=1e-6)
    assert (picture.source_rect.width, picture.source_rect.height) == pytest.approx((0.4, 0.5), rel=1e-6)
    (caption_component,) = plan.components
    assert (caption_component.kind, caption_component.semantic_role) == ("caption", "caption")


# --- Rule 10: geometry fallback, archetypes, density -------------------------------------------------


def test_stored_manifest_without_enrichment_normalizes_by_unit_extents() -> None:
    blocks = [
        text_block(1, "개요", 0, geometry={"x": 0, "y": 0, "cx": 6000000, "cy": 500000}),
        text_block(2, "본문 한 단락", 1, geometry={"x": 0, "y": 1000000, "cx": 6000000, "cy": 2000000}),
        image_block(3, 2, {"x": 6000000, "y": 0, "cx": 6000000, "cy": 3000000}),
    ]
    plan = classify_unit(unit=unit(*blocks), slide_index=3, deck_context={"page_count": 1, "repeated_texts": []})
    assert plan.slide_index == 3 and plan.title is not None and plan.title.component_id == "slide-1:text:1"
    assert plan.title.source_size_pt is None
    (body,) = plan.components
    assert body.source_rect is not None
    assert (body.source_rect.width, body.source_rect.height) == pytest.approx((0.5, 2 / 3), rel=1e-6)
    assert plan.images[0].source_rect is not None and plan.images[0].source_rect.x == pytest.approx(0.5)
    assert plan.furniture == []
    assert plan.text_chars == len("개요") + len("본문 한 단락")


def test_blocks_without_any_geometry_have_no_source_rect_but_still_bind() -> None:
    plan = plan_for(text_block(1, "제목", 0), text_block(2, "본문", 1))
    assert plan.title is not None and plan.title.source_rect is None
    assert plan.components[0].source_rect is None
    assert bindings_by_block(plan) == {"slide-1:text:1": [(None, None)], "slide-1:text:2": [(None, None)]}


def test_archetype_selection() -> None:
    footer = text_block(9, "3 / 12", 9, geometry=geo(0.9, 0.95, 0.08, 0.04))
    only_image = plan_for(image_block(1, 0, geo(0.1, 0.1, 0.8, 0.8)), footer)
    assert only_image.archetype == "image_only" and len(only_image.furniture) == 1
    grid = plan_for(TITLE, *[image_block(i, i, geo(0.05 + 0.3 * (i - 1), 0.3, 0.28, 0.4)) for i in (1, 2, 3)])
    assert grid.archetype == "image_grid"
    assert plan_for(TITLE, text_block(2, "본문 한 단락", 1, geometry=BODY_BOX)).archetype == "text"
    picture = image_block(3, 2, geo(0.5, 0.3, 0.4, 0.4))
    assert plan_for(TITLE, text_block(2, "본문", 1, geometry=BODY_BOX), picture).archetype == "mixed"
    closing = text_block(3, "맺음말", 2, geometry=geo(0.05, 0.85, 0.9, 0.05))
    assert plan_for(TITLE, text_block(2, BULLETS, 1, geometry=geo(0.05, 0.3, 0.9, 0.5)), closing).archetype == "list"
    empty = plan_for()
    assert empty.archetype == "text" and empty.components == [] and empty.text_chars == 0


_COVER_TITLE_TEXT = "숏폼대행\n1인사업자 홀로서기\n4주 · 총 8시간 · 온라인 실전 창업 과정"


def test_cover_page_with_title_label_tagline_and_furniture() -> None:
    title = text_block(1, _COVER_TITLE_TEXT, 0, geometry=geo(0.08, 0.32, 0.84, 0.24), role="title")
    label = text_block(2, "🎯 실전 크리에이터 과정", 1, geometry=geo(0.08, 0.18, 0.3, 0.06))
    tagline = text_block(3, "지금 바로 시작하는 나만의 콘텐츠 사업", 2, geometry=geo(0.08, 0.6, 0.5, 0.06))
    footer = text_block(4, "㈜ 아이비즈 | 숏폼대행", 3, role="source_note")
    page_number = text_block(5, "1 / 35", 4, geometry=geo(0.92, 0.95, 0.06, 0.04))
    plan = plan_for(title, label, tagline, footer, page_number)
    assert plan.title is not None and len(plan.title.paragraphs) == 3
    assert kinds_of(plan) == ["paragraph", "paragraph"]
    assert furniture_of(plan) == ["footer", "page_number"]
    assert plan.archetype == "cover"


def test_same_title_with_a_five_item_bullet_block_is_not_a_cover() -> None:
    title = text_block(1, _COVER_TITLE_TEXT, 0, geometry=geo(0.08, 0.05, 0.84, 0.14), role="title")
    bullets = "\n".join(f"• 항목 {i}" for i in range(1, 6))
    plan = plan_for(title, text_block(2, bullets, 1, geometry=geo(0.08, 0.3, 0.84, 0.5)))
    assert plan.title is not None and plan.components[0].kind == "bullets"
    assert plan.archetype != "cover"


def test_supporting_role_for_small_body_text_and_text_chars_total() -> None:
    body = text_block(2, "본문 단락입니다.", 1, geometry=BODY_BOX, dominantSizePt=18)
    body_two = text_block(3, "둘째 본문 단락.", 2, geometry=geo(0.05, 0.62, 0.9, 0.2), dominantSizePt=18)
    note = text_block(4, "* 출처: 내부 자료", 3, geometry=geo(0.05, 0.84, 0.9, 0.05), dominantSizePt=10)
    plan = plan_for(TITLE, body, body_two, note)
    roles = {component.component_id: component.semantic_role for component in plan.components}
    assert roles == {"slide-1:text:2": "body", "slide-1:text:3": "body", "slide-1:text:4": "supporting"}
    components = [component for component in [plan.title, *plan.components] if component is not None]
    assert plan.text_chars == sum(len(paragraph.text) for component in components for paragraph in component.paragraphs)


# --- Contract --------------------------------------------------------------------------------------


def test_classify_unit_is_a_classifier_and_is_deterministic() -> None:
    classifier: Classifier = classify_unit
    blocks = (TITLE, text_block(2, BULLETS, 1, geometry=geo(0.05, 0.25, 0.9, 0.6)))
    first = classifier(unit=unit(*blocks), slide_index=0, deck_context=context())
    second = classifier(unit=unit(*blocks), slide_index=0, deck_context=context())
    assert first.model_dump() == second.model_dump()
    assert ComposerSlidePlan.model_validate(first.model_dump()) == first


def test_camel_cased_manifest_keys_are_accepted() -> None:
    block = {"sourceBlockId": "slide-1:text:1", "kind": "text", "semanticRole": "title", "order": 0}
    block["payload"] = {"text": "제목"}
    camel_unit = {"sourceUnitId": UNIT, "sourceIndex": 0, "blocks": [block]}
    plan = classify_unit(unit=camel_unit, slide_index=0, deck_context=context())
    assert plan.source_unit_id == UNIT and plan.title is not None and plan.title.component_id == "slide-1:text:1"


def test_missing_unit_identity_is_an_error() -> None:
    with pytest.raises(ValueError):
        classify_unit(unit={"blocks": []}, slide_index=0, deck_context=context())
