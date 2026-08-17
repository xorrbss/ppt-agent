from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

from models.authored_vnext.master.ai_import import (
    AI_MASTER_FEATURE_ALGORITHM_VERSION,
    AiCandidateDecision,
    AiLayoutAction,
    UpdateAiMasterAnalysisRequest,
)
from models.authored_vnext.master.operations import MasterRevisionResponse
from api.v1.ppt.endpoints.authored_vnext.master.router import (
    update_ai_master_analysis,
)
from services.authored_vnext.master.ai_import import (
    AiImportError,
    AiMasterAnalysisStore,
    PptxMasterAnalyzer,
    apply_layout_actions,
    build_contract_draft,
    dedupe_layout_candidates,
    validate_reapply,
)


def _layout_snapshot(analysis) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "members": item.slide_numbers,
            "representative": item.representative_slide,
            "slots": [
                slot.model_dump(mode="json", by_alias=True) for slot in item.slots
            ],
            "confidence": item.confidence,
            "differences": item.differences,
            "provenance": item.provenance,
            "sourceLayoutIds": item.source_layout_ids,
            "autoDedupeEligible": item.auto_dedupe_eligible,
            "excluded": item.excluded,
        }
        for item in analysis.layout_candidates
    ]


def _assert_exact_partition(analysis) -> None:
    members = [
        number
        for candidate in analysis.layout_candidates
        for number in candidate.slide_numbers
    ]
    assert sorted(members) == list(range(1, analysis.source.slide_count + 1))
    assert len(members) == len(set(members))


def _normalized_title_body_bounds(
    layout,
    *,
    width: int,
    height: int,
) -> tuple[tuple[str, float, float, float, float], ...]:
    return tuple(
        sorted(
            (
                slot.role,
                slot.bounds.x / width,
                slot.bounds.y / height,
                slot.bounds.width / width,
                slot.bounds.height / height,
            )
            for slot in layout.slots
            if slot.role in {"title", "body"}
        )
    )


def _layouts_are_structurally_equivalent(
    left,
    right,
    *,
    width: int,
    height: int,
    tolerance: float = 0.0075,
) -> bool:
    left_bounds = _normalized_title_body_bounds(
        left,
        width=width,
        height=height,
    )
    right_bounds = _normalized_title_body_bounds(
        right,
        width=width,
        height=height,
    )
    if len(left_bounds) != len(right_bounds):
        return False
    return all(
        left_slot[0] == right_slot[0]
        and all(
            abs(left_value - right_value) <= tolerance
            for left_value, right_value in zip(left_slot[1:], right_slot[1:])
        )
        for left_slot, right_slot in zip(left_bounds, right_bounds)
    )


class _NoopAssetStore:
    def publish_png(self, _value: bytes) -> dict[str, object]:
        return {"assetId": "asset:focused-test-image"}


def _source_pptx_with_repeated_direct_text() -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]

    for number, title in enumerate(
        ["제안 개요", "추진 전략", "구현 범위", "감사합니다"],
        start=1,
    ):
        slide = presentation.slides.add_slide(blank)
        title_box = slide.shapes.add_textbox(
            Inches(0.8), Inches(0.7), Inches(8.6), Inches(0.7)
        )
        title_run = title_box.text_frame.paragraphs[0].add_run()
        title_run.text = title
        title_run.font.name = "Malgun Gothic"
        title_run.font.size = Pt(26)
        title_run.font.color.rgb = RGBColor(0x17, 0x20, 0x33)

        body_box = slide.shapes.add_textbox(
            Inches(0.8), Inches(1.8), Inches(8.8), Inches(3.8)
        )
        body_run = body_box.text_frame.paragraphs[0].add_run()
        body_run.text = f"슬라이드 {number}의 서로 다른 본문"
        body_run.font.name = "Malgun Gothic"
        body_run.font.size = Pt(18)

        # This is ordinary slide content, not inherited master furniture.  Its
        # identical geometry and value across slides must still become evidence.
        rfp_box = slide.shapes.add_textbox(
            Inches(0.55), Inches(6.83), Inches(3.0), Inches(0.28)
        )
        rfp_run = rfp_box.text_frame.paragraphs[0].add_run()
        rfp_run.text = "RFP No. 2026-0042"
        rfp_run.font.name = "Arial"
        rfp_run.font.size = Pt(9)
        rfp_run.font.color.rgb = RGBColor(0x5F, 0x6B, 0x7C)

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _add_direct_text(
    slide,
    text: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    size: int,
    color: tuple[int, int, int] = (0x17, 0x20, 0x33),
) -> None:
    box = slide.shapes.add_textbox(
        Inches(x), Inches(y), Inches(width), Inches(height)
    )
    run = box.text_frame.paragraphs[0].add_run()
    run.text = text
    run.font.name = "Malgun Gothic"
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor(*color)


def _same_native_layout_with_direct_archetypes() -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]

    cover = presentation.slides.add_slide(blank)
    _add_direct_text(
        cover,
        "AI 전환 전략 제안",
        x=1.7,
        y=2.0,
        width=9.9,
        height=1.0,
        size=34,
    )
    _add_direct_text(
        cover,
        "2026 실행 로드맵",
        x=2.8,
        y=3.35,
        width=7.8,
        height=0.45,
        size=16,
        color=(0x35, 0x6A, 0xE6),
    )

    section = presentation.slides.add_slide(blank)
    accent = section.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.85),
        Inches(1.75),
        Inches(0.14),
        Inches(2.25),
    )
    accent.fill.solid()
    accent.fill.fore_color.rgb = RGBColor(0x35, 0x6A, 0xE6)
    accent.line.fill.background()
    _add_direct_text(
        section,
        "02",
        x=1.35,
        y=1.55,
        width=1.2,
        height=0.45,
        size=15,
        color=(0x35, 0x6A, 0xE6),
    )
    _add_direct_text(
        section,
        "추진 전략",
        x=1.35,
        y=2.2,
        width=7.5,
        height=0.9,
        size=31,
    )

    body_texts = [
        "현황과 목표를 정렬합니다.",
        "핵심 과제를 단계별로 실행합니다.",
        "운영 지표와 책임 체계를 함께 설계합니다.",
        "성과를 측정하고 다음 반복에 반영합니다.",
    ]
    for number, text in enumerate(body_texts, start=3):
        slide = presentation.slides.add_slide(blank)
        title_x = {3: 0.74, 4: 0.80, 5: 0.86, 6: 0.92}[number]
        _add_direct_text(
            slide,
            f"실행 과제 {number - 2}",
            x=title_x,
            y=0.55,
            width=8.7,
            height=0.65,
            size=25,
        )
        _add_direct_text(
            slide,
            text,
            x=0.8,
            y=1.75,
            width=7.8,
            height=2.8,
            size=18,
        )
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(9.15),
            Inches(1.75),
            Inches(3.1),
            Inches(2.8),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0xF5, 0xF8, 0xFC)
        card.line.color.rgb = RGBColor(0xD7, 0xDC, 0xE5)
        _add_direct_text(
            slide,
            f"KPI {number - 2}",
            x=9.55,
            y=2.55,
            width=2.3,
            height=0.55,
            size=20,
            color=(0x35, 0x6A, 0xE6),
        )

    closing = presentation.slides.add_slide(blank)
    _add_direct_text(
        closing,
        "감사합니다",
        x=3.25,
        y=2.35,
        width=6.85,
        height=1.0,
        size=36,
    )
    _add_direct_text(
        closing,
        "contact@example.com",
        x=4.2,
        y=4.25,
        width=4.9,
        height=0.4,
        size=13,
        color=(0x5F, 0x6B, 0x7C),
    )

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _two_family_repeated_furniture_pptx() -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    family_layouts = (presentation.slide_layouts[6], presentation.slide_layouts[5])

    for family_index, layout in enumerate(family_layouts):
        for member in range(4):
            slide = presentation.slides.add_slide(layout)
            _add_direct_text(
                slide,
                f"가변 제목 {family_index + 1}-{member + 1}",
                x=0.8,
                y=0.55,
                width=8.7,
                height=0.65,
                size=25,
            )
            _add_direct_text(
                slide,
                f"슬라이드마다 달라지는 본문 {family_index + 1}-{member + 1}",
                x=0.8,
                y=1.75,
                width=8.7,
                height=3.2,
                size=18,
            )
            _add_direct_text(
                slide,
                f"FAMILY-{family_index + 1} CONFIDENTIAL",
                x=0.55,
                y=6.83,
                width=3.2,
                height=0.28,
                size=9,
                color=(0x5F, 0x6B, 0x7C),
            )

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _near_duplicate_body_with_topology_guard_pptx() -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]

    cover = presentation.slides.add_slide(blank)
    _add_direct_text(
        cover,
        "AI 업무 전환 제안",
        x=1.8,
        y=2.15,
        width=9.7,
        height=1.0,
        size=34,
    )

    section = presentation.slides.add_slide(blank)
    _add_direct_text(
        section,
        "01 실행 전략",
        x=1.4,
        y=2.2,
        width=8.0,
        height=0.9,
        size=31,
    )

    for slide_number, drift, color in (
        (3, 0.00, (0x35, 0x6A, 0xE6)),
        (4, 0.04, (0x2F, 0x65, 0xDE)),
    ):
        slide = presentation.slides.add_slide(blank)
        _add_direct_text(
            slide,
            f"실행 과제 {slide_number - 2}",
            x=0.80 + drift,
            y=0.55,
            width=8.7,
            height=0.65,
            size=25,
        )
        _add_direct_text(
            slide,
            f"동일한 슬롯 구조의 본문 {slide_number}",
            x=0.80 + drift,
            y=1.75,
            width=7.8,
            height=2.8,
            size=18,
        )
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(9.15 + drift),
            Inches(1.75),
            Inches(3.1),
            Inches(2.8),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(*color)
        card.line.fill.background()
        _add_direct_text(
            slide,
            f"KPI {slide_number - 2}",
            x=9.55 + drift,
            y=2.55,
            width=2.3,
            height=0.55,
            size=20,
            color=(0xFF, 0xFF, 0xFF),
        )

    comparison = presentation.slides.add_slide(blank)
    _add_direct_text(
        comparison,
        "대안 비교",
        x=0.8,
        y=0.55,
        width=8.7,
        height=0.65,
        size=25,
    )
    for x, title in ((0.8, "대안 A"), (6.85, "대안 B")):
        card = comparison.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(x),
            Inches(1.7),
            Inches(5.65),
            Inches(3.8),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0xF5, 0xF8, 0xFC)
        card.line.color.rgb = RGBColor(0xD7, 0xDC, 0xE5)
        _add_direct_text(
            comparison,
            title,
            x=x + 0.35,
            y=2.05,
            width=4.9,
            height=0.55,
            size=22,
        )
        _add_direct_text(
            comparison,
            f"{title}의 장점과 고려사항",
            x=x + 0.35,
            y=2.95,
            width=4.9,
            height=1.65,
            size=17,
        )

    closing = presentation.slides.add_slide(blank)
    _add_direct_text(
        closing,
        "감사합니다",
        x=3.25,
        y=2.35,
        width=6.85,
        height=1.0,
        size=36,
    )

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _slot_intent_dedupe_regression_pptx() -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]

    cover = presentation.slides.add_slide(blank)
    _add_direct_text(
        cover,
        "AI 운영 혁신 제안",
        x=1.8,
        y=2.1,
        width=9.7,
        height=1.0,
        size=34,
    )

    section = presentation.slides.add_slide(blank)
    _add_direct_text(
        section,
        "02 실행 모델",
        x=1.45,
        y=2.25,
        width=8.0,
        height=0.9,
        size=31,
    )

    # Four card-dashboard variants retain the same three logical body slots.
    # Only low-information drawing fragments, a few pixels of vertical drift,
    # and a broad decorative background (large-region count) vary.
    for variant in range(4):
        slide = presentation.slides.add_slide(blank)
        drift = variant * 0.025
        if variant == 3:
            background = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(0.55),
                Inches(1.45),
                Inches(12.2),
                Inches(4.8),
            )
            background.fill.solid()
            background.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFD)
            background.line.fill.background()
        _add_direct_text(
            slide,
            f"운영 대시보드 {variant + 1}",
            x=0.8,
            y=0.55 + drift,
            width=8.7,
            height=0.65,
            size=25,
        )
        for column, label in enumerate(("품질", "속도", "비용")):
            x = 0.8 + column * 4.1
            card = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(x),
                Inches(1.75 + drift),
                Inches(3.65),
                Inches(3.15),
            )
            card.fill.solid()
            card.fill.fore_color.rgb = RGBColor(0xF2, 0xF6, 0xFC)
            card.line.color.rgb = RGBColor(0xD7, 0xDC, 0xE5)
            _add_direct_text(
                slide,
                f"{label} 지표 {variant + 1}",
                x=x + 0.32,
                y=2.25 + drift,
                width=3.0,
                height=1.25,
                size=19,
            )
        for decoration in range(variant):
            accent = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(0.8 + decoration * 0.35),
                Inches(6.45 + decoration * 0.03),
                Inches(0.22),
                Inches(0.08),
            )
            accent.fill.solid()
            accent.fill.fore_color.rgb = RGBColor(0x35, 0x6A, 0xE6)
            accent.line.fill.background()

    # Four long-title variants share title/body intent despite y-band drift and
    # decorative fragments that previously made their exact topology unequal.
    for variant in range(4):
        slide = presentation.slides.add_slide(blank)
        drift = variant * 0.03
        if variant == 2:
            panel = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(0.45),
                Inches(0.4),
                Inches(12.4),
                Inches(6.25),
            )
            panel.fill.solid()
            panel.fill.fore_color.rgb = RGBColor(0xFB, 0xFC, 0xFE)
            panel.line.fill.background()
        _add_direct_text(
            slide,
            f"지속 가능한 AI 운영 체계를 위한 장문 제목 {variant + 1}",
            x=0.8,
            y=0.6 + drift,
            width=10.2,
            height=1.15,
            size=25,
        )
        _add_direct_text(
            slide,
            f"장문 제목 아래의 단일 본문 슬롯 {variant + 1}",
            x=0.85,
            y=2.35 + drift,
            width=9.35,
            height=2.4,
            size=18,
        )
        for decoration in range(variant + 1):
            accent = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(11.6),
                Inches(0.75 + decoration * 0.3),
                Inches(0.12),
                Inches(0.18),
            )
            accent.fill.solid()
            accent.fill.fore_color.rgb = RGBColor(0x35, 0x6A, 0xE6)
            accent.line.fill.background()

    comparison = presentation.slides.add_slide(blank)
    _add_direct_text(
        comparison,
        "대안 비교",
        x=0.8,
        y=0.55,
        width=8.7,
        height=0.65,
        size=25,
    )
    for x, label in ((0.8, "현재"), (6.85, "목표")):
        for row in range(2):
            _add_direct_text(
                comparison,
                f"{label} 비교 항목 {row + 1}",
                x=x,
                y=1.65 + row * 1.75,
                width=5.55,
                height=1.25,
                size=18,
            )

    two_column = presentation.slides.add_slide(blank)
    _add_direct_text(
        two_column,
        "역할별 실행 항목",
        x=0.8,
        y=0.55,
        width=8.7,
        height=0.65,
        size=25,
    )
    for x, y, text_value in (
        (0.8, 1.7, "실행 조직"),
        (0.8, 3.45, "운영 책임"),
        (6.85, 1.7, "지원 체계"),
    ):
        _add_direct_text(
            two_column,
            text_value,
            x=x,
            y=y,
            width=5.55,
            height=1.25,
            size=18,
        )

    image_led = presentation.slides.add_slide(blank)
    _add_direct_text(
        image_led,
        "서비스 경험",
        x=0.8,
        y=0.55,
        width=8.7,
        height=0.65,
        size=25,
    )
    image_bytes = BytesIO()
    Image.new("RGB", (160, 90), (0x35, 0x6A, 0xE6)).save(
        image_bytes, format="PNG"
    )
    image_bytes.seek(0)
    image_led.shapes.add_picture(
        image_bytes,
        Inches(2.0),
        Inches(1.55),
        Inches(9.3),
        Inches(4.6),
    )

    kpi_chart = presentation.slides.add_slide(blank)
    _add_direct_text(
        kpi_chart,
        "핵심 성과 지표",
        x=0.8,
        y=0.55,
        width=8.7,
        height=0.65,
        size=25,
    )
    for column, value in enumerate(("92%", "3.4x", "-28%")):
        x = 0.85 + column * 4.0
        _add_direct_text(
            kpi_chart,
            value,
            x=x,
            y=1.55,
            width=2.7,
            height=0.8,
            size=29,
            color=(0x35, 0x6A, 0xE6),
        )
        for bar in range(column + 2):
            shape = kpi_chart.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(x + bar * 0.38),
                Inches(5.25 - bar * 0.42),
                Inches(0.24),
                Inches(0.75 + bar * 0.42),
            )
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor(0x35, 0x6A, 0xE6)
            shape.line.fill.background()

    closing = presentation.slides.add_slide(blank)
    _add_direct_text(
        closing,
        "감사합니다",
        x=3.25,
        y=2.35,
        width=6.85,
        height=1.0,
        size=36,
    )

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def test_deterministic_analysis_persists_ai_zone_semantic_manifest_and_reentry(
    tmp_path,
) -> None:
    source = _source_pptx_with_repeated_direct_text()
    analysis_id = "a" * 32
    analyzer = PptxMasterAnalyzer()

    first = analyzer.analyze(
        source,
        filename="customer-proposal.pptx",
        purpose="faithful",
        analysis_id=analysis_id,
    )
    repeated = analyzer.analyze(
        source,
        filename="customer-proposal.pptx",
        purpose="faithful",
        analysis_id=analysis_id,
    )

    assert first.model_dump(mode="json", by_alias=True) == repeated.model_dump(
        mode="json", by_alias=True
    )
    assert first.purpose == "faithful"
    assert first.source.slide_count == 4
    assert first.source.digest.startswith("sha256:")
    assert first.summary.layout_candidates >= 1
    assert first.tolerances == {
        "geometryRatio": 0.004,
        "rotationDegrees": 1.0,
        "fontSizePt": 1.0,
        "layoutVector": 0.24,
        "autoDedupeVector": 0.11,
        "repeatCoverage": 0.5,
    }

    rfp = next(item for item in first.repeated_elements if item.role == "rfpNumber")
    assert {item.slide_number for item in rfp.evidence} == {1, 2, 3, 4}
    decision = AiCandidateDecision(
        candidate_id=rfp.id,
        action="aiZone",
        instruction="이 문서의 RFP 번호를 찾아 'RFP No. {번호}'로 표시하고 없으면 숨긴다.",
        example_text="RFP No. 2026-0042",
        empty_behavior="hidden",
        max_chars=40,
    )
    decisions = [
        decision if item.candidate_id == rfp.id else item for item in first.decisions
    ]
    reviewed = first.model_copy(update={"decisions": decisions})
    contract = build_contract_draft(reviewed)
    validation = validate_reapply(reviewed)
    reviewed = reviewed.model_copy(
        update={"contract_draft": contract, "validation": validation}
    )

    store = AiMasterAnalysisStore(tmp_path / "owner-bound-analyses")
    store.save(reviewed, source=source)
    reopened = store.get(analysis_id)
    feature_sidecar = store.feature_sidecar(analysis_id)

    assert store.source(analysis_id) == source
    assert reopened.source.digest == first.source.digest
    assert reopened.algorithm_version == AI_MASTER_FEATURE_ALGORITHM_VERSION
    assert reopened.feature_sidecar_status == "ready"
    assert feature_sidecar.algorithm_version == AI_MASTER_FEATURE_ALGORITHM_VERSION
    assert feature_sidecar.source_digest == first.source.digest
    assert feature_sidecar.slide_count == first.source.slide_count
    assert [item.slide_number for item in feature_sidecar.slides] == [1, 2, 3, 4]
    sidecar_json = (
        tmp_path
        / "owner-bound-analyses"
        / analysis_id
        / "feature-sidecar.json"
    ).read_text(encoding="utf-8")
    assert "슬라이드 1의 서로 다른 본문" not in sidecar_json
    assert "제안 개요" not in sidecar_json
    assert reopened.decisions == decisions
    assert reopened.validation is not None
    assert reopened.validation.checked_slide_count == 4
    semantic = next(
        item
        for item in reopened.contract_draft.semantic_elements
        if item.content.role == "ai-text-zone"
    )
    assert semantic.editable_by_ai is True
    assert semantic.editable_by_user is False
    assert semantic.content.ai_binding is not None
    assert semantic.content.ai_binding.instruction == decision.instruction
    assert semantic.content.ai_binding.empty_behavior == "hidden"
    assert "konva" not in reopened.model_dump_json(by_alias=True).lower()


def test_feature_sidecar_miss_corrupt_and_binding_mismatch_reextract_source(
    tmp_path,
) -> None:
    source = _source_pptx_with_repeated_direct_text()
    analysis_id = "b" * 32
    analysis = PptxMasterAnalyzer().analyze(
        source,
        filename="sidecar-reextract.pptx",
        purpose="faithful",
        analysis_id=analysis_id,
    )
    store = AiMasterAnalysisStore(tmp_path / "owner-bound-analyses")
    store.save(analysis, source=source)
    sidecar_path = (
        tmp_path
        / "owner-bound-analyses"
        / analysis_id
        / "feature-sidecar.json"
    )

    sidecar_path.unlink()
    missing, missing_reason = store.ensure_feature_sidecar(analysis_id)
    assert missing_reason == "missing"
    assert missing.source_digest == analysis.source.digest
    assert sidecar_path.is_file()

    version_payload = missing.model_dump(mode="json", by_alias=True)
    version_payload["algorithmVersion"] = "ai-master-features/obsolete"
    sidecar_path.write_text(
        json.dumps(version_payload, ensure_ascii=False), encoding="utf-8"
    )
    versioned, version_reason = store.ensure_feature_sidecar(analysis_id)
    assert version_reason == "versionMismatch"
    assert versioned.algorithm_version == AI_MASTER_FEATURE_ALGORITHM_VERSION

    sidecar_path.write_text("{not-valid-json", encoding="utf-8")
    repaired, corrupt_reason = store.ensure_feature_sidecar(analysis_id)
    assert corrupt_reason == "corrupt"
    assert repaired.source_digest == analysis.source.digest

    digest_payload = repaired.model_dump(mode="json", by_alias=True)
    digest_payload["sourceDigest"] = f"sha256:{'0' * 64}"
    sidecar_path.write_text(
        json.dumps(digest_payload, ensure_ascii=False), encoding="utf-8"
    )
    rebound, digest_reason = store.ensure_feature_sidecar(analysis_id)
    assert digest_reason == "sourceDigestMismatch"
    assert rebound.source_digest == analysis.source.digest
    assert rebound.algorithm_version == AI_MASTER_FEATURE_ALGORITHM_VERSION


def test_direct_placement_archetypes_cluster_without_body_over_split_and_split_repeatedly(
    tmp_path,
) -> None:
    source = _same_native_layout_with_direct_archetypes()
    analyzer = PptxMasterAnalyzer()
    analysis = analyzer.analyze(
        source,
        filename="same-native-layout-archetypes.pptx",
        purpose="faithful",
        analysis_id="c" * 32,
    )
    feature_sidecar = analyzer.extract_feature_sidecar(source, analysis)

    candidates = analysis.layout_candidates
    assert 3 <= len(candidates) <= 4
    assert len({item.layout_part for item in candidates}) == 1
    assert any(item.slide_numbers == [1] for item in candidates)
    assert any(item.slide_numbers == [2] for item in candidates)
    assert any(item.slide_numbers == [7] for item in candidates)

    # The four body slides only vary in text content; they are one reusable
    # direct-placement pattern instead of four accidental layouts.
    body = next(item for item in candidates if item.slide_numbers == [3, 4, 5, 6])
    assert body.representative_slide in body.slide_numbers

    cover = next(item for item in candidates if item.slide_numbers == [1])
    section = next(item for item in candidates if item.slide_numbers == [2])
    low_confidence = 0.41
    confidence_seed = analysis.model_copy(
        update={
            "layout_candidates": [
                item.model_copy(update={"confidence": low_confidence})
                if item.id == cover.id
                else item
                for item in candidates
            ]
        }
    )
    forced = apply_layout_actions(
        confidence_seed,
        [
            AiLayoutAction(
                action="merge",
                layout_ids=[cover.id, section.id],
                name="사용자 강제 병합",
            )
        ],
        feature_sidecar=feature_sidecar,
    )
    merged = next(item for item in forced.layout_candidates if item.slide_numbers == [1, 2])
    assert merged.confidence < section.confidence
    assert any(value.startswith("userMerged=") for value in merged.differences)
    assert "forcedMergeDiameterExceeded=true" in merged.differences
    assert any(value.startswith("forcedMergeDiameter=") for value in merged.differences)
    assert any(value.startswith("mergeThreshold=") for value in merged.differences)
    assert f"forcedMergeLowConfidence:{merged.id}" in forced.warnings

    first_split = apply_layout_actions(
        analysis,
        [
            AiLayoutAction(
                action="split",
                layout_ids=[body.id],
                slide_numbers=[4, 5, 6],
                name="본문 후반",
            )
        ],
        feature_sidecar=feature_sidecar,
    )
    remaining = next(
        item for item in first_split.layout_candidates if item.slide_numbers == [3]
    )
    split_pair = next(
        item
        for item in first_split.layout_candidates
        if item.slide_numbers == [4, 5, 6]
    )
    assert remaining.representative_slide in remaining.slide_numbers
    assert split_pair.representative_slide == 5
    split_title = next(slot for slot in split_pair.slots if slot.role == "title")
    assert split_title.bounds.x == pytest.approx(0.80 * 96)
    assert "slotEvidencePending=true" not in split_pair.differences

    second_split = apply_layout_actions(
        first_split,
        [
            AiLayoutAction(
                action="split",
                layout_ids=[split_pair.id],
                slide_numbers=[6],
                name="본문 마지막 변형",
            )
        ],
        feature_sidecar=feature_sidecar,
    )
    assert any(item.slide_numbers == [4, 5] for item in second_split.layout_candidates)
    assert any(item.slide_numbers == [6] for item in second_split.layout_candidates)
    assert all(
        item.representative_slide in item.slide_numbers
        for item in second_split.layout_candidates
    )
    _assert_exact_partition(second_split)

    replayed_first_split = apply_layout_actions(
        analysis,
        [
            AiLayoutAction(
                action="split",
                layout_ids=[body.id],
                slide_numbers=[4, 5, 6],
                name="본문 후반",
            )
        ],
        feature_sidecar=feature_sidecar,
    )
    replayed_pair = next(
        item
        for item in replayed_first_split.layout_candidates
        if item.slide_numbers == [4, 5, 6]
    )
    replayed = apply_layout_actions(
        replayed_first_split,
        [
            AiLayoutAction(
                action="split",
                layout_ids=[replayed_pair.id],
                slide_numbers=[6],
                name="본문 마지막 변형",
            )
        ],
        feature_sidecar=feature_sidecar,
    )
    assert _layout_snapshot(replayed) == _layout_snapshot(second_split)

    freshly_reanalyzed = PptxMasterAnalyzer().analyze(
        source,
        filename="same-native-layout-archetypes.pptx",
        purpose="faithful",
        analysis_id=analysis.analysis_id,
    )
    replayed_after_reanalysis = apply_layout_actions(
        freshly_reanalyzed,
        second_split.layout_actions,
        feature_sidecar=feature_sidecar,
    )
    assert _layout_snapshot(replayed_after_reanalysis) == _layout_snapshot(
        second_split
    )
    assert (
        replayed_after_reanalysis.contract_draft.master_id
        == second_split.contract_draft.master_id
    )

    persisted_contract = second_split.contract_draft.model_copy(
        update={"master_id": "master:persisted-replay"}
    )
    persisted_revision = MasterRevisionResponse(
        master_id="master:persisted-replay",
        revision=3,
        digest=f"sha256:{'1' * 64}",
        status="draft",
        contract=persisted_contract,
    )
    sealed = second_split.model_copy(
        update={
            "contract_draft": persisted_contract,
            "master_revision": persisted_revision,
        }
    )
    store = AiMasterAnalysisStore(tmp_path / "replay-owner")
    store.save(sealed, source=source, feature_sidecar=feature_sidecar)
    reopened = store.get(second_split.analysis_id)
    assert _layout_snapshot(reopened) == _layout_snapshot(second_split)
    assert reopened.layout_actions == second_split.layout_actions
    persisted_sidecar = store.feature_sidecar(second_split.analysis_id)
    assert persisted_sidecar == feature_sidecar

    reentered = update_ai_master_analysis(
        UpdateAiMasterAnalysisRequest(
            layout_actions=[
                AiLayoutAction(
                    action="reanalyze",
                    layout_ids=[body.id],
                )
            ]
        ),
        store,
        _NoopAssetStore(),
        second_split.analysis_id,
    )
    assert _layout_snapshot(reentered) == _layout_snapshot(second_split)
    assert reentered.master_revision == persisted_revision
    assert reentered.contract_draft.master_id == persisted_revision.master_id
    assert reentered.algorithm_version == AI_MASTER_FEATURE_ALGORITHM_VERSION
    assert reentered.feature_sidecar_status == "ready"
    assert reentered.layout_actions[:-1] == second_split.layout_actions
    assert reentered.layout_actions[-1].action == "reanalyze"

    original_actions = list(analysis.layout_actions)
    invalid_actions = [
        AiLayoutAction(action="merge", layout_ids=[cover.id]),
        AiLayoutAction(action="merge", layout_ids=[cover.id, cover.id]),
        AiLayoutAction(
            action="merge",
            layout_ids=[cover.id, "layout-candidate:unknown"],
        ),
        AiLayoutAction(
            action="split",
            layout_ids=[body.id],
            slide_numbers=list(body.slide_numbers),
        ),
    ]
    for invalid in invalid_actions:
        with pytest.raises(AiImportError):
            apply_layout_actions(
                analysis, [invalid], feature_sidecar=feature_sidecar
            )
        assert analysis.layout_actions == original_actions

    canonical = build_contract_draft(second_split)
    canonical_json = second_split.model_copy(
        update={"contract_draft": canonical}
    ).model_dump_json(by_alias=True)
    assert canonical.contract_version == "deckmaster-contract/v1"
    assert len({item.id for item in canonical.semantic_elements}) == len(
        canonical.semantic_elements
    )
    assert "konva" not in canonical_json.lower()


def test_body_macro_dedupe_preserves_fine_evidence_and_explicit_split_replay() -> None:
    source = _near_duplicate_body_with_topology_guard_pptx()
    analyzer = PptxMasterAnalyzer()
    analysis = analyzer.analyze(
        source,
        filename="near-duplicate-topology-guard.pptx",
        purpose="faithful",
        analysis_id="7" * 32,
    )
    assert any(
        item.slide_numbers == [3, 4, 5] for item in analysis.layout_candidates
    )
    feature_sidecar = analyzer.extract_feature_sidecar(source, analysis)
    feature_sidecar = feature_sidecar.model_copy(
        update={
            "slides": [
                slide.model_copy(
                    update={"layout_part": "ppt/slideLayouts/body-drift.xml"}
                )
                if slide.slide_number == 4
                else slide
                for slide in feature_sidecar.slides
            ]
        }
    )

    # Normalize the test seed to one analysis candidate per body archetype.
    # This isolates the registration-time dedupe contract from the analyzer's
    # earlier clustering pass while retaining real source-aware sidecar data.
    isolated = analysis
    for slide_number in (3, 4, 5):
        candidate = next(
            item
            for item in isolated.layout_candidates
            if slide_number in item.slide_numbers
        )
        if candidate.slide_numbers == [slide_number]:
            continue
        isolated = apply_layout_actions(
            isolated,
            [
                AiLayoutAction(
                    action="split",
                    layout_ids=[candidate.id],
                    slide_numbers=[slide_number],
                    name=f"body seed {slide_number}",
                )
            ],
            feature_sidecar=feature_sidecar,
        )
    seed = isolated.model_copy(
        update={
            "layout_candidates": [
                item.model_copy(
                    update={
                        "provenance": "analysis",
                        "source_layout_ids": [item.id],
                        "auto_dedupe_eligible": True,
                    }
                )
                if item.slide_numbers in ([3], [4], [5])
                else item
                for item in isolated.layout_candidates
            ],
            "layout_actions": [],
        }
    )

    deduped = dedupe_layout_candidates(seed, feature_sidecar)
    merged = next(
        item for item in deduped.layout_candidates if item.slide_numbers == [3, 4, 5]
    )

    assert merged.provenance == "autoDedupe"
    assert merged.auto_dedupe_eligible is True
    assert merged.representative_slide in merged.slide_numbers
    assert len(merged.source_layout_ids) == 3
    assert any(value.startswith("autoDedupe=") for value in merged.differences)
    assert any(
        value.startswith("autoDedupeDistance=") for value in merged.differences
    )
    assert any(
        value.startswith("autoDedupeThreshold=") for value in merged.differences
    )
    assert any(
        value.startswith("autoDedupeIntent=") for value in merged.differences
    )
    assert "autoDedupeFamily=structured" in merged.differences
    assert "autoDedupeThreshold=0.3600" in merged.differences
    assert "autoDedupeMetric=logicalSlotMeanAbsolute" in merged.differences
    assert "autoDedupeLinkage=completeMemberSlots" in merged.differences
    assert "autoDedupeMacroGuard=matched" in merged.differences
    assert (
        "autoDedupeTopologyGuard=fineEvidencePreserved"
        in merged.differences
    )
    assert "nativeLayoutMismatch=true" in merged.differences
    assert any(
        item.slide_numbers == [1] and item.kind == "cover"
        for item in deduped.layout_candidates
    )
    assert any(
        item.slide_numbers == [2] and item.kind == "section"
        for item in deduped.layout_candidates
    )
    assert any(
        item.slide_numbers == [6] and item.kind == "closing"
        for item in deduped.layout_candidates
    )
    _assert_exact_partition(deduped)
    assert _layout_snapshot(
        dedupe_layout_candidates(deduped, feature_sidecar)
    ) == _layout_snapshot(deduped)

    split_action = AiLayoutAction(
        action="split",
        layout_ids=[merged.id],
        slide_numbers=[4],
        name="사용자 본문 분리",
    )
    split = apply_layout_actions(
        deduped,
        [split_action],
        feature_sidecar=feature_sidecar,
    )
    split_members = [
        item
        for item in split.layout_candidates
        if item.slide_numbers in ([3, 5], [4])
    ]
    assert len(split_members) == 2
    assert all(item.provenance == "userSplit" for item in split_members)
    assert all(not item.auto_dedupe_eligible for item in split_members)
    assert _layout_snapshot(
        dedupe_layout_candidates(split, feature_sidecar)
    ) == _layout_snapshot(split)
    _assert_exact_partition(split)

    freshly_reanalyzed = analyzer.analyze(
        source,
        filename="near-duplicate-topology-guard.pptx",
        purpose="faithful",
        analysis_id=analysis.analysis_id,
    )
    repeated_reanalysis = analyzer.analyze(
        source,
        filename="near-duplicate-topology-guard.pptx",
        purpose="faithful",
        analysis_id=analysis.analysis_id,
    )
    replayed = apply_layout_actions(
        freshly_reanalyzed,
        [split_action],
        feature_sidecar=feature_sidecar,
    )
    replayed = dedupe_layout_candidates(replayed, feature_sidecar)
    replayed_again = apply_layout_actions(
        repeated_reanalysis,
        [split_action],
        feature_sidecar=feature_sidecar,
    )
    replayed_again = dedupe_layout_candidates(replayed_again, feature_sidecar)
    assert _layout_snapshot(replayed_again) == _layout_snapshot(replayed)
    assert replayed_again.layout_actions == replayed.layout_actions
    _assert_exact_partition(replayed)

    canonical = build_contract_draft(replayed)
    canonical_layout_ids = [
        canonical.layouts.cover.id,
        canonical.layouts.section.id,
        canonical.layouts.standard.id,
        canonical.layouts.long_title.id,
        *(layout.id for layout in canonical.custom_layouts),
    ]
    assert len(canonical_layout_ids) == len(set(canonical_layout_ids))
    assert canonical.contract_version == "deckmaster-contract/v1"


def test_slot_intent_dedupe_ignores_decorations_but_keeps_body_archetypes() -> None:
    source = _slot_intent_dedupe_regression_pptx()
    analyzer = PptxMasterAnalyzer()
    analysis = analyzer.analyze(
        source,
        filename="slot-intent-dedupe-regression.pptx",
        purpose="faithful",
        analysis_id="6" * 32,
    )
    feature_sidecar = analyzer.extract_feature_sidecar(source, analysis)
    expected_memberships = (
        [1],
        [2],
        [3, 4, 5, 6, 11, 12, 14],
        [7, 8, 9, 10],
        [13],
        [15],
    )

    assert [item.slide_numbers for item in analysis.layout_candidates] == list(
        expected_memberships
    )
    assert analysis.summary.layout_candidates == len(expected_memberships)
    assert analysis.tolerances["autoDedupeVector"] == 0.11
    assert all(
        item.representative_slide in item.slide_numbers
        for item in analysis.layout_candidates
    )
    _assert_exact_partition(analysis)

    # Force every body slide back into a singleton analysis candidate so the
    # registration boundary must rebuild only the three product macro families.
    isolated = analysis
    macro_members = set(range(3, 15))
    for slide_number in sorted(macro_members):
        candidate = next(
            item
            for item in isolated.layout_candidates
            if slide_number in item.slide_numbers
        )
        if candidate.slide_numbers == [slide_number]:
            continue
        isolated = apply_layout_actions(
            isolated,
            [
                AiLayoutAction(
                    action="split",
                    layout_ids=[candidate.id],
                    slide_numbers=[slide_number],
                    name=f"intent seed {slide_number}",
                )
            ],
            feature_sidecar=feature_sidecar,
        )
    seed = isolated.model_copy(
        update={
            "layout_candidates": [
                item.model_copy(
                    update={
                        "provenance": "analysis",
                        "source_layout_ids": [item.id],
                        "auto_dedupe_eligible": True,
                    }
                )
                if item.slide_numbers[0] in macro_members
                else item
                for item in isolated.layout_candidates
            ],
            "layout_actions": [],
        }
    )

    deduped = dedupe_layout_candidates(seed, feature_sidecar)
    assert [item.slide_numbers for item in deduped.layout_candidates] == list(
        expected_memberships
    )
    structured = next(
        item
        for item in deduped.layout_candidates
        if item.slide_numbers == [3, 4, 5, 6, 11, 12, 14]
    )
    long_title = next(
        item
        for item in deduped.layout_candidates
        if item.slide_numbers == [7, 8, 9, 10]
    )
    for candidate, family, member_count in (
        (structured, "structured", 7),
        (long_title, "longTitle", 4),
    ):
        assert candidate.provenance == "autoDedupe"
        assert candidate.auto_dedupe_eligible is True
        assert candidate.representative_slide in candidate.slide_numbers
        assert len(candidate.source_layout_ids) == member_count
        assert f"autoDedupeFamily={family}" in candidate.differences
        assert "autoDedupeThreshold=0.3600" in candidate.differences
        assert "autoDedupeMetric=logicalSlotMeanAbsolute" in candidate.differences
        assert "autoDedupeLinkage=completeMemberSlots" in candidate.differences
        assert "autoDedupeMacroGuard=matched" in candidate.differences
        assert (
            "autoDedupeTopologyGuard=fineEvidencePreserved"
            in candidate.differences
        )

    structured_intents = {
        value.removeprefix("autoDedupeIntent=")
        for value in structured.differences
        if value.startswith("autoDedupeIntent=")
    }
    assert {"cards", "comparison", "twoColumn", "kpiChart"}.issubset(
        structured_intents
    )
    assert sum(
        value.startswith("autoDedupeFineTopology=")
        for value in structured.differences
    ) >= 4
    assert "autoDedupeIntent=longTitle" in long_title.differences
    visual = next(
        item for item in deduped.layout_candidates if item.slide_numbers == [13]
    )
    assert visual.representative_slide == 13
    assert visual.auto_dedupe_eligible is True

    contract = build_contract_draft(deduped)
    width = deduped.source.width_px
    height = deduped.source.height_px
    standard_bounds = _normalized_title_body_bounds(
        contract.layouts.standard,
        width=width,
        height=height,
    )
    long_title_bounds = _normalized_title_body_bounds(
        contract.layouts.long_title,
        width=width,
        height=height,
    )
    assert standard_bounds != long_title_bounds
    assert not _layouts_are_structurally_equivalent(
        contract.layouts.standard,
        contract.layouts.long_title,
        width=width,
        height=height,
    )
    assert long_title.name not in {
        layout.name for layout in contract.custom_layouts
    }
    assert [
        layout.name for layout in contract.custom_layouts
    ].count(visual.name) == 1

    equivalent_custom_name = "표준 본문 구조 중복 후보"
    equivalent_custom = structured.model_copy(
        update={
            "id": "layout-candidate:equivalent-standard-custom",
            "name": equivalent_custom_name,
            "kind": "custom",
            "slide_numbers": [structured.representative_slide],
            "source_layout_ids": [structured.id],
        }
    )
    with_equivalent_custom = deduped.model_copy(
        update={
            "layout_candidates": [
                *deduped.layout_candidates,
                equivalent_custom,
            ]
        }
    )
    first_contract = build_contract_draft(with_equivalent_custom)
    repeated_contract = build_contract_draft(with_equivalent_custom)
    assert first_contract.model_dump(mode="json", by_alias=True) == (
        repeated_contract.model_dump(mode="json", by_alias=True)
    )
    assert equivalent_custom_name not in {
        layout.name for layout in first_contract.custom_layouts
    }
    registered_layouts = [
        first_contract.layouts.standard,
        first_contract.layouts.long_title,
        *first_contract.custom_layouts,
    ]
    assert all(
        not _layouts_are_structurally_equivalent(
            registered_layouts[left_index],
            registered_layouts[right_index],
            width=width,
            height=height,
        )
        for left_index in range(len(registered_layouts))
        for right_index in range(left_index + 1, len(registered_layouts))
    )
    assert any(
        item.slide_numbers == [1] and item.kind == "cover"
        for item in deduped.layout_candidates
    )
    assert any(
        item.slide_numbers == [2] and item.kind == "section"
        for item in deduped.layout_candidates
    )
    assert any(
        item.slide_numbers == [15] and item.kind == "closing"
        for item in deduped.layout_candidates
    )
    assert _layout_snapshot(
        dedupe_layout_candidates(deduped, feature_sidecar)
    ) == _layout_snapshot(deduped)
    _assert_exact_partition(deduped)

    split_action = AiLayoutAction(
        action="split",
        layout_ids=[structured.id],
        slide_numbers=[6],
        name="사용자 카드 변형 분리",
    )
    split = apply_layout_actions(
        deduped,
        [split_action],
        feature_sidecar=feature_sidecar,
    )
    split_members = [
        item
        for item in split.layout_candidates
        if set(item.slide_numbers).issubset({3, 4, 5, 6, 11, 12, 14})
    ]
    assert [item.slide_numbers for item in split_members] == [
        [3, 4, 5, 11, 12, 14],
        [6],
    ]
    assert all(item.provenance == "userSplit" for item in split_members)
    assert all(not item.auto_dedupe_eligible for item in split_members)
    assert _layout_snapshot(
        dedupe_layout_candidates(split, feature_sidecar)
    ) == _layout_snapshot(split)
    _assert_exact_partition(split)

    replayed = apply_layout_actions(
        deduped,
        [split_action],
        feature_sidecar=feature_sidecar,
    )
    repeated_dedupe = dedupe_layout_candidates(seed, feature_sidecar)
    replayed_again = apply_layout_actions(
        repeated_dedupe,
        [split_action],
        feature_sidecar=feature_sidecar,
    )
    assert _layout_snapshot(replayed_again) == _layout_snapshot(replayed)
    assert replayed_again.layout_actions == replayed.layout_actions
    _assert_exact_partition(replayed_again)


def test_scoped_family_furniture_preserves_variable_title_and_body_signals() -> None:
    source = _two_family_repeated_furniture_pptx()
    analyzer = PptxMasterAnalyzer()
    analysis = analyzer.analyze(
        source,
        filename="two-family-furniture.pptx",
        purpose="faithful",
        analysis_id="8" * 32,
    )
    sidecar = analyzer.extract_feature_sidecar(source, analysis)

    assert len({slide.design_family for slide in sidecar.slides}) == 2
    for slide in sidecar.slides:
        footer = [
            element
            for element in slide.elements
            if element.kind == "text"
            and element.bounds.y > 6.7 * 96
            and element.bounds.width < 4.0 * 96
        ]
        variable_title = [
            element
            for element in slide.elements
            if element.kind == "text"
            and element.bounds.y < 1.0 * 96
            and 8.0 * 96 < element.bounds.width < 9.5 * 96
        ]
        variable_body = [
            element
            for element in slide.elements
            if element.kind == "text"
            and 1.5 * 96 < element.bounds.y < 5.5 * 96
            and 8.0 * 96 < element.bounds.width < 9.5 * 96
        ]
        assert footer and all(element.furniture for element in footer)
        assert variable_title and all(
            not element.furniture for element in variable_title
        )
        assert variable_body and all(not element.furniture for element in variable_body)

    assert any(
        slot.role == "title"
        for candidate in analysis.layout_candidates
        for slot in candidate.slots
    )
    _assert_exact_partition(analysis)


def test_forced_merge_preserves_native_layout_mismatch_evidence() -> None:
    source = _same_native_layout_with_direct_archetypes()
    analyzer = PptxMasterAnalyzer()
    analysis = analyzer.analyze(
        source,
        filename="native-layout-mismatch.pptx",
        purpose="faithful",
        analysis_id="9" * 32,
    )
    sidecar = analyzer.extract_feature_sidecar(source, analysis)
    cover = next(item for item in analysis.layout_candidates if item.slide_numbers == [1])
    section = next(item for item in analysis.layout_candidates if item.slide_numbers == [2])
    mismatch_sidecar = sidecar.model_copy(
        update={
            "slides": [
                item.model_copy(
                    update={
                        "layout_part": "ppt/slideLayouts/injected-mismatch.xml"
                    }
                )
                if item.slide_number == 2
                else item
                for item in sidecar.slides
            ]
        }
    )

    merged_analysis = apply_layout_actions(
        analysis,
        [
            AiLayoutAction(
                action="merge",
                layout_ids=[cover.id, section.id],
                name="native mismatch forced merge",
            )
        ],
        feature_sidecar=mismatch_sidecar,
    )
    assert len(merged_analysis.layout_candidates) == len(analysis.layout_candidates) - 1
    merged = next(
        item for item in merged_analysis.layout_candidates if item.slide_numbers == [1, 2]
    )
    assert merged.representative_slide in merged.slide_numbers
    assert "nativeLayoutMismatch=true" in merged.differences
    _assert_exact_partition(merged_analysis)


def test_optional_local_31_slide_corpus_keeps_expected_cluster_guards() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    analysis_ids = (
        "426859183dd8480490757570650068ed",
        "39da1a6e1ad74856b56aa91de7125701",
    )
    source_paths = [
        source_path
        for analysis_id in analysis_ids
        for source_path in (
            repository_root / "app_data" / "authored_vnext" / "ai-master-import" / "users"
        ).glob(f"*/{analysis_id}/source.pptx")
    ]
    if not source_paths:
        pytest.skip("optional local 31-slide AI-master corpus is unavailable")

    source = source_paths[0].read_bytes()
    if len(source_paths) > 1:
        assert all(path.read_bytes() == source for path in source_paths[1:])

    analysis = PptxMasterAnalyzer().analyze(
        source,
        filename="템플릿테스트.pptx",
        purpose="faithful",
        analysis_id="d" * 32,
    )
    expected_memberships = [
        [1],
        [2, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, 17, 18, 19, 20, 21, 22, 26, 27, 28, 30],
        [3, 16, 23, 24, 25, 29],
        [6, 12],
        [31],
    ]

    assert analysis.source.slide_count == 31
    assert analysis.summary.layout_candidates == 5
    assert [
        candidate.slide_numbers for candidate in analysis.layout_candidates
    ] == expected_memberships
    assert analysis.layout_candidates[0].kind == "cover"
    assert analysis.layout_candidates[-1].kind == "closing"
    assert all(
        candidate.representative_slide in candidate.slide_numbers
        for candidate in analysis.layout_candidates
    )
    _assert_exact_partition(analysis)

    structured, long_title, visual = analysis.layout_candidates[1:4]
    assert structured.representative_slide == 19
    assert long_title.representative_slide == 24
    assert visual.representative_slide == 6
    assert "autoDedupeFamily=structured" in structured.differences
    assert "autoDedupeFamily=longTitle" in long_title.differences
    assert "autoDedupeFamily=visual" in visual.differences
    assert "autoDedupeIntent=cards" in structured.differences
    assert "autoDedupeIntent=twoColumn" in structured.differences
    assert "autoDedupeIntent=longTitle" in long_title.differences
    assert "autoDedupeIntent=imageLed" in visual.differences
    assert "autoDedupeDistance=0.0778" in structured.differences
    assert "autoDedupeDistance=0.0975" in long_title.differences
    assert "autoDedupeDistance=0.0668" in visual.differences
    merged_families = (structured, long_title, visual)
    assert all(
        "autoDedupeThreshold=0.3600" in candidate.differences
        for candidate in merged_families
    )
    assert all(
        "autoDedupeMetric=logicalSlotMeanAbsolute" in candidate.differences
        for candidate in merged_families
    )
    assert all(
        "autoDedupeLinkage=completeMemberSlots" in candidate.differences
        for candidate in merged_families
    )
    assert all(
        "autoDedupeMacroGuard=matched" in candidate.differences
        for candidate in merged_families
    )
    assert all(
        "autoDedupeTopologyGuard=fineEvidencePreserved"
        in candidate.differences
        for candidate in merged_families
    )

    sidecar = PptxMasterAnalyzer().extract_feature_sidecar(source, analysis)
    assert _layout_snapshot(
        dedupe_layout_candidates(analysis, sidecar)
    ) == _layout_snapshot(analysis)

    contract = build_contract_draft(analysis)
    width = analysis.source.width_px
    height = analysis.source.height_px
    visual_layouts = [
        layout for layout in contract.custom_layouts if layout.name == visual.name
    ]
    assert len(contract.custom_layouts) == 2
    assert len(visual_layouts) == 1
    assert long_title.name not in {
        layout.name for layout in contract.custom_layouts
    }
    body_representations = [
        contract.layouts.standard,
        contract.layouts.long_title,
        visual_layouts[0],
    ]
    normalized_body_representations = [
        _normalized_title_body_bounds(
            layout,
            width=width,
            height=height,
        )
        for layout in body_representations
    ]
    assert len(set(normalized_body_representations)) == 3
    assert all(
        not _layouts_are_structurally_equivalent(
            body_representations[left_index],
            body_representations[right_index],
            width=width,
            height=height,
        )
        for left_index in range(len(body_representations))
        for right_index in range(left_index + 1, len(body_representations))
    )
