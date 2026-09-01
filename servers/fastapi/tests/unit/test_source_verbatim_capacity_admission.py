from __future__ import annotations

import pytest

from services.authored_vnext.composition_capacity import canonical_digest
from services.authored_vnext.source_verbatim_capacity import (
    CapacityAdmissionError,
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
    assert sorted(first_page_image_slots.values()) == [8]
    assert receipt["policy"] == {
        "schemaVersion": "source-verbatim-capacity-policy/v1",
        "preservePageCount": True,
        "preservePageOrder": True,
        "allowSlideExpansion": False,
        "allowPageReassignment": False,
        "allowContentMutation": False,
        "allowContentOmission": False,
        "overflowAction": "fail-before-provider",
        "packingStrategy": "same-page-content-fit/v3",
        "frameRanking": "semantic-density-media-data-readability/v1",
        "varietyTieBreak": "least-used-after-fit/v1",
        "minimumAdaptiveTextScale": 0.70,
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
