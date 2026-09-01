from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.authored_vnext.composition import (
    SealedContentUnit,
    assert_composition_plan,
    build_composition_plan,
)
from services.authored_vnext.source_native_generation import (
    _StructuredNarrativeProvider,
    _apply_composition_plan,
    _assert_source_image_readback,
)
from services.authored_vnext.source_unitization import (
    SourceUnitizationError,
    build_manifest_source_content_admission,
    build_source_content_admission,
    unitize_source_page,
)


def _slot(
    slot_id: str,
    role: str,
    *,
    accepted: tuple[str, ...] = ("text",),
    required: bool = False,
    capacity: int = 500,
) -> SimpleNamespace:
    return SimpleNamespace(
        slot_id=slot_id,
        semantic_role=role,
        accepted_kinds=accepted,
        required=required,
        patch_handle=SimpleNamespace(kind="authored-qid"),
        capacity_envelope=SimpleNamespace(
            max_characters=capacity,
            max_lines=20,
            characters_per_line=50,
            content_area=capacity * 150,
            character_area=150,
        ),
        bounds=SimpleNamespace(width=800, height=400),
    )


def _frame(frame_id: str, source_index: int) -> SimpleNamespace:
    return SimpleNamespace(
        frame_id=frame_id,
        source_index=source_index,
        role="content",
        density="medium",
        slots=(
            _slot("title", "title", required=True, capacity=120),
            _slot("body", "body", required=True, capacity=1200),
            _slot("image", "image", accepted=("image",), capacity=1200),
        ),
    )


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def test_new_authoring_uses_adaptive_source_flow_without_template_branch() -> None:
    def slot(
        slot_id: str,
        role: str,
        *,
        required: bool,
        maximum_characters: int,
        bounds: dict[str, float],
        container_id: str,
    ) -> dict[str, object]:
        return {
            "slotId": slot_id,
            "semanticRole": role,
            "acceptedKinds": ["text"],
            "required": required,
            "capacityUnits": maximum_characters,
            "capacityEnvelope": {
                "maxCharacters": maximum_characters,
                "maxLines": 2,
                "maxNumericItems": 0,
                "contentArea": bounds["width"] * bounds["height"],
                "characterArea": 150,
            },
            "patchHandle": {
                "kind": "authored-qid",
                "elementId": slot_id,
                "selector": f"[data-qid='{slot_id}']",
                "sourceDigest": _digest("a"),
            },
            "containerId": container_id,
            "extractionConfidence": 1.0,
            "bounds": bounds,
        }

    frame = SimpleNamespace(
        frame_id="frame:generic-cover",
        source_digest=_digest("b"),
        canvas={"x": 0, "y": 0, "width": 1280, "height": 720},
        chrome=(),
        slots=(
            slot(
                "cover.title",
                "title",
                required=True,
                maximum_characters=20,
                bounds={"x": 80, "y": 70, "width": 800, "height": 60},
                container_id="cover-heading",
            ),
            slot(
                "cover.thesis",
                "body",
                required=False,
                maximum_characters=120,
                bounds={"x": 80, "y": 180, "width": 800, "height": 180},
                container_id="cover-content",
            ),
            slot(
                "cover.metric",
                "body",
                required=False,
                maximum_characters=40,
                bounds={"x": 80, "y": 390, "width": 380, "height": 100},
                container_id="cover-content",
            ),
        ),
    )
    units = (
        SealedContentUnit(
            content_unit_id="generated:001:key-message",
            content_digest=_digest("c"),
            kind="text",
            required=True,
            text="가" * 40,
            data=(),
            source_page_index=0,
            source_unit_index=0,
            asset_occurrence_id=None,
            asset_digest=None,
            source_ownership=(),
            semantic_role="title",
        ),
        SealedContentUnit(
            content_unit_id="generated:001:datum:001",
            content_digest=_digest("d"),
            kind="number",
            required=True,
            text="리드타임: 30 %",
            data={"value": 30},
            source_page_index=0,
            source_unit_index=1,
            asset_occurrence_id=None,
            asset_digest=None,
            source_ownership=(),
            semantic_role="data",
        ),
    )
    narrative = SimpleNamespace(
        generation_lane="new-authoring",
        content_graph_digest=_digest("e"),
    )
    slide = SimpleNamespace(
        slide_key="slide-001",
        sequence_index=0,
        sealed_content_units=units,
        content_unit_ids=tuple(item.content_unit_id for item in units),
    )

    plan, bound_units, slots = build_composition_plan(
        narrative=narrative,
        slide=slide,
        frame=frame,
    )

    assert_composition_plan(plan)
    assert plan["layoutMode"] == "source-flow"
    assert len(bound_units) == 2
    assert {item["contentUnitId"] for item in plan["assignments"]} == {
        "generated:001:key-message",
        "generated:001:datum:001",
    }
    title_assignment = next(
        item
        for item in plan["assignments"]
        if item["contentUnitId"] == "generated:001:key-message"
    )
    assert title_assignment["slotId"] == "cover.title"
    assert any(item.get("sourceFlowLayout") for item in slots)


def test_source_native_unitization_preserves_structure_reuses_frames_and_hides_empty_slots() -> None:
    unitized = unitize_source_page(
        """# 품질 중심 AI 개발

첫 문장입니다. 두 번째 문장도 온전히 보존됩니다.

- 자동화된 품질 검증

| 구분 | 목표 |
| --- | --- |
| 개발 | 안전한 배포 |""",
        capacity_limits=(120, 1200),
        required_slot_count=2,
    )
    assert unitized.title_candidate_index == 0
    assert [item.boundary_kind for item in unitized.units] == [
        "title-candidate",
        "paragraph",
        "bullet",
        "table-cell",
        "table-cell",
        "table-cell",
        "table-cell",
    ]
    assert unitized.units[1].text == "첫 문장입니다. 두 번째 문장도 온전히 보존됩니다."
    with pytest.raises(
        SourceUnitizationError,
        match="source_native_sentence_capacity_exceeded",
    ):
        unitize_source_page(
            "제목\n\n" + "분리할 수 없는장문" * 20,
            capacity_limits=(120, 40),
            required_slot_count=2,
        )

    manifest = {
        "manifestId": "manifest-1",
        "manifestDigest": _digest("a"),
        "units": [
            {
                "sourceUnitId": "source-unit-001",
                "sourceIndex": 0,
                "blocks": [
                    {
                        "sourceBlockId": "source-unit-001:text:1",
                        "kind": "text",
                        "semanticRole": "title",
                        "order": 0,
                        "contentDigest": _digest("b"),
                        "payload": {
                            "text": "전략 제목\n첫 문단\n불릿 항목",
                            "paragraphs": [
                                {"text": "전략 제목", "bullet": False},
                                {"text": "첫 문단", "bullet": False},
                                {"text": "불릿 항목", "bullet": True},
                            ],
                        },
                    },
                    {
                        "sourceBlockId": "source-unit-001:table:2",
                        "kind": "table",
                        "semanticRole": "table",
                        "order": 1,
                        "contentDigest": _digest("c"),
                        "payload": {
                            "rows": [
                                [{"text": "구분"}, {"text": "목표"}],
                                [{"text": "개발"}, {"text": "안전"}],
                            ]
                        },
                    },
                    {
                        "sourceBlockId": "source-unit-001:image:3",
                        "kind": "image",
                        "semanticRole": "image",
                        "order": 2,
                        "contentDigest": _digest("d"),
                        "payload": {
                            "assetId": "asset-1",
                            "contentDigest": _digest("e"),
                            "occurrence": 1,
                        },
                    },
                ],
            },
            {
                "sourceUnitId": "source-unit-002",
                "sourceIndex": 1,
                "blocks": [
                    {
                        "sourceBlockId": "source-unit-002:text:1",
                        "kind": "text",
                        "semanticRole": "title",
                        "order": 0,
                        "contentDigest": _digest("f"),
                        "payload": {"text": "두 번째 페이지", "paragraphs": []},
                    },
                    {
                        "sourceBlockId": "source-unit-002:text:2",
                        "kind": "text",
                        "semanticRole": "body",
                        "order": 1,
                        "contentDigest": _digest("1"),
                        "payload": {"text": "원문 순서 보존", "paragraphs": []},
                    },
                ],
            },
        ],
    }
    outline = {
        "manifestPin": {"manifestId": "manifest-1", "digest": _digest("a")},
        "slides": [
            {
                "sourceUnitId": "source-unit-001",
                "sourceIndex": 0,
                "titleBlockIds": ["source-unit-001:text:1"],
            },
            {
                "sourceUnitId": "source-unit-002",
                "sourceIndex": 1,
                "titleBlockIds": ["source-unit-002:text:1"],
            },
        ],
    }
    frames = (_frame("frame:one", 0),)
    generic_admission = build_source_content_admission(
        brief="[SOURCE PAGE 1]\n전략 제목\n\n원문 본문입니다.",
        slide_count=1,
        frames=frames,
    )
    assert generic_admission is not None
    assert generic_admission.content_graph.generation_lane == "new-authoring"
    admission = build_manifest_source_content_admission(
        manifest=manifest,
        outline=outline,
        frames=frames,
        expected_slide_count=2,
    )
    graph = admission.content_graph
    assert graph.generation_lane == "preserve-source"
    assert [item.source_unit_id for item in graph.source_unit_order] == [
        "source-unit-001",
        "source-unit-002",
    ]
    first = graph.pages[0].content_units
    assert [item.kind for item in first].count("image") == 1
    assert [item.content_unit_id for item in first if "table-cell" in item.content_unit_id] == [
        "source-page-001:table-cell-001",
        "source-page-001:table-cell-002",
        "source-page-001:table-cell-003",
        "source-page-001:table-cell-004",
    ]
    image = next(item for item in first if item.kind == "image")
    assert image.payload.asset_occurrence_id == "source-unit-001:image:3"
    assert image.payload.asset_digest == _digest("e")

    provider = _StructuredNarrativeProvider(
        slides_markdown=None,
        package=SimpleNamespace(structure=SimpleNamespace(frames=frames)),
        admission_frame_ids=("frame:one", "frame:one"),
    )
    draft = asyncio.run(
        provider.generate(
            {
                "admittedContentGraph": graph.model_dump(
                    mode="json", by_alias=True
                ),
                "language": "ko-KR",
            }
        )
    )
    assert len(draft["slides"]) == 2
    assert draft["slides"][0]["frameSemantics"] == ["source-frame-001"]
    assert draft["slides"][1]["frameSemantics"] == ["source-frame-001"]

    image_unit = SealedContentUnit(
        content_unit_id="image-unit",
        content_digest=_digest("2"),
        kind="image",
        required=True,
        text="",
        data=(),
        source_page_index=0,
        source_unit_index=0,
        asset_occurrence_id="source-unit-001:image:3",
        asset_digest=_digest("e"),
        source_ownership=(),
        semantic_role="data",
    )
    compiled, semantic, before, after = _apply_composition_plan(
        "<html><body><div data-qid='image'>IMAGE PLACEHOLDER</div><div data-qid='empty'>CHART PLACEHOLDER</div></body></html>",
        plan={
            "assignments": [
                {"contentUnitId": "image-unit", "slotId": "slot:image"}
            ]
        },
        units=[image_unit],
        slots=[
            {
                "slotId": "slot:image",
                "semanticRole": "image",
                "patchHandle": {"kind": "authored-qid", "elementId": "image"},
            },
            {
                "slotId": "slot:empty",
                "semanticRole": "chart",
                "patchHandle": {"kind": "authored-qid", "elementId": "empty"},
            },
        ],
        image_assets={
            "source-unit-001:image:3": {
                "assetDigest": _digest("e"),
                "dataUrl": "data:image/png;base64,iVBORw0KGgo=",
            }
        },
    )
    assert before == after
    assert "IMAGE PLACEHOLDER" not in compiled
    assert "CHART PLACEHOLDER" not in compiled
    assert "data:image/png;base64,iVBORw0KGgo=" in compiled
    assert 'data-source-native-empty="true"' in compiled
    assert semantic[0]["kind"] == "image"
    _assert_source_image_readback(
        rendered_html=compiled,
        units=[image_unit],
        browser_metrics={
            "elements": [
                {
                    "qid": "image--occurrence-001",
                    "source_block_id": "source-unit-001:image:3",
                    "css_visible": True,
                    "effective_opacity": 1,
                    "viewport_intersection_ratio": 1,
                    "image": {
                        "complete": True,
                        "naturalWidth": 640,
                        "naturalHeight": 360,
                    },
                }
            ]
        },
    )
