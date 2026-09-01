"""Digest-bound capacity admission before source-verbatim provider use."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from .composition_capacity import (
    _mapping,
    _value,
    allocation_quality_score,
    allocate_units,
    canonical_digest,
    executable_slots,
    largest_fitting_text_scale,
    source_flow_slots,
    unit_text,
)


SOURCE_VERBATIM_CAPACITY_POLICY_V1: dict[str, object] = {
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


class CapacityAdmissionError(ValueError):
    def __init__(self, code: str, receipt: Mapping[str, Any]) -> None:
        super().__init__(code)
        self.code = code
        self.receipt = dict(receipt)


@dataclass(frozen=True)
class _AdmissionUnit:
    content_unit_id: str
    content_digest: str
    kind: str
    required: bool
    text: str
    data: object
    source_page_index: int
    source_unit_index: int
    semantic_role: str


def _semantic_role(
    *, value: object, payload: Mapping[str, Any], kind: str, index: int
) -> str:
    explicit = str(
        _value(value, "semantic_role", "semanticRole", default="")
        or payload.get("semanticRole")
        or payload.get("semantic_role")
        or payload.get("boundaryKind")
        or payload.get("boundary_kind")
        or ""
    ).lower()
    content_unit_id = str(
        _value(value, "content_unit_id", "contentUnitId", default="") or ""
    ).lower()
    evidence = f"{explicit} {content_unit_id} {kind.lower()}"
    if any(token in evidence for token in ("title", "headline", "heading")):
        return "title"
    if any(token in evidence for token in ("table-cell", "table_cell", "cell")):
        return "table-cell"
    if kind == "image" or any(
        token in evidence for token in ("image", "photo", "picture", "media")
    ):
        return "image"
    if kind in {"table", "chart", "metric", "number", "data"} or any(
        token in evidence for token in ("table", "chart", "metric", "number", "data")
    ):
        return "data"
    if explicit:
        return explicit
    return "title" if index == 0 and kind == "text" else "body"


def _admission_units(page: object, page_index: int) -> list[_AdmissionUnit]:
    raw_units = _value(page, "content_units", "contentUnits", default=()) or ()
    units: list[_AdmissionUnit] = []
    for index, value in enumerate(raw_units):
        payload = _mapping(_value(value, "payload", default={}))
        kind = str(_value(value, "kind", default="text") or "text")
        data = _value(value, "data", default=None)
        if data in (None, [], {}):
            data = payload.get("data")
        if kind != "text" and data in (None, [], {}):
            data = payload
        units.append(
            _AdmissionUnit(
                content_unit_id=str(
                    _value(value, "content_unit_id", "contentUnitId", default="")
                    or ""
                ),
                content_digest=str(
                    _value(value, "content_digest", "contentDigest", default="")
                    or ""
                ),
                kind=kind,
                required=bool(_value(value, "required", default=True)),
                text=str(
                    _value(value, "text", default=payload.get("text", "")) or ""
                ),
                data=data,
                source_page_index=int(
                    _value(
                        value,
                        "source_page_index",
                        "sourcePageIndex",
                        default=page_index,
                    )
                    or 0
                ),
                source_unit_index=int(
                    _value(
                        value,
                        "source_unit_index",
                        "sourceUnitIndex",
                        default=index,
                    )
                    or 0
                ),
                semantic_role=_semantic_role(
                    value=value,
                    payload=payload,
                    kind=kind,
                    index=index,
                ),
            )
        )
    return units


def _role_group(role: object, *, kind: str = "") -> str:
    evidence = f"{str(role or '').lower()} {kind.lower()}"
    if any(token in evidence for token in ("title", "headline", "heading")):
        return "title"
    if any(token in evidence for token in ("table-cell", "table_cell", "cell")):
        return "table-cell"
    if kind == "image" or any(
        token in evidence for token in ("image", "photo", "picture", "media")
    ):
        return "image"
    if kind in {"table", "chart", "metric", "number", "data"} or any(
        token in evidence for token in ("table", "chart", "metric", "number", "data")
    ):
        return "data"
    return "body"


def _positive_float(value: object) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    return resolved if math.isfinite(resolved) and resolved > 0 else None


def _image_aspect(data: object) -> float | None:
    if not isinstance(data, Mapping):
        return None
    aspect = _positive_float(
        data.get("aspectRatio", data.get("aspect_ratio"))
    )
    if aspect is not None:
        return aspect
    width = _positive_float(data.get("width"))
    height = _positive_float(data.get("height"))
    if width is not None and height is not None:
        return width / height
    payload = data.get("payload")
    return _image_aspect(payload) if isinstance(payload, Mapping) else None


def _content_features(units: Sequence[_AdmissionUnit]) -> dict[str, Any]:
    role_counts: dict[str, int] = {}
    text_units = [item for item in units if item.kind == "text"]
    images = [item for item in units if item.kind == "image"]
    for item in units:
        group = _role_group(item.semantic_role, kind=item.kind)
        role_counts[group] = role_counts.get(group, 0) + 1
    aspects = sorted(
        aspect
        for aspect in (_image_aspect(item.data) for item in images)
        if aspect is not None
    )
    table_cells = role_counts.get("table-cell", 0)
    data_units = role_counts.get("data", 0) + table_cells
    text_characters = sum(len(unit_text(item)) for item in text_units)
    return {
        "requiredUnitCount": sum(1 for item in units if item.required),
        "textUnitCount": len(text_units),
        "textCharacters": text_characters,
        "maximumTextCharacters": max(
            (len(unit_text(item)) for item in text_units), default=0
        ),
        "imageOccurrenceCount": len(images),
        "imageAspectRatios": [round(item, 6) for item in aspects],
        "dataUnitCount": data_units,
        "tableCellCount": table_cells,
        "semanticRoleCounts": dict(sorted(role_counts.items())),
    }


def _frame_features(frame: object) -> dict[str, Any]:
    slots = executable_slots(frame)
    role_counts: dict[str, int] = {}
    text_slots: list[Mapping[str, Any]] = []
    image_slots: list[Mapping[str, Any]] = []
    data_slots: list[Mapping[str, Any]] = []
    image_aspects: list[float] = []
    for slot in slots:
        accepted = {str(item).lower() for item in slot["acceptedKinds"]}
        role = _role_group(slot["semanticRole"])
        role_counts[role] = role_counts.get(role, 0) + 1
        if accepted & {"text", "title", "body", "quote"}:
            text_slots.append(slot)
        if "image" in accepted:
            image_slots.append(slot)
            bounds = slot.get("bounds") or {}
            width = _positive_float(bounds.get("width"))
            height = _positive_float(bounds.get("height"))
            if width is not None and height is not None:
                image_aspects.append(width / height)
        if accepted & {"table", "chart", "metric", "number", "data"} or role in {
            "data",
            "table-cell",
        }:
            data_slots.append(slot)

    def envelope_total(name: str, values: Sequence[Mapping[str, Any]]) -> int:
        return sum(
            max(0, int((item.get("capacityEnvelope") or {}).get(name, 0) or 0))
            for item in values
        )

    image_capacity = sum(
        max(
            1,
            int(
                (item.get("capacityEnvelope") or {}).get("maxImages", 1)
                or 1
            ),
        )
        for item in image_slots
    )
    return {
        "slotCount": len(slots),
        "textSlotCount": len(text_slots),
        "textCharacterCapacity": envelope_total("maxCharacters", text_slots),
        "textLineCapacity": envelope_total("maxLines", text_slots),
        "imageSlotCount": len(image_slots),
        "imageCapacity": image_capacity,
        "imageAspectRatios": [round(item, 6) for item in sorted(image_aspects)],
        "dataSlotCount": len(data_slots),
        "semanticRoleCounts": dict(sorted(role_counts.items())),
        "meanExtractionConfidence": round(
            sum(float(item["extractionConfidence"]) for item in slots)
            / max(1, len(slots)),
            6,
        ),
    }


def _normalized_gap(observed: int, available: int) -> float:
    if observed <= 0:
        return 0.0 if available <= 0 else min(1.0, available * 0.05)
    if available <= 0:
        return 4.0
    return abs(observed - available) / max(observed, available)


def _frame_fit_quality(
    *,
    content: Mapping[str, Any],
    frame: Mapping[str, Any],
    slots: Sequence[Mapping[str, Any]],
    assigned: Mapping[str, Sequence[object]],
) -> dict[str, Any]:
    content_roles = content["semanticRoleCounts"]
    frame_roles = frame["semanticRoleCounts"]
    semantic_penalty = 0.0
    for role, count in content_roles.items():
        if count and not frame_roles.get(role, 0):
            semantic_penalty += 1.0

    data_count = int(content["dataUnitCount"])
    data_penalty = 0.0 if not data_count else (
        0.0 if int(frame["dataSlotCount"]) else 1.0
    )
    text_characters = int(content["textCharacters"])
    text_capacity = int(frame["textCharacterCapacity"])
    if text_characters and not text_capacity:
        text_volume_penalty = 4.0
    elif not text_characters:
        text_volume_penalty = 0.0
    else:
        utilization = text_characters / max(1, text_capacity)
        text_volume_penalty = (
            2.0 + (utilization - 1.0) * 4.0
            if utilization > 1.0
            else abs(utilization - 0.60) * 0.25
        )

    image_count = int(content["imageOccurrenceCount"])
    image_slots = int(frame["imageSlotCount"])
    image_capacity = int(frame["imageCapacity"])
    image_count_penalty = _normalized_gap(image_count, image_slots)
    if image_count > image_capacity:
        image_count_penalty += 2.0 + (
            (image_count - image_capacity) / max(1, image_count)
        )

    source_aspects = [float(item) for item in content["imageAspectRatios"]]
    frame_aspects = [float(item) for item in frame["imageAspectRatios"]]
    if source_aspects and not frame_aspects:
        image_aspect_penalty = 4.0
    elif not source_aspects:
        image_aspect_penalty = 0.0
    else:
        image_aspect_penalty = sum(
            min(abs(math.log(source / candidate)) for candidate in frame_aspects)
            for source in source_aspects
        ) / len(source_aspects)

    matching_slots = int(frame["textSlotCount"]) + image_slots + int(
        frame["dataSlotCount"]
    )
    density_penalty = _normalized_gap(
        int(content["requiredUnitCount"]), matching_slots
    )
    text_scales = [
        largest_fitting_text_scale(slot, assigned[str(slot["slotId"])])
        for slot in slots
        if assigned[str(slot["slotId"])]
        and "text" in slot["acceptedKinds"]
    ]
    readability_slack = min(
        (float(item) for item in text_scales if item is not None), default=1.0
    )
    readability_penalty = max(0.0, 1.0 - readability_slack)
    confidence_penalty = max(
        0.0, 1.0 - float(frame["meanExtractionConfidence"])
    )
    rank = [
        semantic_penalty,
        data_penalty,
        image_count_penalty,
        image_aspect_penalty,
        text_volume_penalty,
        density_penalty,
        readability_penalty,
        confidence_penalty,
    ]
    return {
        "semanticRolePenalty": round(semantic_penalty, 6),
        "dataRepresentationPenalty": round(data_penalty, 6),
        "imageCountPenalty": round(image_count_penalty, 6),
        "imageAspectPenalty": round(image_aspect_penalty, 6),
        "textVolumePenalty": round(text_volume_penalty, 6),
        "densityPenalty": round(density_penalty, 6),
        "readabilitySlack": round(readability_slack, 6),
        "readabilityPenalty": round(readability_penalty, 6),
        "extractionConfidencePenalty": round(confidence_penalty, 6),
        "fitQualityRank": [round(float(item), 6) for item in rank],
    }


def build_source_verbatim_capacity_admission(
    *,
    content_graph: object,
    frames: Sequence[object],
    frame_structure_digest: str,
    expected_slide_count: int,
) -> dict[str, Any]:
    """Seal a provider-free, same-page-only capacity admission receipt."""

    pages = list(_value(content_graph, "pages", default=()) or ())
    page_order = [
        str(_value(page, "page_id", "pageId", default="") or "")
        for page in pages
    ]
    content_graph_digest = str(
        _value(
            content_graph,
            "content_graph_digest",
            "contentGraphDigest",
            default="",
        )
        or ""
    )
    page_receipts: list[dict[str, Any]] = []
    contract_error = None
    if expected_slide_count <= 0 or len(pages) != expected_slide_count:
        contract_error = "source_verbatim_page_count_mismatch"
    elif not frames:
        contract_error = "source_verbatim_candidate_frames_missing"
    elif len(page_order) != len(set(page_order)) or not all(page_order):
        contract_error = "source_verbatim_page_order_invalid"

    candidate_frame_set = []
    for frame in frames:
        slots = source_flow_slots(frame)
        candidate_frame_set.append(
            {
                "frameId": str(
                    _value(frame, "frame_id", "frameId", default="") or ""
                ),
                "frameDigest": str(
                    _value(frame, "source_digest", "sourceDigest", default="")
                    or ""
                ),
                "executableSlots": [
                    {
                        "slotId": item["slotId"],
                        "semanticRole": item["semanticRole"],
                        "acceptedKinds": item["acceptedKinds"],
                        "required": item["required"],
                        "capacityEnvelope": item["capacityEnvelope"],
                        "patchHandle": item["patchHandle"],
                        "bounds": item["bounds"],
                        "extractionConfidence": item["extractionConfidence"],
                    }
                    for item in slots
                ],
            }
        )
    candidate_frame_set.sort(
        key=lambda item: (item["frameDigest"], item["frameId"])
    )

    selected_frame_use_counts: dict[tuple[str, str], int] = {}
    previous_selected_frame: tuple[str, str] | None = None
    if contract_error is None:
        for page_index, page in enumerate(pages):
            units = _admission_units(page, page_index)
            content_features = _content_features(units)
            content_feature_digest = canonical_digest(content_features)
            required_ids = {
                item.content_unit_id for item in units if item.required
            }
            invalid_order = any(
                item.source_page_index != page_index
                or item.source_unit_index != index
                for index, item in enumerate(units)
            )
            evaluations: list[dict[str, Any]] = []
            for frame_index, frame in enumerate(frames):
                slots = source_flow_slots(frame)
                native_frame_features = _frame_features(frame)
                assigned, assignments, unmapped = allocate_units(units, slots)
                mapped_ids = [
                    str(item["contentUnitId"]) for item in assignments
                ]
                duplicates = sorted(
                    {item for item in mapped_ids if mapped_ids.count(item) > 1}
                    & required_ids
                )
                missing_required_slots = sorted(
                    str(item["slotId"])
                    for item in slots
                    if item["required"] and not assigned[str(item["slotId"])]
                )
                unmapped_required = sorted(required_ids & set(unmapped))
                semantic_penalty, capacity_slack = allocation_quality_score(
                    units, slots, assigned, assignments
                )
                fit_quality = _frame_fit_quality(
                    content=content_features,
                    frame=native_frame_features,
                    slots=slots,
                    assigned=assigned,
                )
                hard_pass = bool(
                    units
                    and not unmapped
                    and not duplicates
                    and not missing_required_slots
                    and not invalid_order
                )
                assignment_digest = canonical_digest(assignments)
                evaluation_unsigned = {
                    "candidateIndex": frame_index,
                    "frameId": str(
                        _value(frame, "frame_id", "frameId", default="") or ""
                    ),
                    "frameDigest": str(
                        _value(
                            frame,
                            "source_digest",
                            "sourceDigest",
                            default="",
                        )
                        or ""
                    ),
                    "status": "passed" if hard_pass else "failed",
                    "semanticPenalty": semantic_penalty,
                    "capacitySlack": capacity_slack,
                    "contentFeatureDigest": content_feature_digest,
                    "nativeFrameFeatureDigest": canonical_digest(
                        native_frame_features
                    ),
                    **fit_quality,
                    "assignmentDigest": assignment_digest,
                    "unmappedRequiredUnits": unmapped_required,
                    "duplicateRequiredUnits": duplicates,
                    "missingRequiredSlots": missing_required_slots,
                }
                evaluations.append(
                    {
                        **evaluation_unsigned,
                        "evaluationDigest": canonical_digest(evaluation_unsigned),
                        "assignments": assignments,
                    }
                )
            hard_pass_candidates = [
                item for item in evaluations if item["status"] == "passed"
            ]

            def candidate_rank(item: Mapping[str, Any]) -> tuple[object, ...]:
                frame_key = (str(item["frameDigest"]), str(item["frameId"]))
                return (
                    *tuple(float(value) for value in item["fitQualityRank"]),
                    int(item["semanticPenalty"]),
                    abs(float(item["capacitySlack"])),
                    selected_frame_use_counts.get(frame_key, 0),
                    1 if frame_key == previous_selected_frame else 0,
                    frame_key[0],
                    frame_key[1],
                    str(item["assignmentDigest"]),
                )

            hard_pass_candidates.sort(
                key=candidate_rank
            )
            selected = hard_pass_candidates[0] if hard_pass_candidates else None
            selected_variety_use_count: int | None = None
            selected_variety_immediate_repeat: bool | None = None
            if selected is None:
                diagnostic_candidates = sorted(
                    evaluations,
                    key=lambda item: (
                        len(item["unmappedRequiredUnits"]),
                        len(item["missingRequiredSlots"]),
                        len(item["duplicateRequiredUnits"]),
                        item["semanticPenalty"],
                        item["frameDigest"],
                        item["frameId"],
                    ),
                )
                diagnostic = diagnostic_candidates[0]
            else:
                diagnostic = selected
                selected_frame_key = (
                    str(selected["frameDigest"]),
                    str(selected["frameId"]),
                )
                selected_variety_use_count = (
                    selected_frame_use_counts.get(selected_frame_key, 0)
                )
                selected_variety_immediate_repeat = (
                    selected_frame_key == previous_selected_frame
                )
                selected_frame_use_counts[selected_frame_key] = (
                    selected_frame_use_counts.get(selected_frame_key, 0) + 1
                )
                previous_selected_frame = selected_frame_key
            evaluation_receipts = [
                {key: value for key, value in item.items() if key != "assignments"}
                for item in evaluations
            ]
            evaluation_receipts.sort(
                key=lambda item: (item["frameDigest"], item["frameId"])
            )
            page_unsigned = {
                "sourcePageIndex": page_index,
                "pageId": page_order[page_index],
                "pageDigest": str(
                    _value(page, "page_digest", "pageDigest", default="") or ""
                ),
                "candidateFrameCount": len(frames),
                "hardPassCandidateCount": len(hard_pass_candidates),
                "contentFeatures": content_features,
                "contentFeatureDigest": content_feature_digest,
                "candidateEvaluationDigest": canonical_digest(
                    evaluation_receipts
                ),
                "selectedFrameId": (
                    selected["frameId"] if selected is not None else None
                ),
                "selectedFrameDigest": (
                    selected["frameDigest"] if selected is not None else None
                ),
                "selectedSemanticPenalty": (
                    selected["semanticPenalty"] if selected is not None else None
                ),
                "selectedCapacitySlack": (
                    selected["capacitySlack"] if selected is not None else None
                ),
                "selectedFitQualityRank": (
                    selected["fitQualityRank"] if selected is not None else None
                ),
                "selectedReadabilitySlack": (
                    selected["readabilitySlack"] if selected is not None else None
                ),
                "selectedVarietyUseCountBeforeSelection": (
                    selected_variety_use_count
                ),
                "selectedVarietyImmediateRepeat": (
                    selected_variety_immediate_repeat
                ),
                "assignmentDigest": (
                    selected["assignmentDigest"] if selected is not None else None
                ),
                "expectedContentUnitIds": [
                    item.content_unit_id for item in units
                ],
                "expectedContentDigests": [
                    item.content_digest for item in units
                ],
                "assignments": selected["assignments"] if selected else [],
                "unmappedRequiredUnits": diagnostic[
                    "unmappedRequiredUnits"
                ],
                "duplicateRequiredUnits": diagnostic[
                    "duplicateRequiredUnits"
                ],
                "missingRequiredSlots": diagnostic["missingRequiredSlots"],
                "sourceOrderError": invalid_order,
                "status": "passed" if selected is not None else "failed",
            }
            page_receipts.append(
                {
                    **page_unsigned,
                    "pageAdmissionDigest": canonical_digest(page_unsigned),
                }
            )

    status = (
        "passed"
        if contract_error is None
        and len(page_receipts) == expected_slide_count
        and all(item["status"] == "passed" for item in page_receipts)
        else "failed"
    )
    failure_code = contract_error
    if failure_code is None and status == "failed":
        failure_code = "source_verbatim_same_page_capacity_exceeded"
    unsigned = {
        "schemaVersion": "source-verbatim-capacity-admission/v1",
        "status": status,
        "failureCode": failure_code,
        "contentGraphDigest": content_graph_digest,
        "frameStructureDigest": frame_structure_digest,
        "candidateFrameStructureDigest": frame_structure_digest,
        "candidateFrameSetDigest": canonical_digest(candidate_frame_set),
        "candidateFrameCount": len(frames),
        "expectedSlideCount": expected_slide_count,
        "sourcePageCount": len(pages),
        "outputSlideCount": expected_slide_count,
        "sourcePageOrder": page_order,
        "policy": dict(SOURCE_VERBATIM_CAPACITY_POLICY_V1),
        "policyDigest": canonical_digest(SOURCE_VERBATIM_CAPACITY_POLICY_V1),
        "pages": page_receipts,
    }
    return {**unsigned, "capacityAdmissionDigest": canonical_digest(unsigned)}


def assert_source_verbatim_capacity_admission(
    receipt: Mapping[str, Any],
) -> None:
    if receipt.get("status") != "passed":
        code = str(
            receipt.get("failureCode")
            or "source_verbatim_same_page_capacity_exceeded"
        )
        raise CapacityAdmissionError(code, receipt)


__all__ = [
    "CapacityAdmissionError",
    "SOURCE_VERBATIM_CAPACITY_POLICY_V1",
    "assert_source_verbatim_capacity_admission",
    "build_source_verbatim_capacity_admission",
]
