from __future__ import annotations

from types import SimpleNamespace

from services.authored_vnext.composition import build_composition_plan
from services.authored_vnext.composition_capacity import source_flow_slots


def _slot(
    slot_id: str,
    role: str,
    kind: str,
    bounds: dict[str, float],
    *,
    maximum: int,
    required: bool = False,
) -> dict[str, object]:
    element_id = slot_id.replace(":", "-")
    return {
        "slotId": slot_id,
        "semanticRole": role,
        "acceptedKinds": [kind],
        "required": required,
        "capacityUnits": 1,
        "capacityEnvelope": {
            "measurementBasis": "chromium-observed",
            "contentArea": bounds["width"] * bounds["height"],
            "maxCharacters": maximum if kind == "text" else None,
            "maxLines": 4 if kind == "text" else None,
        },
        "patchHandle": {
            "kind": "authored-qid",
            "sourcePart": "authored/reference",
            "elementId": element_id,
            "selector": f"[data-qid='{element_id}']",
            "sourceDigest": "sha256:" + ("1" if kind == "text" else "2") * 64,
        },
        "containerId": "content-canvas",
        "bounds": bounds,
        "textStyle": {
            "latinFont": "Inter",
            "eastAsianFont": "Pretendard",
            "fontSize": 22 if role == "body" else 42,
            "fontWeight": 500 if role == "body" else 800,
            "alignment": "left",
        }
        if kind == "text"
        else None,
        "spacing": {
            "insetLeft": 12,
            "insetTop": 8,
            "insetRight": 12,
            "insetBottom": 8,
        }
        if kind == "text"
        else None,
        "extractionConfidence": 1.0,
    }


def _frame() -> dict[str, object]:
    return {
        "frameId": "arbitrary-registered-frame",
        "sourceDigest": "sha256:" + "f" * 64,
        "canvasBounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "slots": [
            _slot(
                "slot:title",
                "title",
                "text",
                {"x": 120, "y": 90, "width": 1180, "height": 130},
                maximum=120,
                required=True,
            ),
            _slot(
                "slot:body-left",
                "body",
                "text",
                {"x": 120, "y": 290, "width": 500, "height": 600},
                maximum=300,
                required=True,
            ),
            _slot(
                "slot:body-right",
                "body",
                "text",
                {"x": 680, "y": 290, "width": 500, "height": 600},
                maximum=300,
            ),
            _slot(
                "slot:image-top",
                "visual",
                "image",
                {"x": 1350, "y": 290, "width": 420, "height": 270},
                maximum=1,
            ),
            _slot(
                "slot:image-bottom",
                "visual",
                "image",
                {"x": 1350, "y": 600, "width": 420, "height": 290},
                maximum=1,
            ),
        ],
        "chrome": [
            {
                "elementId": "protected-divider",
                "kind": "shape",
                "protected": True,
                "bounds": {"x": 620, "y": 290, "width": 60, "height": 600},
                "sourceXmlDigest": "sha256:" + "d" * 64,
            },
            {
                "elementId": "protected-wordmark",
                "kind": "image",
                "protected": True,
                "bounds": {"x": 1600, "y": 70, "width": 170, "height": 80},
                "sourceXmlDigest": "sha256:" + "e" * 64,
            },
        ],
    }


def _intersects(left: dict[str, float], right: dict[str, float]) -> bool:
    return not (
        left["x"] + left["width"] <= right["x"]
        or right["x"] + right["width"] <= left["x"]
        or left["y"] + left["height"] <= right["y"]
        or right["y"] + right["height"] <= left["y"]
    )


def _unit(unit_id: str, index: int, text: str) -> dict[str, object]:
    return {
        "contentUnitId": unit_id,
        "kind": "text",
        "required": True,
        "payload": {"text": text},
        "sourcePageIndex": 0,
        "sourceUnitIndex": index,
        "sourceOwnership": [{"useMode": "verbatim", "required": True}],
    }


def test_source_flow_regions_are_pack_derived_atomic_and_capacity_fail_closed() -> None:
    frame = _frame()
    original_handles = {
        item["slotId"]: item["patchHandle"] for item in frame["slots"]
    }
    slots = source_flow_slots(frame)
    assert {item["slotId"]: item["patchHandle"] for item in slots} == original_handles

    active = [item for item in slots if item.get("sourceFlowLayout")]
    assert {item["sourceFlowRole"] for item in active} == {
        "title",
        "body",
        "images",
    }
    assert all(item["canvasBounds"] == frame["canvasBounds"] for item in slots)
    assert all(item["bounds"] != {"x": 56.0, "y": 64.0, "width": 720.0, "height": 608.0} for item in active)
    assert all(item["layoutHints"]["preserveUnitBoundary"] is True for item in active)
    assert all(
        item["layoutHints"]["overflowPolicy"]
        == "same-page-fail-before-provider"
        for item in active
    )
    assert all("columnCount" in item["layoutHints"] for item in active)
    assert all("textStyle" in item and "spacing" in item for item in active)

    protected_bounds = [item["bounds"] for item in frame["chrome"]]
    assert all(
        not _intersects(item["bounds"], protected)
        for item in active
        for protected in protected_bounds
    )
    image_region = next(item for item in active if item["sourceFlowRole"] == "images")
    assert image_region["capacityEnvelope"]["maxImages"] == 2
    assert image_region["layoutHints"]["rowCount"] == 2
    suppressed = [item for item in slots if item.get("sourceFlowSuppressedBy")]
    assert len(suppressed) == 1
    assert suppressed[0]["acceptedKinds"] == []

    units = [
        _unit("unit-title", 0, "Pack-derived title"),
        _unit("unit-body", 1, "A complete source sentence stays indivisible."),
    ]
    narrative = SimpleNamespace(content_graph_digest="sha256:" + "a" * 64)
    slide = SimpleNamespace(
        slide_key="slide-001",
        sequence_index=0,
        content_unit_ids=("unit-title", "unit-body"),
        sealed_content_units=tuple(units),
    )
    passed, _, planned_slots = build_composition_plan(
        narrative=narrative, slide=slide, frame=frame
    )
    assert passed["status"] == "passed"
    assert passed["layoutMode"] == "source-flow"
    assert passed["flowRegions"]
    assert passed["flowLayoutDigest"].startswith("sha256:")
    assert [item["contentUnitId"] for item in passed["assignments"]] == [
        "unit-title",
        "unit-body",
    ]
    assert planned_slots == slots

    oversized = _unit("unit-body-too-large", 1, "문장" * 400)
    rejected, _, _ = build_composition_plan(
        narrative=narrative,
        slide=SimpleNamespace(
            slide_key="slide-001",
            sequence_index=0,
            content_unit_ids=("unit-title", "unit-body-too-large"),
            sealed_content_units=(units[0], oversized),
        ),
        frame=frame,
    )
    assert rejected["status"] == "failed"
    assert rejected["unmappedRequiredUnits"] == ["unit-body-too-large"]


def test_source_flow_does_not_merge_same_role_across_pack_containers() -> None:
    frame = _frame()
    body_slots = [
        item for item in frame["slots"] if item["semanticRole"] == "body"
    ]
    body_slots[0]["containerId"] = "white-panel"
    body_slots[1]["containerId"] = "dark-panel"

    slots = source_flow_slots(frame)
    body_flows = [
        item
        for item in slots
        if item.get("sourceFlowLayout") == "text"
        and item.get("sourceFlowRole") == "body"
    ]

    assert len(body_flows) == 2
    assert {item["containerId"] for item in body_flows} == {
        "white-panel",
        "dark-panel",
    }
    assert all(len(item["sourceFlowMemberSlotIds"]) == 1 for item in body_flows)


def test_source_flow_promotes_authored_container_graph_instead_of_leaf_slots() -> None:
    frame = {
        "frameId": "arbitrary-frame",
        "sourceDigest": "sha256:" + "f" * 64,
        "canvasBounds": {"x": 0, "y": 0, "width": 1280, "height": 720},
        "slots": [],
        "chrome": [
            {
                "elementId": "content",
                "selector": "html:arbitrary-frame#content",
                "containerId": "html:arbitrary-frame",
                "kind": "text",
                "bounds": {"x": 72, "y": 150, "width": 1136, "height": 500},
            },
            *[
                {
                    "elementId": f"card-{index}",
                    "selector": f"html:arbitrary-frame#card-{index}",
                    "containerId": "html:arbitrary-frame#content",
                    "kind": "text",
                    "bounds": {
                        "x": 90 + (index % 4) * 275,
                        "y": 175 + (index // 4) * 220,
                        "width": 250,
                        "height": 190,
                    },
                }
                for index in range(8)
            ],
        ],
    }
    for index in range(8):
        slot = _slot(
            f"slot:leaf-{index}",
            "body",
            "text",
            {
                "x": 105 + (index % 4) * 275,
                "y": 195 + (index // 4) * 220,
                "width": 220,
                "height": 150,
            },
            maximum=120,
        )
        slot["containerId"] = f"html:arbitrary-frame#card-{index}"
        frame["slots"].append(slot)

    slots = source_flow_slots(frame)
    active = [item for item in slots if item.get("sourceFlowLayout")]

    assert len(active) == 1
    assert active[0]["containerId"] == "html:arbitrary-frame#content"
    assert active[0]["sourceFlowSurface"] == "container-inherited"
    assert active[0]["sourceFlowContainerGraphVersion"] == "v1"
    assert len([item for item in slots if item.get("sourceFlowSuppressedBy")]) == 7
