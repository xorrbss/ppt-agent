from __future__ import annotations

import pytest

from services.authored_vnext.composition_capacity import (
    canonical_digest,
    source_flow_slots,
)
from services.authored_vnext.source_verbatim_capacity import (
    CapacityAdmissionError,
    _admission_units,
    _renderer_blocks,
    _renderer_geometry_fit,
    _renderer_text_geometry,
    assert_source_verbatim_capacity_admission,
    build_source_verbatim_capacity_admission,
)


def _unit(page_index: int, unit_index: int, text: str) -> dict[str, object]:
    unsigned = {
        "contentUnitId": f"page-{page_index + 1:03d}-unit-{unit_index + 1:03d}",
        "kind": "text",
        "required": True,
        "payload": {"text": text, "data": []},
        "sourcePageIndex": page_index,
        "sourceUnitIndex": unit_index,
    }
    return {**unsigned, "contentDigest": canonical_digest(unsigned)}


def _image_unit(
    page_index: int, unit_index: int, *, aspect_ratio: float | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "data": [],
        "assetOccurrenceId": f"asset-{page_index + 1:03d}-{unit_index - 1:03d}",
        "assetDigest": canonical_digest(f"asset-{page_index}-{unit_index}"),
    }
    if aspect_ratio is not None:
        payload["aspectRatio"] = aspect_ratio
    unsigned = {
        "contentUnitId": f"page-{page_index + 1:03d}-image-{unit_index - 1:03d}",
        "kind": "image",
        "required": True,
        "payload": payload,
        "sourcePageIndex": page_index,
        "sourceUnitIndex": unit_index,
    }
    return {**unsigned, "contentDigest": canonical_digest(unsigned)}


def _graph(page_count: int = 43) -> dict[str, object]:
    pages = []
    for index in range(page_count):
        units = [
            _unit(index, 0, f"Page {index + 1} title"),
            _unit(index, 1, f"Source paragraph retained on page {index + 1}."),
        ]
        if index == 0:
            units.extend(_image_unit(index, unit_index) for unit_index in range(2, 10))
        page = {
            "pageId": f"page-{index + 1:03d}",
            "pageIndex": index,
            "contentUnits": units,
        }
        pages.append({**page, "pageDigest": canonical_digest(page)})
    return {
        "contentGraphDigest": canonical_digest(pages),
        "pages": pages,
    }


def _frames(frame_count: int = 10) -> list[dict[str, object]]:
    frames = []
    for index in range(frame_count):
        frame_id = f"frame-{index + 1:03d}"
        frames.append(
            {
                "frameId": frame_id,
                "sourceDigest": canonical_digest(frame_id),
                "slots": [
                    {
                        "slotId": f"{frame_id}-title",
                        "semanticRole": "title",
                        "acceptedKinds": ["text"],
                        "required": True,
                        "capacityEnvelope": {
                            "maxCharacters": 32,
                            "maxLines": 2,
                            "charactersPerLine": 24,
                            "contentArea": 10000,
                            "characterArea": 10,
                        },
                        "patchHandle": {
                            "kind": "authored-qid",
                            "elementId": f"title-{index + 1:03d}",
                        },
                        "bounds": {"width": 400, "height": 80},
                        "extractionConfidence": 1.0,
                    },
                    {
                        "slotId": f"{frame_id}-body",
                        "semanticRole": "body",
                        "acceptedKinds": ["text"],
                        "required": True,
                        "capacityEnvelope": {
                            "maxCharacters": 80,
                            "maxLines": 4,
                            "charactersPerLine": 32,
                            "contentArea": 20000,
                            "characterArea": 10,
                        },
                        "patchHandle": {
                            "kind": "authored-qid",
                            "elementId": f"body-{index + 1:03d}",
                        },
                        "bounds": {"width": 640, "height": 240},
                        "extractionConfidence": 1.0,
                    },
                    *[
                        {
                            "slotId": f"{frame_id}-image-{slot_index + 1:02d}",
                            "semanticRole": "image",
                            "acceptedKinds": ["image"],
                            "required": False,
                            "capacityEnvelope": {
                                "maxCharacters": 1,
                                "maxLines": 1,
                                "maxImages": 3,
                                "contentArea": 20000,
                                "characterArea": 10,
                            },
                            "patchHandle": {
                                "kind": "authored-qid",
                                "elementId": (
                                    f"image-{index + 1:03d}-{slot_index + 1:02d}"
                                ),
                            },
                            "bounds": {"width": 240, "height": 180},
                            "extractionConfidence": 1.0,
                        }
                        for slot_index in range(4)
                    ],
                ],
            }
        )
    return frames


def test_source_verbatim_capacity_admission_preserves_exact_43_pages_and_fails_closed() -> None:
    graph = _graph()
    frames = _frames()
    receipt = build_source_verbatim_capacity_admission(
        content_graph=graph,
        frames=frames,
        frame_structure_digest=canonical_digest(frames),
        expected_slide_count=43,
    )

    assert_source_verbatim_capacity_admission(receipt)
    assert receipt["status"] == "passed"
    assert receipt["sourcePageCount"] == receipt["outputSlideCount"] == 43
    assert receipt["sourcePageOrder"] == [
        f"page-{index + 1:03d}" for index in range(43)
    ]
    assert len(receipt["pages"]) == 43
    assert all(page["status"] == "passed" for page in receipt["pages"])
    assert receipt["candidateFrameCount"] == 10
    assert all(page["candidateFrameCount"] == 10 for page in receipt["pages"])
    assert all(page["hardPassCandidateCount"] == 10 for page in receipt["pages"])
    selected_frame_ids = [page["selectedFrameId"] for page in receipt["pages"]]
    assert len(set(selected_frame_ids)) == 10
    assert len(selected_frame_ids) == 43
    selected_counts = {
        frame_id: selected_frame_ids.count(frame_id)
        for frame_id in set(selected_frame_ids)
    }
    assert sorted(selected_counts.values()) == [4] * 7 + [5] * 3
    assert not any(
        page["selectedVarietyImmediateRepeat"] for page in receipt["pages"]
    )
    assert all(page["assignmentDigest"] for page in receipt["pages"])
    first_page_image_slots: dict[str, int] = {}
    for assignment in receipt["pages"][0]["assignments"]:
        if "-image-" in assignment["contentUnitId"]:
            slot_id = assignment["slotId"]
            first_page_image_slots[slot_id] = first_page_image_slots.get(slot_id, 0) + 1
    assert sum(first_page_image_slots.values()) == 8
    assert all("-image-" in slot_id for slot_id in first_page_image_slots)
    assert receipt["policy"] == {
        "schemaVersion": "source-verbatim-capacity-policy/v1",
        "preservePageCount": True,
        "preservePageOrder": True,
        "allowSlideExpansion": False,
        "allowPageReassignment": False,
        "allowContentMutation": False,
        "allowContentOmission": False,
        "overflowAction": "fail-before-provider",
        "packingStrategy": "same-page-render-geometry-fit/v12",
        "frameRanking": "semantic-density-media-render-geometry/v2",
        "varietyTieBreak": "least-used-after-fit/v1",
        "minimumAdaptiveTextScale": 0.70,
        "minimumBodyFontPoints": 12.0,
        "textLineBoxBreathingPx": 1.0,
        "componentOverlayPaddingPx": 8.0,
        "iconVisualHeightPx": 48.0,
        "mediaVisualHeightPx": 120.0,
        "processMediaVisualHeightPx": 96.0,
        "componentVisualGapPx": 8.0,
        "componentVisualPacking": "best-of-row-major-or-contiguous-columns/v1",
        "titleStackGapPx": 6.0,
        "processStepMedia": "atomic-image-header-body/v1",
        "semanticGrouping": "normalized-source-semantic/v1",
        "tableLayout": "source-matrix-when-present/v1",
        "meaningfulImageGate": "ui-evidence-absolute-geometry/v1",
        "footerLayout": "compact-lane/v1",
        "chromiumOverflowGateRequired": True,
    }

    rebound_graph = {**graph, "contentGraphDigest": canonical_digest("rebound")}
    rebound = build_source_verbatim_capacity_admission(
        content_graph=rebound_graph,
        frames=frames,
        frame_structure_digest=canonical_digest(frames),
        expected_slide_count=43,
    )
    assert rebound["capacityAdmissionDigest"] != receipt["capacityAdmissionDigest"]

    overflow_graph = _graph()
    overflow_graph["pages"][7]["contentUnits"][1]["payload"]["text"] = "X" * 4000
    rejected = build_source_verbatim_capacity_admission(
        content_graph=overflow_graph,
        frames=frames,
        frame_structure_digest=canonical_digest(frames),
        expected_slide_count=43,
    )
    assert rejected["status"] == "failed"
    assert rejected["failureCode"] == "source_verbatim_same_page_capacity_exceeded"
    assert rejected["sourcePageCount"] == rejected["outputSlideCount"] == 43
    assert rejected["pages"][7]["selectedFrameId"] is None
    assert rejected["pages"][7]["hardPassCandidateCount"] == 0
    assert rejected["pages"][7]["unmappedRequiredUnits"] == [
        "page-008-unit-002"
    ]
    with pytest.raises(
        CapacityAdmissionError,
        match="source_verbatim_same_page_capacity_exceeded",
    ) as raised:
        assert_source_verbatim_capacity_admission(rejected)
    assert raised.value.receipt["capacityAdmissionDigest"] == rejected[
        "capacityAdmissionDigest"
    ]


def test_source_verbatim_capacity_applies_target_font_floor_before_selection() -> None:
    graph = _graph(page_count=1)
    frames = _frames(frame_count=1)
    common = {
        "content_graph": graph,
        "frames": frames,
        "frame_structure_digest": canonical_digest(frames),
        "expected_slide_count": 1,
        "target_contain_scale": 0.5625,
        "target_font_policy_id": "dense-source-7px",
        "target_canvas_digest": "sha256:" + "a" * 64,
    }
    accepted = build_source_verbatim_capacity_admission(
        **common, minimum_effective_font_px=7,
    )
    assert accepted["status"] == "passed"
    # Fixture body font is 22 CSS px; contain applies exactly 22 * 0.5625.
    assert accepted["pages"][0]["minimumEffectiveFontPx"] == 12.375
    rejected = build_source_verbatim_capacity_admission(
        **common, minimum_effective_font_px=13,
    )
    assert rejected["status"] == "failed"
    assert rejected["pages"][0]["rendererGeometryFailures"] == [
        {"slotId": "*", "reasonCode": "document_canvas_contain_css_font_floor"}
    ]


def test_source_verbatim_frame_ranking_prefers_content_fit_before_variety() -> None:
    graph = _graph(page_count=2)
    for page in graph["pages"]:
        page["contentUnits"] = page["contentUnits"][:2]
    graph["contentGraphDigest"] = canonical_digest(graph["pages"])
    frames = _frames(frame_count=2)
    loose, fitted = frames
    for slot in loose["slots"][:2]:
        slot["capacityEnvelope"]["maxCharacters"] = 4000
    fitted["slots"][0]["capacityEnvelope"]["maxCharacters"] = 30
    fitted["slots"][1]["capacityEnvelope"]["maxCharacters"] = 70

    receipt = build_source_verbatim_capacity_admission(
        content_graph=graph,
        frames=frames,
        frame_structure_digest=canonical_digest(frames),
        expected_slide_count=2,
    )

    assert_source_verbatim_capacity_admission(receipt)
    assert [page["selectedFrameId"] for page in receipt["pages"]] == [
        fitted["frameId"],
        fitted["frameId"],
    ]
    assert receipt["pages"][1]["selectedVarietyUseCountBeforeSelection"] == 1
    assert receipt["pages"][1]["selectedVarietyImmediateRepeat"] is True
    assert all(
        page["contentFeatures"]["textCharacters"] > 0
        and page["selectedFitQualityRank"]
        for page in receipt["pages"]
    )


def test_source_verbatim_frame_ranking_uses_image_aspect_and_table_semantics() -> None:
    units = [
        _unit(0, 0, "Title"),
        _unit(0, 1, "Body"),
        _image_unit(0, 2, aspect_ratio=16 / 9),
    ]
    table_cell = _unit(0, 3, "Q1 revenue")
    table_cell["contentUnitId"] = "page-001-table-cell-001"
    units.append(table_cell)
    page = {"pageId": "page-001", "pageIndex": 0, "contentUnits": units}
    page["pageDigest"] = canonical_digest(page)
    graph = {
        "contentGraphDigest": canonical_digest([page]),
        "pages": [page],
    }
    portrait, wide_data = _frames(frame_count=2)
    for slot in portrait["slots"][2:]:
        slot["bounds"] = {"width": 180, "height": 320}
    for slot in wide_data["slots"][2:]:
        slot["bounds"] = {"width": 320, "height": 180}
    wide_data["slots"].append(
        {
            "slotId": f"{wide_data['frameId']}-table",
            "semanticRole": "table",
            "acceptedKinds": ["text", "table"],
            "required": False,
            "capacityEnvelope": {
                "maxCharacters": 80,
                "maxLines": 4,
                "charactersPerLine": 32,
                "contentArea": 20000,
                "characterArea": 10,
                "maxTableRows": 10,
                "maxTableColumns": 6,
            },
            "patchHandle": {
                "kind": "authored-qid",
                "elementId": "table-slot",
            },
            "bounds": {"width": 640, "height": 240},
            "extractionConfidence": 1.0,
        }
    )

    receipt = build_source_verbatim_capacity_admission(
        content_graph=graph,
        frames=[portrait, wide_data],
        frame_structure_digest=canonical_digest([portrait, wide_data]),
        expected_slide_count=1,
    )

    assert_source_verbatim_capacity_admission(receipt)
    selected = receipt["pages"][0]
    assert selected["selectedFrameId"] == wide_data["frameId"]
    assert selected["contentFeatures"]["imageOccurrenceCount"] == 1
    assert selected["contentFeatures"]["imageAspectRatios"] == [1.777778]
    assert selected["contentFeatures"]["tableCellCount"] == 1


def test_source_verbatim_admission_uses_renderer_geometry_and_fails_closed() -> None:
    units = [_unit(0, 0, "Title")]
    units.extend(
        _unit(0, index, "한" * 100)
        for index in range(1, 4)
    )
    page = {"pageId": "page-001", "pageIndex": 0, "contentUnits": units}
    page["pageDigest"] = canonical_digest(page)
    graph = {
        "contentGraphDigest": canonical_digest([page]),
        "pages": [page],
    }

    def frame(frame_id: str, *, body_width: float, body_height: float) -> dict[str, object]:
        return {
            "frameId": frame_id,
            "sourceDigest": canonical_digest(frame_id),
            "slots": [
                {
                    "slotId": f"{frame_id}-title",
                    "semanticRole": "title",
                    "acceptedKinds": ["text"],
                    "required": True,
                    "capacityEnvelope": {
                        "maxCharacters": 4000,
                        "maxLines": 1000,
                        "charactersPerLine": 1000,
                        "contentArea": 1000000,
                        "characterArea": 1,
                    },
                    "patchHandle": {
                        "kind": "authored-qid",
                        "elementId": f"{frame_id}-title",
                    },
                    "bounds": {"x": 40, "y": 40, "width": 600, "height": 90},
                    "textStyle": {"fontSize": 40},
                    "extractionConfidence": 1.0,
                },
                {
                    "slotId": f"{frame_id}-body",
                    "semanticRole": "body",
                    "acceptedKinds": ["text"],
                    "required": True,
                    "capacityEnvelope": {
                        "maxCharacters": 4000,
                        "maxLines": 1000,
                        "charactersPerLine": 1000,
                        "contentArea": 1000000,
                        "characterArea": 1,
                    },
                    "patchHandle": {
                        "kind": "authored-qid",
                        "elementId": f"{frame_id}-body",
                    },
                    "bounds": {
                        "x": 40,
                        "y": 150,
                        "width": body_width,
                        "height": body_height,
                    },
                    "textStyle": {"fontSize": 22},
                    "extractionConfidence": 1.0,
                },
            ],
        }

    synthetic_only = frame("synthetic-only", body_width=120, body_height=80)
    actual_fit = frame("actual-fit", body_width=900, body_height=500)
    receipt = build_source_verbatim_capacity_admission(
        content_graph=graph,
        frames=[synthetic_only, actual_fit],
        frame_structure_digest=canonical_digest([synthetic_only, actual_fit]),
        expected_slide_count=1,
    )

    assert_source_verbatim_capacity_admission(receipt)
    selected = receipt["pages"][0]
    assert selected["selectedFrameId"] == "actual-fit"
    assert selected["hardPassCandidateCount"] == 1
    assert selected["selectedRendererGeometryScale"] >= 0.70
    assert selected["rendererGeometryFailures"] == []

    rejected = build_source_verbatim_capacity_admission(
        content_graph=graph,
        frames=[synthetic_only],
        frame_structure_digest=canonical_digest([synthetic_only]),
        expected_slide_count=1,
    )
    assert rejected["status"] == "failed"
    assert rejected["failureCode"] == "source_verbatim_same_page_capacity_exceeded"
    assert rejected["pages"][0]["selectedFrameId"] is None
    assert rejected["pages"][0]["hardPassCandidateCount"] == 0
    assert rejected["pages"][0]["unmappedRequiredUnits"] == []
    geometry_failures = rejected["pages"][0]["rendererGeometryFailures"]
    assert "synthetic-only-body" in {
        item["slotId"] for item in geometry_failures
    }
    assert {
        item["reasonCode"] for item in geometry_failures
    } == {"source_native_text_capacity_exceeded"}

    mixed_units = [
        _unit(0, 0, "Title"),
        _unit(0, 1, "Short body"),
        *[
            _image_unit(0, index)
            for index in range(2, 6)
        ],
    ]
    mixed_page = {
        "pageId": "mixed-page",
        "pageIndex": 0,
        "contentUnits": mixed_units,
    }
    mixed_page["pageDigest"] = canonical_digest(mixed_page)
    mixed_graph = {
        "contentGraphDigest": canonical_digest([mixed_page]),
        "pages": [mixed_page],
    }
    mixed_fit = frame("mixed-fit", body_width=900, body_height=500)
    mixed_fit["slots"][0]["capacityEnvelope"]["maxCharacters"] = 5
    mixed_fit["slots"][1]["capacityEnvelope"]["maxImages"] = 8
    mixed_receipt = build_source_verbatim_capacity_admission(
        content_graph=mixed_graph,
        frames=[mixed_fit],
        frame_structure_digest=canonical_digest([mixed_fit]),
        expected_slide_count=1,
    )
    assert_source_verbatim_capacity_admission(mixed_receipt)
    mixed_selected = mixed_receipt["pages"][0]
    assert mixed_selected["selectedRendererGeometryScale"] == 1.0
    assert mixed_selected["selectedRendererMixedLayouts"] == {
        "mixed-fit-body": "vertical"
    }

    table_units = [_unit(0, 0, "Head")]
    for index in range(1, 9):
        cell = _unit(0, index, f"값{index:02d}")
        cell["contentUnitId"] = f"page-001-table-cell-{index:03d}"
        table_units.append(cell)
    table_page = {
        "pageId": "table-page",
        "pageIndex": 0,
        "contentUnits": table_units,
    }
    table_page["pageDigest"] = canonical_digest(table_page)
    table_fit = frame("table-fit", body_width=900, body_height=80)
    table_fit["slots"][1]["semanticRole"] = "table-cell"
    table_slot = next(
        slot
        for slot in source_flow_slots(table_fit)
        if slot.get("sourceFlowRole") == "data"
    )
    passed, scale, utilization, columns, reason = _renderer_text_geometry(
        slot=table_slot,
        units=_admission_units(table_page, 0)[1:],
    )
    assert (passed, scale, columns, reason) == (True, 1.0, 1, None)
    # The renderer emits `repeat(3,minmax(0,1fr))` for eight metadata-free
    # cells, each `padding:1px 6px;border-left:2px;line-height:1.08!important`
    # inside a `gap:1px 10px` grid: three rows of 22*1.08+2 plus two 1px row
    # gaps is 79.28px in the 80px region.
    assert utilization == pytest.approx(0.991, abs=0.000001)


def test_source_verbatim_normalized_semantics_drive_fixed_geometry_gates() -> None:
    def attach(unit: dict[str, object], **metadata: object) -> dict[str, object]:
        payload = unit["payload"]
        assert isinstance(payload, dict)
        payload["data"] = [
            {
                "datumKey": f"source-semantic.{key}",
                "value": value,
            }
            for key, value in metadata.items()
        ]
        return unit

    grouped = [
        attach(
            _unit(0, index, text),
            role=role,
            **{"group-kind": "process-step", "group-id": "step-01"},
        )
        for index, (role, text) in enumerate(
            (
                ("step-number", "01"),
                ("step-heading", "목표 입력"),
                ("step-body", "자연어 요구와 성공 기준 수집"),
            )
        )
    ]
    grouped_page = {
        "pageId": "grouped",
        "pageIndex": 0,
        "contentUnits": grouped,
    }
    grouped_units = _admission_units(grouped_page, 0)
    blocks = _renderer_blocks(grouped_units)
    assert [len(block) for block in blocks] == [3]
    assert grouped_units[0].semantic_group_kind == "process-step"

    table_raw = []
    for row in range(2):
        for column in range(3):
            table_raw.append(
                attach(
                    _unit(0, row * 3 + column, f"R{row}C{column}"),
                    role="table-header" if row == 0 else "table-cell",
                    **{
                        "group-kind": "table-matrix",
                        "group-id": "table-01",
                        "table-row": row,
                        "table-column": column,
                        "table-row-span": 1,
                        "table-column-span": 1,
                        "table-header": row == 0,
                    },
                )
            )
    table_page = {
        "pageId": "table",
        "pageIndex": 0,
        "contentUnits": table_raw,
    }
    table_units = _admission_units(table_page, 0)
    table_slot = {
        "slotId": "table-slot",
        "semanticRole": "table-cell",
        "sourceFlowRole": "data",
        "sourceFlowLayout": "text",
        "acceptedKinds": ["text", "table"],
        "bounds": {"width": 900, "height": 240},
        "spacing": {},
        "textStyle": {"fontSize": 22},
        "layoutHints": {"columnGap": 18},
        "capacityEnvelope": {"minimumTextScale": 0.70},
    }
    passed, scale, utilization, columns, reason = _renderer_text_geometry(
        slot=table_slot,
        units=table_units,
    )
    assert passed is True and scale == 1.0 and reason is None
    assert columns == 1
    assert utilization < 1.0
    assert {(unit.table_row, unit.table_column) for unit in table_units} == {
        (row, column) for row in range(2) for column in range(3)
    }

    small_image = attach(
        _image_unit(0, 1),
        role="ui-evidence",
        **{"image-aspect-ratio": 16 / 9},
    )
    icon = attach(
        _image_unit(0, 2),
        role="icon",
        **{"image-aspect-ratio": 16 / 9},
    )
    image_slot = {
        "slotId": "image-slot",
        "semanticRole": "image",
        "acceptedKinds": ["image"],
        "bounds": {"width": 400, "height": 225},
        "spacing": {},
        "capacityEnvelope": {"maxImages": 1},
    }
    small_unit = _admission_units(
        {"contentUnits": [small_image]}, 0
    )[0]
    icon_unit = _admission_units({"contentUnits": [icon]}, 0)[0]
    rejected = _renderer_geometry_fit(
        slots=[image_slot],
        assigned={"image-slot": [small_unit]},
    )
    assert rejected["rendererGeometryFit"] is False
    assert rejected["rendererGeometryFailures"] == [
        {
            "slotId": "image-slot",
            "reasonCode": "source_native_meaningful_image_capacity_exceeded",
        }
    ]
    accepted_icon = _renderer_geometry_fit(
        slots=[image_slot],
        assigned={"image-slot": [icon_unit]},
    )
    assert accepted_icon["rendererGeometryFit"] is True

    footer_raw = [
        attach(
            _unit(0, index, text),
            role="compact-footer",
            **{"group-kind": "compact-footer", "group-id": "footer-01"},
        )
        for index, text in enumerate(("제안서", "CONFIDENTIAL", "1 / 43"))
    ]
    footer_units = _admission_units(
        {"contentUnits": [*grouped, *footer_raw]}, 0
    )
    footer_slot = {
        **table_slot,
        "slotId": "footer-slot",
        "semanticRole": "body",
        "sourceFlowRole": "body",
        "bounds": {"width": 1136, "height": 240},
    }
    footer_fit = _renderer_text_geometry(
        slot=footer_slot,
        units=footer_units,
    )
    assert footer_fit[0] is True

    dense_units_raw: list[dict[str, object]] = []
    for step in range(6):
        for offset, (role, text) in enumerate(
            (
                ("step-number", f"{step + 1:02d}"),
                ("step-heading", f"단계 {step + 1}"),
                (
                    "step-body",
                    "요구 구조화와 검증 결과를 같은 페이지에서 읽을 수 있게 유지합니다.",
                ),
            )
        ):
            dense_units_raw.append(
                attach(
                    _unit(0, step * 3 + offset, text),
                    role=role,
                    **{
                        "group-kind": "process-step",
                        "group-id": f"dense-step-{step + 1:02d}",
                    },
                )
            )
    for index in range(2):
        dense_units_raw.append(
            attach(
                _image_unit(0, 100 + index, aspect_ratio=16 / 9),
                role="ui-evidence",
                **{
                    "group-kind": "media-card",
                    "group-id": f"evidence-{index + 1:02d}",
                    "image-kind": "ui-evidence",
                    "image-aspect-ratio": 16 / 9,
                },
            )
        )
    dense_units = _admission_units(
        {"pageId": "components", "contentUnits": dense_units_raw}, 0
    )
    component_surfaces = [
        {
            "componentKind": "cardGrid",
            "slotId": "pack-card-grid",
            "relativeBounds": {"x": 0, "y": 0, "width": 1136, "height": 446},
        },
        {
            "componentKind": "evidenceGallery",
            "slotId": "pack-evidence-gallery",
            "relativeBounds": {"x": 0, "y": 0, "width": 1136, "height": 446},
        },
    ]
    component_slot = {
        **table_slot,
        "slotId": "component-slot",
        "semanticRole": "source-flow-body",
        "sourceFlowRole": "body",
        "sourceFlowLayout": "components",
        "sourceFlowComponentKind": "contentCanvas",
        "acceptedKinds": ["text", "image"],
        "bounds": {"width": 1136, "height": 446},
        "spacing": {},
        "textStyle": {},
        "capacityEnvelope": {"minimumTextScale": 0.70, "maxImages": 8},
        "sourceFlowComponentSurfaces": component_surfaces,
        "sourceFlowComponentGraphDigest": canonical_digest(component_surfaces),
    }
    component_fit = _renderer_geometry_fit(
        slots=[component_slot],
        assigned={"component-slot": dense_units},
    )
    assert component_fit["rendererGeometryFit"] is True
    assert component_fit["rendererGeometryScale"] >= 0.70
    assert component_fit["rendererGeometryColumns"] == 3

    invalid_component_slot = {
        **component_slot,
        "sourceFlowComponentGraphDigest": canonical_digest("tampered"),
    }
    invalid_component = _renderer_geometry_fit(
        slots=[invalid_component_slot],
        assigned={"component-slot": dense_units},
    )
    assert invalid_component["rendererGeometryFit"] is False
    assert invalid_component["rendererGeometryFailures"] == [
        {
            "slotId": "component-slot",
            "reasonCode": "source_native_component_graph_invalid",
        }
    ]
