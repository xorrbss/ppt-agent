"""Focused acceptance for the canonical authored-vNext design compiler."""

from __future__ import annotations

import json
import re

import pytest

from models.authored_vnext.deck.scene import (
    SceneChartElement,
    SceneImageElement,
    SceneShapeElement,
    SceneTableElement,
    SceneTextElement,
    scene_digest,
)
from models.authored_vnext.deck.visual_plan import SlideVisualPlan
from models.authored_vnext.master.contract import DeckMasterContract, Rect
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.generation.outline import OutlineItem, plan_outline
from services.authored_vnext.generation.scene_compiler import (
    compile_deck_scene,
    compile_slide_scene,
)
from services.authored_vnext.master.digests import canonical_digest, seal_master_contract
from tests.support.authored_vnext_master_fixture import master_contract_payload


SOURCE_DIGEST = "sha256:" + "7" * 64
_VISIBLE_DELIMITER = re.compile(r"(?:\s+·\s+|\s*[|→➜▶▸]+\s*)")


def _semantic_copy(value: str) -> str:
    return " ".join(_VISIBLE_DELIMITER.sub(" ", value).split())


def _contract(*, body_grammar: bool = False) -> DeckMasterContract:
    payload = master_contract_payload()
    if body_grammar:
        payload["semanticElements"] = [
            {
                "id": "element:authored-body-grammar-v2",
                "type": "text",
                "scope": {"kind": "common"},
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                "rotation": 0,
                "zIndex": -100000,
                "visibility": "hidden",
                "editableByUser": False,
                "editableByAI": False,
                "locked": True,
                "style": {},
                "content": {
                    "role": "authored-body-grammar:osb-smart-banking:v2"
                },
            }
        ]
    return seal_master_contract(DeckMasterContract.model_validate(payload))


def _company_variant_contract() -> DeckMasterContract:
    payload = master_contract_payload()
    payload["masterId"] = "master:company-variant"
    payload["canvas"]["safeArea"] = {
        "x": 120,
        "y": 40,
        "width": 1030,
        "height": 620,
    }
    payload["tokens"]["fonts"] = {
        "latin": "Arial",
        "eastAsian": "Arial",
        "complexScript": "Arial",
        "fallbacks": ["Arial"],
    }
    payload["tokens"]["colors"] = {
        "background": "#101827",
        "surface": "#182337",
        "textPrimary": "#F4F7FB",
        "textSecondary": "#D5DDEA",
        "accent": "#F4C95D",
        "divider": "#39465B",
        "warning": "#F28C8C",
    }
    payload["fontManifest"].update(
        {
            "primary": "Arial",
            "fallbacks": ["Arial"],
            "windowsFallback": "Arial",
            "themeScripts": {
                "latin": "Arial",
                "hang": "Arial",
                "jpan": "Arial",
                "hans": "Arial",
                "hant": "Arial",
            },
            "files": [],
        }
    )
    payload["furniture"]["header"]["text"] = "COMPANY B"
    payload["furniture"]["header"]["fill"] = "#F4C95D"
    payload["furniture"]["footer"]["text"] = "CONFIDENTIAL"
    for layout in payload["layouts"].values():
        for slot in layout["slots"]:
            slot["bounds"]["x"] += 18
            slot["bounds"]["width"] -= 36
    return seal_master_contract(DeckMasterContract.model_validate(payload))


def _slide_and_plan(
    contract: DeckMasterContract,
    *,
    title: str,
    body: str,
    slide_id: str,
) -> tuple[SlideSpecV2, SlideVisualPlan, Rect]:
    published = MasterRevisionResponse(
        master_id=contract.master_id,
        revision=1,
        digest=contract.digest,
        status="published",
        contract=contract,
    )
    planned = plan_outline(
        published,
        [
            OutlineItem("slide:cover", "품질 검증", "대표 deck"),
            OutlineItem(slide_id, title, body),
        ],
    ).slides[1]
    content_digest = canonical_digest(
        {
            "title": planned.title.model_dump(mode="json", by_alias=True),
            "body": body,
        }
    )
    slide_payload = {
        "schemaVersion": "slidespec/v2",
        "slideId": slide_id,
        "revision": 1,
        "layoutId": planned.layout_id,
        "title": planned.title.model_dump(mode="json", by_alias=True),
        "body": body,
        "ownership": {"locked": False, "modelWritable": True},
        "masterDigest": contract.digest,
        "titleFit": planned.title_fit.model_dump(mode="json", by_alias=True),
        "contentDigest": content_digest,
    }
    slide = SlideSpecV2(
        **slide_payload,
        specDigest=canonical_digest(slide_payload),
    )
    layout = next(
        item
        for item in (
            contract.layouts.cover,
            contract.layouts.section,
            contract.layouts.standard,
            contract.layouts.long_title,
        )
        if item.id == planned.layout_id
    )
    title_bounds = next(item.bounds for item in layout.slots if item.role == "title")
    body_slot = next(item for item in layout.slots if item.role == "body")
    suffix = slide_id.removeprefix("slide:")
    elements = [
        {
            "kind": "shape",
            "elementId": f"body:{suffix}.source-panel",
            "bounds": body_slot.bounds.model_dump(mode="json", by_alias=True),
            "semanticRole": "decoration",
            "shape": "roundRect",
            "colorToken": "surface",
        },
        {
            "kind": "text",
            "elementId": f"body:{suffix}.source-text",
            "bounds": body_slot.bounds.model_dump(mode="json", by_alias=True),
            "zOrder": 1,
            "semanticRole": "body",
            "text": body,
        },
    ]
    plan_payload = {
        "schemaVersion": "slide-visual-plan/v1",
        "scope": "bodyOnly",
        "slideId": slide_id,
        "outlineItemId": f"outline:{suffix}",
        "role": "longTitle" if planned.role == "long-title" else planned.role,
        "layoutId": planned.layout_id,
        "bodySlotId": body_slot.id,
        "sourceDigest": SOURCE_DIGEST,
        "elements": elements,
    }
    plan = SlideVisualPlan(
        **plan_payload,
        planDigest=canonical_digest(plan_payload),
    )
    return slide, plan, title_bounds


def _compile(
    *,
    hint: str | None,
    title: str,
    body: str,
    slide_id: str,
    body_grammar: bool = False,
    structured: dict | None = None,
):
    contract = _contract(body_grammar=body_grammar)
    slide, plan, title_bounds = _slide_and_plan(
        contract,
        title=title,
        body=body,
        slide_id=slide_id,
    )
    inputs = dict(structured or {})
    if hint is not None:
        inputs["roleHint"] = hint
    scene = compile_slide_scene(
        slide=slide,
        visual_plan=plan,
        title_bounds=title_bounds,
        contract=contract,
        structured_inputs=inputs or None,
    )
    authored_text = [
        item
        for item in scene.elements
        if isinstance(item, SceneTextElement) and ".authored." in item.element_id
    ]
    assert _semantic_copy(
        " ".join(" ".join(item.text.split()) for item in authored_text)
    ) == _semantic_copy(body)
    assert all(
        item.style is not None
        and item.style.font_size >= contract.semantic_styles.body.min_font_pt
        for item in authored_text
    )
    return scene


@pytest.mark.parametrize(
    ("hint", "title", "body", "expected_role"),
    [
        ("card", "핵심 실행 원칙", "권리 확인\n품질 검증\n감사 기록", "cardSurface"),
        ("process", "검증 프로세스", "요청 접수\n권리 확인\n품질 검증\n승인", "processAxis"),
        ("timeline", "2026 추진 로드맵", "1분기 진단\n2분기 구축\n3분기 확산\n4분기 고도화", "timelineAxis"),
        ("comparison", "운영 방식 비교", "기존 수작업 중심 운영\n자동 검증 기반 운영", "splitPanel"),
        ("metric", "핵심 성과 지표", "정확도 98%\n처리시간 42% 감소\n감사 대응 3배 향상", "metricRow"),
        ("infographic", "품질 운영 구조", "정책 계층\n검증 계층\n증거 계층", "infographicConcept"),
    ],
)
def test_explicit_archetypes_preserve_copy_and_use_measured_readable_type(
    hint: str,
    title: str,
    body: str,
    expected_role: str,
) -> None:
    scene = _compile(
        hint=hint,
        title=title,
        body=body,
        slide_id=f"slide:{hint}",
    )
    assert any(item.semantic_role == expected_role for item in scene.elements)


def test_existing_authored_body_grammar_drives_the_canonical_scene() -> None:
    scene = _compile(
        hint=None,
        title="서비스 추진 로드맵",
        body="현황 진단\n기반 구축\n서비스 확산\n운영 고도화",
        slide_id="slide:grammar-timeline",
        body_grammar=True,
    )
    assert any(item.semantic_role == "timelineAxis" for item in scene.elements)


def test_inline_process_and_timeline_markers_do_not_collapse_to_two_boxes() -> None:
    process = _compile(
        hint="process",
        title="검증 프로세스",
        body="1. 요청 접수 2. 권리 확인 3. 승인",
        slide_id="slide:inline-process",
        body_grammar=True,
    )
    timeline = _compile(
        hint="timeline",
        title="2026 추진 로드맵",
        body="1분기 진단 2분기 구축 3분기 확산 4분기 고도화",
        slide_id="slide:inline-timeline",
        body_grammar=True,
    )
    assert len([item for item in process.elements if item.semantic_role == "processStep"]) == 3
    assert len([item for item in timeline.elements if item.semantic_role == "timelineStep"]) == 4


@pytest.mark.parametrize(
    ("hint", "body"),
    [
        (
            "card",
            "콘텐츠 분석: 의미와 언어를 판별 | 근거 보존: stable ID와 digest 유지 | 품질 판정: 잘림·겹침·대비 검사",
        ),
        (
            "process",
            "분석: 콘텐츠 구조화 → 레이아웃 선택: CJK 밀도 반영 → 검증: native readback과 PowerPoint 렌더",
        ),
        (
            "comparison",
            "既存 Authored: 래스터 중심 기준 | authored-vNext: Scene 네이티브 객체 | 共通: 동일 콘텐츠와 body design",
        ),
        (
            "infographic",
            "指標: 視覚品質 | データ: digest 保全 | 安全: capability を明示",
        ),
    ],
)
def test_structural_delimiters_are_not_exposed_as_leading_body_copy(
    hint: str,
    body: str,
) -> None:
    scene = _compile(
        hint=hint,
        title="대표 시각 품질",
        body=body,
        slide_id=f"slide:delimiter-{hint}",
    )
    visible = [
        item.text
        for item in scene.elements
        if isinstance(item, SceneTextElement) and ".authored." in item.element_id
    ]
    assert len(visible) == 3
    assert all(re.match(r"^[|→➜▶▸·]", item) is None for item in visible)
    assert _semantic_copy(" ".join(visible)) == _semantic_copy(body)


def test_leading_year_is_bound_to_q1_and_timeline_is_exactly_four_nodes() -> None:
    body = "2026 Q1 基线 → Q2 场景编译 → Q3 PowerPoint 验证 → Q4 发布"
    first = _compile(
        hint="timeline",
        title="2026 质量验证时间线",
        body=body,
        slide_id="slide:quality-timeline",
    )
    second = _compile(
        hint="timeline",
        title="2026 质量验证时间线",
        body=body,
        slide_id="slide:quality-timeline",
    )
    milestones = [
        item
        for item in first.elements
        if isinstance(item, SceneTextElement)
        and item.semantic_role in {"body", "timelineBody"}
    ]
    assert milestones[0].text.startswith("2026 Q1 ")
    assert len(milestones) == 4
    assert all(re.match(r"^[|→➜▶▸·]", item.text) is None for item in milestones)
    assert first.slide_digest == second.slide_digest


def test_process_timeline_and_infographic_use_the_body_envelope() -> None:
    cases = (
        (
            "process",
            "분석: 콘텐츠 구조화 → 레이아웃 선택: CJK 밀도 반영 → 검증: native readback과 PowerPoint 렌더",
            "processStep",
            300_000,
        ),
        (
            "timeline",
            "2026 Q1 基线 → Q2 场景编译 → Q3 PowerPoint 验证 → Q4 发布",
            "timelineStep",
            140_000,
        ),
        (
            "infographic",
            "指標: 視覚品質 | データ: digest 保全 | 安全: capability を明示",
            "infographicConcept",
            220_000,
        ),
    )
    for hint, body, role, minimum_area in cases:
        scene = _compile(
            hint=hint,
            title="대표 시각 품질",
            body=body,
            slide_id=f"slide:occupied-{hint}",
        )
        occupied = sum(
            item.bounds.width * item.bounds.height
            for item in scene.elements
            if isinstance(item, SceneShapeElement) and item.semantic_role == role
        )
        assert occupied >= minimum_area


def test_comparison_metric_and_infographic_have_distinct_visual_hierarchy() -> None:
    comparison = _compile(
        hint="comparison",
        title="운영 방식 비교",
        body="기존 수작업 접수\n자동화 접수\n사후 표본 검증\n전수 상시 검증",
        slide_id="slide:comparison-rich",
    )
    metric = _compile(
        hint="metric",
        title="핵심 성과 지표",
        body="정확도 98%\n처리시간 42% 감소\n감사 대응 3배 향상",
        slide_id="slide:metric-rich",
    )
    infographic = _compile(
        hint="infographic",
        title="품질 운영 구조",
        body="정책 계층\n검증 계층\n증거 계층\n운영 계층",
        slide_id="slide:infographic-rich",
    )
    assert len([item for item in comparison.elements if item.semantic_role == "splitPanel"]) == 4
    assert any(item.semantic_role == "comparisonAxis" for item in comparison.elements)
    metric_text = [
        item
        for item in metric.elements
        if isinstance(item, SceneTextElement) and item.semantic_role in {"body", "metricBody"}
    ]
    assert all(
        item.style is not None
        and item.style.font_size >= 24
        and item.style.font_weight >= 700
        for item in metric_text
    )
    assert any(item.semantic_role == "infographicHub" for item in infographic.elements)
    assert len([item for item in infographic.elements if item.semantic_role == "infographicConcept"]) == 4


def test_five_cjk_points_use_measured_stacked_density_without_tail_folding() -> None:
    scene = _compile(
        hint="card",
        title="긴 한글 운영 원칙",
        body=(
            "권리 검토와 출처 확인을 먼저 수행합니다\n"
            "검증 결과와 근거를 함께 기록합니다\n"
            "편집 가능한 객체 상태를 계속 유지합니다\n"
            "실패한 결과를 성공으로 표시하지 않습니다\n"
            "배포 전 최종 품질을 다시 확인합니다"
        ),
        slide_id="slide:cjk-1",
    )
    assert len([item for item in scene.elements if item.semantic_role == "cardSurface"]) == 5


def test_cover_is_one_editorial_field_and_preserves_planned_title_lines() -> None:
    contract = _contract()
    slide, plan, title_bounds = _slide_and_plan(
        contract,
        title="품질 동등성: Authored와 authored-vNext",
        body="Authored 기준 82점 | authored-vNext 97점 | 네이티브 객체 5종 | 첫 저장 성공",
        slide_id="slide:cover-editorial",
    )
    plan = plan.model_copy(update={"role": "cover"})
    first = compile_slide_scene(
        slide=slide,
        visual_plan=plan,
        title_bounds=title_bounds,
        contract=contract,
    )
    second = compile_slide_scene(
        slide=slide,
        visual_plan=plan,
        title_bounds=title_bounds,
        contract=contract,
    )
    title = next(item for item in first.elements if item.semantic_role == "title")
    body = [
        item
        for item in first.elements
        if isinstance(item, SceneTextElement) and ".authored." in item.element_id
    ]
    assert title.text == "\n".join(slide.title.lines)
    assert title.style is not None and title.style.font_size >= 35
    assert len(body) == 1 and body[0].text.count("\n") == 1
    assert "82점" in body[0].text.splitlines()[0]
    assert "97점" in body[0].text.splitlines()[0]
    assert any(item.semantic_role == "heroField" for item in first.elements)
    assert all(item.semantic_role != "heroEvidence" for item in first.elements)
    assert first.slide_digest == second.slide_digest


def test_three_step_process_uses_an_ascending_non_card_silhouette() -> None:
    scene = _compile(
        hint="process",
        title="안전한 생성 프로세스",
        body="분석: 콘텐츠 구조화 → 레이아웃 선택: CJK 밀도 반영 → 검증: native readback과 PowerPoint 렌더",
        slide_id="slide:ascending-process",
    )
    steps = [
        item
        for item in scene.elements
        if isinstance(item, SceneShapeElement) and item.semantic_role == "processStep"
    ]
    assert len(steps) == 3
    assert [item.bounds.height for item in steps] == sorted(
        item.bounds.height for item in steps
    )
    assert max(item.bounds.bottom for item in steps) - min(
        item.bounds.bottom for item in steps
    ) < 0.01
    assert all(item.shape == "rect" and item.stroke is None for item in steps)


def test_long_cjk_slide_uses_explicit_title_breaks_and_editorial_columns() -> None:
    scene = _compile(
        hint=None,
        title="긴 한글 제목과 中文日本語 혼합 문장이 실제 PowerPoint에서도 안전하게 맞아야 하는 품질 검증",
        body="잘림 없음: 한국어 본문과 中文 설명 및 日本語 データ를 함께 배치해 실제 폰트 측정 기반 fit을 확인한다.",
        slide_id="slide:long-cjk-editorial",
    )
    title = next(item for item in scene.elements if item.semantic_role == "title")
    panel = next(item for item in scene.elements if item.semantic_role == "longTitlePanel")
    divider = next(
        item for item in scene.elements if item.semantic_role == "longTitleAccent"
    )
    body_text = [
        item
        for item in scene.elements
        if isinstance(item, SceneTextElement) and ".authored.long-title-" in item.element_id
    ]
    assert "\n" in title.text
    assert title.style is not None and 28 <= title.style.font_size <= 30
    assert max(item.style.font_size for item in body_text if item.style is not None) <= 24
    assert isinstance(panel, SceneShapeElement) and panel.shape == "rect"
    assert isinstance(divider, SceneShapeElement)
    assert len(body_text) == 2
    assert body_text[0].bounds.right < body_text[1].bounds.x
    assert all(item.style is not None and item.style.font_size >= 16 for item in body_text)


def test_company_standard_swap_keeps_raw_scene_elements_byte_identical() -> None:
    first_contract = _contract()
    second_contract = _company_variant_contract()
    cases = (
        (
            "slide:cover-identity",
            "cover",
            None,
            "품질 동등성: Authored와 authored-vNext",
            "Authored 기준 82점 | authored-vNext 97점 | 네이티브 객체 5종 | 첫 저장 성공",
        ),
        (
            "slide:process-identity",
            None,
            "process",
            "검증 프로세스",
            "분석: 콘텐츠 구조화 → 선택: CJK 밀도 반영 → 검증: PowerPoint 렌더",
        ),
        (
            "slide:long-title-identity",
            None,
            None,
            "긴 한글 제목과 中文日本語 혼합 문장이 실제 PowerPoint에서도 안전하게 맞아야 하는 품질 검증",
            "잘림 없음: 한국어 본문과 中文 설명 및 日本語 データ를 함께 배치해 실제 폰트 측정 기반 fit을 확인한다.",
        ),
    )
    forbidden = {
        "#101827",
        "#182337",
        "#F4F7FB",
        "#D5DDEA",
        "#F4C95D",
        "#39465B",
        "#F28C8C",
        "Arial",
        "COMPANY B",
        "CONFIDENTIAL",
    }
    for slide_id, role_override, hint, title, body in cases:
        slide, plan, title_bounds = _slide_and_plan(
            first_contract,
            title=title,
            body=body,
            slide_id=slide_id,
        )
        if role_override is not None:
            plan = plan.model_copy(update={"role": role_override})
        inputs = None if hint is None else {"roleHint": hint}
        first = compile_slide_scene(
            slide=slide,
            visual_plan=plan,
            title_bounds=title_bounds,
            contract=first_contract,
            structured_inputs=inputs,
        )
        second = compile_slide_scene(
            slide=slide,
            visual_plan=plan,
            title_bounds=Rect(x=140, y=120, width=960, height=180),
            contract=second_contract,
            structured_inputs=inputs,
        )
        first_raw = json.dumps(
            [item.model_dump(mode="json", by_alias=True) for item in first.elements],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        second_raw = json.dumps(
            [item.model_dump(mode="json", by_alias=True) for item in second.elements],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert first_raw == second_raw
        assert all(
            item.semantic_role != "canvasBackground" for item in first.elements
        )
        assert all(value not in first_raw for value in forbidden)


@pytest.mark.parametrize("hint", ["image", "table", "chart"])
def test_structured_objects_receive_role_specific_dominant_geometry(hint: str) -> None:
    digest = "sha256:" + "8" * 64
    structured: dict[str, object]
    if hint == "image":
        structured = {
            "sourceImage": {
                "assetId": "asset:dominant-image",
                "assetDigest": digest,
                "altText": "현장 운영 이미지",
                "crop": "cover",
            }
        }
        element_type = SceneImageElement
    else:
        payload = (
            {"cells": [["구분", "값"], ["정확도", "98%"]]}
            if hint == "table"
            else {
                "categories": ["1분기", "2분기"],
                "series": [{"name": "성과", "values": [72, 98]}],
            }
        )
        data_digest = scene_digest(payload)
        structured = {
            "sourceData": {
                "dataId": f"data:{data_digest.removeprefix('sha256:')[:48]}",
                "dataDigest": data_digest,
                "kind": hint,
                **(
                    {"rows": 2, "columns": 2}
                    if hint == "table"
                    else {
                        "chartType": "column",
                        "accessibilitySummary": "성과가 72에서 98로 상승",
                    }
                ),
            }
        }
        element_type = SceneTableElement if hint == "table" else SceneChartElement
    scene = _compile(
        hint=hint,
        title="구조화 네이티브 객체",
        body="검증된 근거를 핵심 메시지와 함께 제시합니다.",
        slide_id=f"slide:dominant-{hint}",
        structured=structured,
    )
    native = next(item for item in scene.elements if isinstance(item, element_type))
    copy_panel = next(
        item
        for item in scene.elements
        if isinstance(item, SceneShapeElement) and item.semantic_role == "supportingCopyPanel"
    )
    if hint == "table":
        assert native.bounds.width >= 1000
        assert native.bounds.width * native.bounds.height > 2 * (
            copy_panel.bounds.width * copy_panel.bounds.height
        )
    else:
        assert native.bounds.width > copy_panel.bounds.width


def test_structured_image_is_digest_pinned_and_missing_manifest_fails_closed() -> None:
    digest = "sha256:" + "9" * 64
    slide_scene = _compile(
        hint="image",
        title="현장 운영 이미지",
        body="현장 검증 결과를 핵심 메시지와 함께 제시합니다.",
        slide_id="slide:image",
        structured={
            "sourceImage": {
                "assetId": "asset:quality-photo",
                "assetDigest": digest,
                "altText": "현장 품질 검증 장면",
                "crop": "cover",
            }
        },
    )
    with pytest.raises(ValueError, match="assetManifest"):
        compile_deck_scene(
            deck_id="deck:quality",
            revision=1,
            composition_digest="sha256:" + "1" * 64,
            source_digest=SOURCE_DIGEST,
            slides=[slide_scene],
        )
    deck = compile_deck_scene(
        deck_id="deck:quality",
        revision=1,
        composition_digest="sha256:" + "1" * 64,
        source_digest=SOURCE_DIGEST,
        slides=[slide_scene],
        structured_inputs={
            "assetManifest": [
                {
                    "assetId": "asset:quality-photo",
                    "assetDigest": digest,
                    "mediaType": "image/png",
                    "storageReference": "asset-store:asset:quality-photo",
                    "sizeBytes": 2048,
                }
            ]
        },
    )
    assert deck.asset_manifest[0].asset_digest == digest


@pytest.mark.parametrize("hint", ["image", "table", "chart"])
def test_structured_native_roles_require_their_pinned_source(hint: str) -> None:
    with pytest.raises(ValueError, match=f"authored_vnext_{hint}_source_required"):
        _compile(
            hint=hint,
            title="구조화 입력",
            body="검증된 입력만 네이티브 객체로 생성합니다.",
            slide_id=f"slide:missing-{hint}",
        )
