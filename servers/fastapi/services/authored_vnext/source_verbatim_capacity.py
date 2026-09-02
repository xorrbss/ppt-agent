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
from .source_render_geometry import (
    BODY_FONT_FLOOR_PX,
    FLOW_ICON_HEIGHT_PX,
    FLOW_MEDIA_HEIGHT_PX,
    FLOW_PROCESS_MEDIA_HEIGHT_PX,
    FOOTER_BODY_GAP_PX,
    FOOTER_LANE_HEIGHT_PX,
    RenderUnit,
    TEXT_LINE_BOX_BREATHING_PX,
    TITLE_FONT_FLOOR_PX,
    TITLE_STACK_GAP_PX,
    flow_font_px,
    footer_lane_item_height,
    is_meaningful_image,
    semantic_block_height,
    semantic_text_metrics,
    slot_declared_font_px,
    table_matrix_shape,
    text_advance_px,
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


# The render geometry model is canonical; these aliases retain the private
# names that existing call sites import.
_BODY_FONT_FLOOR_PX = BODY_FONT_FLOOR_PX
_TITLE_FONT_FLOOR_PX = TITLE_FONT_FLOOR_PX
_FOOTER_LANE_HEIGHT_PX = FOOTER_LANE_HEIGHT_PX
_FOOTER_BODY_GAP_PX = FOOTER_BODY_GAP_PX
_TEXT_LINE_BOX_BREATHING_PX = TEXT_LINE_BOX_BREATHING_PX
_FLOW_ICON_HEIGHT_PX = FLOW_ICON_HEIGHT_PX
_FLOW_MEDIA_HEIGHT_PX = FLOW_MEDIA_HEIGHT_PX
_FLOW_PROCESS_MEDIA_HEIGHT_PX = FLOW_PROCESS_MEDIA_HEIGHT_PX
_TITLE_STACK_GAP_PX = TITLE_STACK_GAP_PX
_SLIDE_WIDTH_PX = 1280.0
_SLIDE_HEIGHT_PX = 720.0
_MEANINGFUL_IMAGE_MIN_WIDTH_PX = 480.0
_MEANINGFUL_IMAGE_MIN_HEIGHT_PX = 270.0
_MEANINGFUL_IMAGE_MIN_AREA_RATIO = 0.140625
_MEANINGFUL_IMAGE_SCALE_FLOOR = 0.33
_COMPONENT_OVERLAY_PADDING_PX = 8.0
_COMPONENT_VISUAL_GAP_PX = 8.0


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
    semantic_group_kind: str
    semantic_group_id: str
    table_row: int | None
    table_column: int | None
    table_row_span: int
    table_column_span: int
    table_header: bool
    image_kind: str
    image_aspect_ratio: float | None


def _semantic_key(value: object) -> str:
    key = str(value or "").strip().replace("_", "-").lower()
    aliases = {
        "semanticrole": "role",
        "semantic-role": "role",
        "semanticgroupkind": "group-kind",
        "semantic-group-kind": "group-kind",
        "semanticgroupid": "group-id",
        "semantic-group-id": "group-id",
        "semantictablerow": "table-row",
        "semantic-table-row": "table-row",
        "semantictablecolumn": "table-column",
        "semantic-table-column": "table-column",
        "semantictablerowspan": "table-row-span",
        "semantic-table-row-span": "table-row-span",
        "semantictablecolumnspan": "table-column-span",
        "semantic-table-column-span": "table-column-span",
        "semantictableheader": "table-header",
        "semantic-table-header": "table-header",
        "semanticimageaspectratio": "image-aspect-ratio",
        "semantic-image-aspect-ratio": "image-aspect-ratio",
        "semanticimagekind": "image-kind",
        "semantic-image-kind": "image-kind",
    }
    if key.startswith("source-semantic."):
        key = key.removeprefix("source-semantic.")
    elif key.startswith("source-semantic-"):
        key = key.removeprefix("source-semantic-")
    return aliases.get(key, key)


def _normalized_source_semantic(
    *, payload: Mapping[str, Any], data: object
) -> dict[str, object]:
    """Read normalized metadata while accepting legacy payload.data shapes."""

    resolved: dict[str, object] = {}

    def merge(value: object) -> None:
        if not isinstance(value, Mapping):
            return
        nested = value.get(
            "source-semantic",
            value.get("sourceSemantic", value.get("source_semantic")),
        )
        if isinstance(nested, Mapping):
            for key, item in nested.items():
                resolved[_semantic_key(key)] = item
        datum_key = value.get(
            "datumKey", value.get("datum_key", value.get("key"))
        )
        if datum_key is not None and "value" in value:
            resolved[_semantic_key(datum_key)] = value["value"]
        for key, item in value.items():
            normalized = _semantic_key(key)
            if normalized in {
                "role",
                "group-kind",
                "group-id",
                "table-row",
                "table-column",
                "table-row-span",
                "table-column-span",
                "table-header",
                "image-aspect-ratio",
                "image-kind",
            }:
                resolved[normalized] = item

    merge(payload)
    if isinstance(data, Sequence) and not isinstance(
        data, (str, bytes, bytearray)
    ):
        for item in data:
            merge(item)
    else:
        merge(data)
    return resolved


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return None
    return resolved if resolved >= 0 else None


def _positive_int(value: object, default: int = 1) -> int:
    resolved = _optional_nonnegative_int(value)
    return resolved if resolved is not None and resolved > 0 else default


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


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
    if any(
        token in evidence
        for token in (
            "table-cell",
            "table_cell",
            "table-header",
            "table-row-header",
            "cell",
        )
    ):
        return "table-cell"
    if explicit and explicit not in {"text", "paragraph", "body", "media"}:
        return explicit
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
        source_semantic = _normalized_source_semantic(
            payload=payload,
            data=data,
        )
        explicit_semantic_role = str(source_semantic.get("role") or "").strip()
        semantic_role = _semantic_role(
            value=value,
            payload={
                **payload,
                **(
                    {"semanticRole": explicit_semantic_role}
                    if explicit_semantic_role
                    else {}
                ),
            },
            kind=kind,
            index=index,
        )
        image_aspect_ratio = _positive_float(
            source_semantic.get("image-aspect-ratio")
        )
        if image_aspect_ratio is None:
            image_aspect_ratio = _image_aspect(data)
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
                semantic_role=semantic_role,
                semantic_group_kind=str(
                    source_semantic.get("group-kind") or ""
                ).strip(),
                semantic_group_id=str(
                    source_semantic.get("group-id") or ""
                ).strip(),
                table_row=_optional_nonnegative_int(
                    source_semantic.get("table-row")
                ),
                table_column=_optional_nonnegative_int(
                    source_semantic.get("table-column")
                ),
                table_row_span=_positive_int(
                    source_semantic.get("table-row-span")
                ),
                table_column_span=_positive_int(
                    source_semantic.get("table-column-span")
                ),
                table_header=_boolean(source_semantic.get("table-header")),
                image_kind=str(source_semantic.get("image-kind") or "").strip(),
                image_aspect_ratio=image_aspect_ratio,
            )
        )
    return units


def _role_group(role: object, *, kind: str = "") -> str:
    evidence = f"{str(role or '').lower()} {kind.lower()}"
    if any(token in evidence for token in ("title", "headline", "heading")):
        return "title"
    if any(
        token in evidence
        for token in (
            "table-cell",
            "table_cell",
            "table-header",
            "table-row-header",
            "cell",
        )
    ):
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
        for aspect in (item.image_aspect_ratio for item in images)
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
        "meaningfulImageCount": sum(
            1 for item in images if _is_meaningful_image(item)
        ),
        "imageAspectRatios": [round(item, 6) for item in aspects],
        "dataUnitCount": data_units,
        "tableCellCount": table_cells,
        "fixedTableMatrixCount": len(
            {
                item.semantic_group_id
                for item in units
                if item.table_row is not None
                and item.table_column is not None
                and item.semantic_group_id
            }
        ),
        "semanticGroupCount": len(
            {item.semantic_group_id for item in units if item.semantic_group_id}
        ),
        "compactFooterUnitCount": sum(
            1 for item in units if _is_footer_unit(item)
        ),
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


def _slot_number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    return resolved if math.isfinite(resolved) else default


def _renderer_font_px(
    slot: Mapping[str, Any], *, role: str, adaptive_scale: float
) -> float:
    return flow_font_px(
        slot_declared_font_px(slot), role=role, adaptive_scale=adaptive_scale
    )


def _render_unit(unit: _AdmissionUnit) -> RenderUnit:
    """Project an admission unit onto the shared render geometry model."""

    return RenderUnit(
        text=unit_text(unit),
        kind=unit.kind,
        semantic_role=unit.semantic_role,
        group_kind=unit.semantic_group_kind,
        image_kind=unit.image_kind or "media",
        image_aspect=unit.image_aspect_ratio,
        table_row=unit.table_row,
        table_column=unit.table_column,
        table_row_span=unit.table_row_span,
        table_column_span=unit.table_column_span,
    )


def _is_footer_unit(unit: _AdmissionUnit) -> bool:
    evidence = (
        f"{unit.semantic_role} {unit.semantic_group_kind} {unit.image_kind}"
    ).lower()
    return "footer" in evidence


def _footer_lane_width(units: Sequence[_AdmissionUnit]) -> float:
    """Advance width of the nowrap footer lane including its 16px item gaps."""

    metrics = semantic_text_metrics(
        semantic_role="compact-footer",
        group_kind="compact-footer",
        fallback_role="body",
        declared_font_px=0.0,
        adaptive_scale=1.0,
    )
    return sum(
        text_advance_px(unit_text(item), font_px=metrics.font_px)
        for item in units
    ) + _FOOTER_BODY_GAP_PX * max(0, len(units) - 1)


def _is_meaningful_image(unit: _AdmissionUnit) -> bool:
    """One predicate with the renderer, so the lanes cannot classify apart."""

    return is_meaningful_image(_render_unit(unit))


def _table_matrix_shape(
    block: Sequence[_AdmissionUnit],
) -> tuple[int, int] | None:
    return table_matrix_shape([_render_unit(item) for item in block])


def _renderer_blocks(
    units: Sequence[_AdmissionUnit],
) -> list[list[_AdmissionUnit]]:
    blocks: list[list[_AdmissionUnit]] = []
    index = 0
    while index < len(units):
        unit = units[index]
        boundary = _role_group(unit.semantic_role, kind=unit.kind)
        end = index + 1
        if boundary == "table-cell":
            while end < len(units) and _role_group(
                units[end].semantic_role, kind=units[end].kind
            ) == "table-cell":
                end += 1
        elif unit.semantic_group_id:
            while (
                end < len(units)
                and units[end].semantic_group_id == unit.semantic_group_id
            ):
                end += 1
        blocks.append(list(units[index:end]))
        index = end
    return blocks


def _estimated_renderer_block_height(
    block: Sequence[_AdmissionUnit],
    *,
    slot: Mapping[str, Any],
    adaptive_scale: float,
    role: str,
    column_width: float,
) -> float:
    """Delegate to the canonical model so admission cannot outrun rendering."""

    return semantic_block_height(
        [_render_unit(item) for item in block],
        column_width=column_width,
        declared_font_px=slot_declared_font_px(slot),
        adaptive_scale=adaptive_scale,
        fallback_role=role,
        table_block=_role_group(block[0].semantic_role, kind=block[0].kind)
        == "table-cell",
    )


def _balanced_renderer_ends(
    weights: Sequence[float], columns: int
) -> tuple[int, ...]:
    prefix = [0.0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)
    states: dict[tuple[int, int], tuple[float, float, tuple[int, ...]]] = {}
    for end in range(1, len(weights) + 1):
        load = prefix[end]
        states[(1, end)] = (load, load * load, (end,))
    for count in range(2, columns + 1):
        for end in range(count, len(weights) + 1):
            candidates = []
            for split in range(count - 1, end):
                previous = states[(count - 1, split)]
                load = prefix[end] - prefix[split]
                candidates.append(
                    (
                        max(previous[0], load),
                        previous[1] + load * load,
                        previous[2] + (end,),
                    )
                )
            states[(count, end)] = min(
                candidates,
                key=lambda item: (
                    round(item[0], 6),
                    round(item[1], 6),
                    item[2],
                ),
            )
    return states[(columns, len(weights))][2]


def _renderer_text_geometry(
    *,
    slot: Mapping[str, Any],
    units: Sequence[_AdmissionUnit],
    mixed: bool = False,
    width_fraction: float = 1.0,
    height_fraction: float = 1.0,
) -> tuple[bool, float, float, int, str | None]:
    bounds = _mapping(slot.get("bounds"))
    spacing = _mapping(slot.get("spacing"))
    width = _slot_number(bounds.get("width"))
    height = _slot_number(bounds.get("height"))
    if width <= 0 or height <= 0:
        return False, 0.0, math.inf, 0, "source_native_flow_geometry_invalid"
    # Source-flow rendering deliberately skips the legacy character/line
    # pre-gate and lets the exact bounded geometry search start at 1.0.
    adaptive_scale = 1.0
    content_width = max(
        1.0,
        width
        - max(0.0, _slot_number(spacing.get("insetLeft")))
        - max(0.0, _slot_number(spacing.get("insetRight"))),
    )
    usable_height = max(
        1.0,
        height
        - max(0.0, _slot_number(spacing.get("insetTop")))
        - max(0.0, _slot_number(spacing.get("insetBottom"))),
    ) * max(0.05, min(1.0, height_fraction))
    content_width *= max(0.05, min(1.0, width_fraction))
    footer_units = [item for item in units if _is_footer_unit(item)]
    body_units = [item for item in units if not _is_footer_unit(item)]
    if footer_units:
        footer_width = _footer_lane_width(footer_units)
        if (
            footer_width > content_width
            # The lane is a fixed `height:23px` box with `overflow:hidden`.
            or footer_lane_item_height() > _FOOTER_LANE_HEIGHT_PX
            or usable_height
            < (
                _FOOTER_LANE_HEIGHT_PX + _FOOTER_BODY_GAP_PX
                if body_units
                else _FOOTER_LANE_HEIGHT_PX
            )
        ):
            return (
                False,
                1.0,
                math.inf,
                0,
                "source_native_footer_capacity_exceeded",
            )
        if not body_units:
            return True, 1.0, _FOOTER_LANE_HEIGHT_PX / usable_height, 1, None
        usable_height -= _FOOTER_LANE_HEIGHT_PX + _FOOTER_BODY_GAP_PX
    target_height = usable_height
    hints = _mapping(slot.get("layoutHints"))
    column_gap = max(
        0.0,
        14.0 if mixed else _slot_number(hints.get("columnGap"), 18.0),
    )
    role = str(
        slot.get("sourceFlowRole") or slot.get("semanticRole") or "body"
    ).strip().lower()
    blocks = _renderer_blocks(body_units)
    if not blocks:
        return True, 1.0, 0.0, 1, None
    try:
        fixed_table_matrix = any(
            _table_matrix_shape(block) is not None
            for block in blocks
            if _role_group(block[0].semantic_role, kind=block[0].kind)
            == "table-cell"
        )
    except ValueError:
        return (
            False,
            1.0,
            math.inf,
            0,
            "source_native_table_matrix_invalid",
        )
    envelope = _mapping(slot.get("capacityEnvelope"))
    minimum_scale = min(
        adaptive_scale,
        max(0.1, _slot_number(envelope.get("minimumTextScale"), 0.70)),
    )

    def evaluate(columns: int, scale: float) -> float:
        column_width = max(
            1.0,
            (content_width - column_gap * max(0, columns - 1)) / columns,
        )
        try:
            weights = [
                _estimated_renderer_block_height(
                    block,
                    slot=slot,
                    adaptive_scale=scale,
                    role=role,
                    column_width=column_width,
                )
                for block in blocks
            ]
        except ValueError:
            return math.inf
        ends = _balanced_renderer_ends(weights, columns)
        start = 0
        maximum = 0.0
        for end in ends:
            maximum = max(maximum, sum(weights[start:end]))
            start = end
        return maximum

    options: list[tuple[float, int, float]] = []
    maximum_columns = max(
        1,
        min(
            4,
            int(_slot_number(hints.get("columnCount"), 4)),
            len(blocks),
        ),
    )
    candidate_columns: Sequence[int] = (
        (1,)
        if fixed_table_matrix
        else range(1, maximum_columns + 1)
    )
    for columns in candidate_columns:
        minimum_height = evaluate(columns, minimum_scale)
        if minimum_height > target_height:
            continue
        maximum_height = evaluate(columns, adaptive_scale)
        if maximum_height <= target_height:
            options.append((adaptive_scale, columns, maximum_height))
            continue
        lower = minimum_scale
        upper = adaptive_scale
        lower_height = minimum_height
        for _ in range(18):
            midpoint = (lower + upper) / 2.0
            midpoint_height = evaluate(columns, midpoint)
            if midpoint_height <= target_height:
                lower = midpoint
                lower_height = midpoint_height
            else:
                upper = midpoint
        options.append((lower, columns, lower_height))
    if not options:
        reason = (
            "source_native_table_matrix_capacity_exceeded"
            if fixed_table_matrix
            else "source_native_text_capacity_exceeded"
        )
        return (
            False,
            minimum_scale,
            math.inf,
            0,
            reason,
        )
    scale, columns, maximum_height = max(
        options, key=lambda item: (round(item[0], 6), -item[1])
    )
    return True, scale, maximum_height / target_height, columns, None


def _renderer_component_geometry(
    *,
    slot: Mapping[str, Any],
    units: Sequence[_AdmissionUnit],
) -> tuple[bool, float, float, int, str | None]:
    surfaces = slot.get("sourceFlowComponentSurfaces")
    expected_digest = str(slot.get("sourceFlowComponentGraphDigest") or "")
    if (
        not isinstance(surfaces, Sequence)
        or isinstance(surfaces, (str, bytes))
        or not surfaces
        or not expected_digest
        or canonical_digest(list(surfaces)) != expected_digest
    ):
        return False, 0.0, math.inf, 0, "source_native_component_graph_invalid"
    bounds = _mapping(slot.get("bounds"))
    spacing = _mapping(slot.get("spacing"))
    width = max(
        1.0,
        _slot_number(bounds.get("width"))
        - max(0.0, _slot_number(spacing.get("insetLeft")))
        - max(0.0, _slot_number(spacing.get("insetRight"))),
    )
    full_height = max(
        1.0,
        _slot_number(bounds.get("height"))
        - max(0.0, _slot_number(spacing.get("insetTop")))
        - max(0.0, _slot_number(spacing.get("insetBottom"))),
    )
    height = full_height
    footer_units = [item for item in units if _is_footer_unit(item)]
    body_units = [item for item in units if not _is_footer_unit(item)]
    if footer_units:
        footer_width = _footer_lane_width(footer_units)
        if (
            footer_width > width
            or footer_lane_item_height() > _FOOTER_LANE_HEIGHT_PX
            or height <= _FOOTER_LANE_HEIGHT_PX + _FOOTER_BODY_GAP_PX
        ):
            return False, 1.0, math.inf, 0, "source_native_footer_capacity_exceeded"
        height -= _FOOTER_LANE_HEIGHT_PX + _FOOTER_BODY_GAP_PX
    blocks = _renderer_blocks(body_units)
    if not blocks:
        return True, 1.0, 0.0, 1, None
    table_blocks = [
        block
        for block in blocks
        if block[0].semantic_group_kind == "table-matrix"
        or _role_group(block[0].semantic_role, kind=block[0].kind) == "table-cell"
    ]
    visual_blocks = [
        block
        for block in blocks
        if any(item.kind == "image" for item in block)
        or block[0].semantic_group_kind in {"media-card", "process-step", "kpi"}
    ]
    plain_blocks = [
        block
        for block in blocks
        if block not in table_blocks and block not in visual_blocks
    ]
    evidence_blocks = [
        block
        for block in visual_blocks
        if any(_is_meaningful_image(item) for item in block)
    ]
    secondary_visual_blocks = [
        block for block in visual_blocks if block not in evidence_blocks
    ]
    envelope = _mapping(slot.get("capacityEnvelope"))
    minimum_scale = max(
        0.1, min(1.0, _slot_number(envelope.get("minimumTextScale"), 0.70))
    )
    maximum_utilization = 0.0
    selected_scale = 1.0
    plain_fraction = 0.0
    if plain_blocks and (table_blocks or visual_blocks) and not evidence_blocks:
        plain_units = [item for block in plain_blocks for item in block]
        plain_fit: tuple[bool, float, float, int, str | None] | None = None
        for candidate_fraction in (
            0.08,
            0.12,
            0.16,
            0.20,
            0.25,
            0.30,
            0.35,
            0.40,
            0.45,
            0.50,
            0.55,
            0.60,
            0.65,
            0.70,
            0.75,
        ):
            width_fraction = 1.0
            height_fraction = candidate_fraction
            if not table_blocks:
                overlay_padding = _COMPONENT_OVERLAY_PADDING_PX * 2.0
                width_fraction = max(0.05, (width - overlay_padding) / width)
                height_fraction = max(
                    0.05,
                    (height * candidate_fraction - overlay_padding) / height,
                )
            candidate_fit = _renderer_text_geometry(
                slot=slot,
                units=plain_units,
                width_fraction=width_fraction,
                height_fraction=height_fraction,
            )
            if candidate_fit[0]:
                plain_fraction = candidate_fraction
                plain_fit = candidate_fit
                break
        if plain_fit is None:
            return (
                False,
                minimum_scale,
                math.inf,
                0,
                "source_native_text_capacity_exceeded",
            )
        selected_scale = min(selected_scale, plain_fit[1])
        maximum_utilization = max(maximum_utilization, plain_fit[2])
        if table_blocks:
            height = max(1.0, height * (1.0 - plain_fraction) - 12.0)

    if table_blocks:
        try:
            if any(_table_matrix_shape(block) is None for block in table_blocks):
                return (
                    False,
                    minimum_scale,
                    math.inf,
                    1,
                    "source_native_table_matrix_invalid",
                )
        except ValueError:
            return (
                False,
                minimum_scale,
                math.inf,
                1,
                "source_native_table_matrix_invalid",
            )

        def table_height(scale: float) -> float:
            try:
                return sum(
                    _estimated_renderer_block_height(
                        block,
                        slot=slot,
                        adaptive_scale=scale,
                        role="body",
                        column_width=width,
                    )
                    for block in table_blocks
                ) + 10.0 * max(0, len(table_blocks) - 1)
            except ValueError:
                return math.inf

        minimum_height = table_height(minimum_scale)
        if minimum_height > height:
            return (
                False,
                minimum_scale,
                math.inf,
                1,
                "source_native_table_matrix_capacity_exceeded",
            )
        maximum_height = table_height(1.0)
        if maximum_height <= height:
            table_scale = 1.0
            selected_height = maximum_height
        else:
            lower = minimum_scale
            upper = 1.0
            selected_height = minimum_height
            for _ in range(18):
                midpoint = (lower + upper) / 2.0
                midpoint_height = table_height(midpoint)
                if midpoint_height <= height:
                    lower = midpoint
                    selected_height = midpoint_height
                else:
                    upper = midpoint
            table_scale = lower
        selected_scale = min(selected_scale, table_scale)
        maximum_utilization = max(maximum_utilization, selected_height / height)
        return True, selected_scale, maximum_utilization, 1, None

    if evidence_blocks:
        evidence_count = len(evidence_blocks)
        evidence_width_fraction = (
            1.0
            if evidence_count == 1
            else 0.60
            if evidence_count == 2
            else 0.55
            if evidence_count == 3
            else 0.52
        )
        evidence_height_fraction = 1.0 if evidence_count == 1 else 0.82
        evidence_cell_width = width * evidence_width_fraction
        evidence_cell_height = height * evidence_height_fraction
        geometry_pass = True
        for block in evidence_blocks:
            for unit in block:
                if not _is_meaningful_image(unit):
                    continue
                aspect = unit.image_aspect_ratio
                if aspect is None:
                    geometry_pass = False
                    break
                if evidence_cell_width / evidence_cell_height >= aspect:
                    rendered_height = evidence_cell_height
                    rendered_width = rendered_height * aspect
                else:
                    rendered_width = evidence_cell_width
                    rendered_height = rendered_width / aspect
                dimension_pass = (
                    rendered_width >= _MEANINGFUL_IMAGE_MIN_WIDTH_PX
                    and rendered_height >= _MEANINGFUL_IMAGE_MIN_HEIGHT_PX
                )
                area_scale_pass = (
                    rendered_width * rendered_height
                    >= _SLIDE_WIDTH_PX
                    * _SLIDE_HEIGHT_PX
                    * _MEANINGFUL_IMAGE_MIN_AREA_RATIO
                    and min(
                        rendered_width / _SLIDE_WIDTH_PX,
                        rendered_height / _SLIDE_HEIGHT_PX,
                    )
                    >= _MEANINGFUL_IMAGE_SCALE_FLOOR
                )
                if not (dimension_pass or area_scale_pass):
                    geometry_pass = False
                    break
            if not geometry_pass:
                break
        if not geometry_pass:
            return (
                False,
                minimum_scale,
                math.inf,
                0,
                "source_native_meaningful_image_capacity_exceeded",
            )
        overlay_blocks = [*plain_blocks, *secondary_visual_blocks]
        overlay_scale = 1.0
        overlay_utilization = 0.0
        overlay_columns = 0
        if overlay_blocks:
            horizontal_gap = 12.0
            vertical_gap = 8.0
            overlay_height = max(1.0, height - 16.0)
            maximum_overlay_columns = min(
                3,
                len(overlay_blocks),
                max(1, int((width + horizontal_gap) // 280.0)),
            )
            overlay_options: list[tuple[float, int, float]] = []

            def overlay_layout(scale: float, columns: int) -> float:
                overlay_width = max(
                    1.0,
                    (
                        width
                        - 16.0
                        - horizontal_gap * max(0, columns - 1)
                    )
                    / columns,
                )
                try:
                    weights = [
                        _estimated_renderer_block_height(
                            block,
                            slot=slot,
                            adaptive_scale=scale,
                            role="body",
                            column_width=overlay_width,
                        )
                        for block in overlay_blocks
                    ]
                except ValueError:
                    return math.inf
                weighted = [value + vertical_gap for value in weights]
                ends = _balanced_renderer_ends(weighted, columns)
                start = 0
                maximum = 0.0
                for end in ends:
                    maximum = max(
                        maximum,
                        max(0.0, sum(weighted[start:end]) - vertical_gap),
                    )
                    start = end
                return maximum

            for columns in range(1, maximum_overlay_columns + 1):
                minimum_height = overlay_layout(minimum_scale, columns)
                if minimum_height > overlay_height:
                    continue
                maximum_height = overlay_layout(1.0, columns)
                if maximum_height <= overlay_height:
                    overlay_options.append((1.0, columns, maximum_height))
                    continue
                lower = minimum_scale
                upper = 1.0
                selected_height = minimum_height
                for _ in range(18):
                    midpoint = (lower + upper) / 2.0
                    midpoint_height = overlay_layout(midpoint, columns)
                    if midpoint_height <= overlay_height:
                        lower = midpoint
                        selected_height = midpoint_height
                    else:
                        upper = midpoint
                overlay_options.append((lower, columns, selected_height))
            if not overlay_options:
                return (
                    False,
                    minimum_scale,
                    math.inf,
                    0,
                    "source_native_text_capacity_exceeded",
                )
            overlay_scale, overlay_columns, selected_height = max(
                overlay_options,
                key=lambda item: (
                    round(item[0], 6),
                    item[1],
                    -round(item[2], 6),
                ),
            )
            overlay_utilization = selected_height / overlay_height
        return (
            True,
            min(selected_scale, overlay_scale),
            max(maximum_utilization, overlay_utilization),
            max(evidence_count, overlay_columns),
            None,
        )

    if visual_blocks:
        gap = _COMPONENT_VISUAL_GAP_PX
        maximum_columns = min(
            4,
            len(visual_blocks),
            max(1, int((width + gap) // 280.0)),
        )
        options: list[tuple[float, int, float, str, tuple[int, ...]]] = []

        def visual_layout(
            scale: float, columns: int
        ) -> tuple[float, tuple[int, ...]]:
            column_width = max(
                1.0,
                (width - gap * max(0, columns - 1)) / columns,
            )
            try:
                weights = [
                    _estimated_renderer_block_height(
                        block,
                        slot=slot,
                        adaptive_scale=scale,
                        role="body",
                        column_width=column_width,
                    )
                    for block in visual_blocks
                ]
            except ValueError:
                return math.inf, ()
            weighted = [value + gap for value in weights]
            ends = _balanced_renderer_ends(weighted, columns)
            start = 0
            maximum = 0.0
            for end in ends:
                maximum = max(
                    maximum,
                    max(0.0, sum(weighted[start:end]) - gap),
                )
                start = end
            return maximum, ends

        def visual_row_layout(scale: float, columns: int) -> float:
            column_width = max(
                1.0,
                (width - gap * max(0, columns - 1)) / columns,
            )
            try:
                weights = [
                    _estimated_renderer_block_height(
                        block,
                        slot=slot,
                        adaptive_scale=scale,
                        role="body",
                        column_width=column_width,
                    )
                    for block in visual_blocks
                ]
            except ValueError:
                return math.inf
            row_heights = [
                max(weights[start : start + columns])
                for start in range(0, len(weights), columns)
            ]
            return sum(row_heights) + gap * max(0, len(row_heights) - 1)

        for columns in range(1, maximum_columns + 1):
            for packing in ("columns", "rows"):
                def evaluate(scale: float) -> tuple[float, tuple[int, ...]]:
                    if packing == "rows":
                        return visual_row_layout(scale, columns), ()
                    return visual_layout(scale, columns)

                minimum_height, minimum_ends = evaluate(minimum_scale)
                if minimum_height > height:
                    continue
                maximum_height, maximum_ends = evaluate(1.0)
                if maximum_height <= height:
                    options.append(
                        (1.0, columns, maximum_height, packing, maximum_ends)
                    )
                    continue
                lower = minimum_scale
                upper = 1.0
                selected_height = minimum_height
                selected_ends = minimum_ends
                for _ in range(18):
                    midpoint = (lower + upper) / 2.0
                    midpoint_height, midpoint_ends = evaluate(midpoint)
                    if midpoint_height <= height:
                        lower = midpoint
                        selected_height = midpoint_height
                        selected_ends = midpoint_ends
                    else:
                        upper = midpoint
                options.append(
                    (lower, columns, selected_height, packing, selected_ends)
                )
        if not options:
            return (
                False,
                minimum_scale,
                math.inf,
                0,
                "source_native_text_capacity_exceeded",
            )
        visual_scale, selected_columns, selected_height, _, _ = max(
            options,
            key=lambda item: (
                round(item[0], 6),
                item[1],
                -round(item[2], 6),
                1 if item[3] == "columns" else 0,
            ),
        )
        selected_scale = min(selected_scale, visual_scale)
        maximum_utilization = max(
            maximum_utilization, selected_height / max(1.0, height)
        )
        return (
            True,
            selected_scale,
            maximum_utilization,
            selected_columns,
            None,
        )

    return _renderer_text_geometry(
        slot=slot,
        units=body_units,
        height_fraction=height / full_height,
    )


def _renderer_geometry_fit(
    *,
    slots: Sequence[Mapping[str, Any]],
    assigned: Mapping[str, Sequence[object]],
) -> dict[str, Any]:
    minimum_scale = 1.0
    maximum_utilization = 0.0
    maximum_image_penalty = 0.0
    resolved_columns = 0
    failures: list[dict[str, str]] = []
    mixed_layouts: dict[str, str] = {}
    for slot in slots:
        slot_id = str(slot["slotId"])
        values = list(assigned[slot_id])
        if not values:
            continue
        text_units = [
            item
            for item in values
            if str(_value(item, "kind", default="text") or "text")
            in {"text", "title", "body", "quote"}
        ]
        image_units = [
            item
            for item in values
            if str(_value(item, "kind", default="") or "") == "image"
        ]
        image_count = len(image_units)
        if str(slot.get("sourceFlowLayout") or "") == "components":
            component_units = [item for item in values if isinstance(item, _AdmissionUnit)]
            passed, scale, utilization, columns, reason = (
                _renderer_component_geometry(slot=slot, units=component_units)
            )
            minimum_scale = min(minimum_scale, scale)
            maximum_utilization = max(maximum_utilization, utilization)
            resolved_columns = max(resolved_columns, columns)
            mixed_layouts[slot_id] = "components"
            if not passed:
                failures.append({"slotId": slot_id, "reasonCode": str(reason)})
            continue
        if text_units:
            selected_layout = "text"
            image_fraction = 0.0
            vertical_image_fraction = 0.0
            if image_count:
                text_weight = max(
                    1.0, sum(len(unit_text(item)) for item in text_units) / 220.0
                )
                image_weight = max(1.0, float(image_count))
                image_fraction = max(
                    0.28,
                    min(0.48, image_weight / (text_weight + image_weight)),
                )
                vertical_image_columns = max(1, min(4, image_count))
                vertical_image_rows = max(
                    1, math.ceil(image_count / vertical_image_columns)
                )
                vertical_image_fraction = max(
                    0.20, min(0.42, 0.16 + 0.08 * vertical_image_rows)
                )
                layout_candidates = []
                for layout, width_fraction, height_fraction in (
                    ("horizontal", 1.0 - image_fraction, 1.0),
                    ("vertical", 1.0, 1.0 - vertical_image_fraction),
                ):
                    result = _renderer_text_geometry(
                        slot=slot,
                        units=text_units,
                        mixed=True,
                        width_fraction=width_fraction,
                        height_fraction=height_fraction,
                    )
                    if result[0]:
                        layout_candidates.append((*result, layout))
                if layout_candidates:
                    passed, scale, utilization, columns, reason, selected_layout = max(
                        layout_candidates,
                        key=lambda item: (
                            round(item[1], 6),
                            item[5]
                            == ("vertical" if image_count >= 4 else "horizontal"),
                            item[5],
                        ),
                    )
                    mixed_layouts[slot_id] = selected_layout
                else:
                    passed = False
                    scale = 0.0
                    utilization = math.inf
                    columns = 0
                    reason = "source_native_text_readability_failed"
            else:
                passed, scale, utilization, columns, reason = (
                    _renderer_text_geometry(
                        slot=slot,
                        units=text_units,
                    )
                )
            minimum_scale = min(minimum_scale, scale)
            maximum_utilization = max(maximum_utilization, utilization)
            resolved_columns = max(resolved_columns, columns)
            if not passed:
                failures.append({"slotId": slot_id, "reasonCode": str(reason)})
        if image_count:
            bounds = _mapping(slot.get("bounds"))
            spacing = _mapping(slot.get("spacing"))
            usable_width = max(
                1.0,
                _slot_number(bounds.get("width"))
                - max(0.0, _slot_number(spacing.get("insetLeft")))
                - max(0.0, _slot_number(spacing.get("insetRight"))),
            )
            usable_height = max(
                1.0,
                _slot_number(bounds.get("height"))
                - max(0.0, _slot_number(spacing.get("insetTop")))
                - max(0.0, _slot_number(spacing.get("insetBottom"))),
            )
            if text_units and selected_layout == "vertical":
                usable_height *= vertical_image_fraction
                columns = vertical_image_columns
            elif text_units:
                usable_width *= image_fraction
                columns = max(1, min(3, math.ceil(image_count**0.5)))
            else:
                columns = max(1, min(3, math.ceil(image_count**0.5)))
            rows = max(1, math.ceil(image_count / columns))
            cell_width = usable_width / columns
            cell_height = usable_height / rows
            cell_area = cell_width * cell_height
            maximum_image_penalty = max(
                maximum_image_penalty,
                max(0.0, 1.0 - cell_area / (120.0 * 90.0)),
            )
            for image_unit in image_units:
                if not isinstance(image_unit, _AdmissionUnit) or not _is_meaningful_image(
                    image_unit
                ):
                    continue
                aspect = image_unit.image_aspect_ratio
                if aspect is None:
                    failures.append(
                        {
                            "slotId": slot_id,
                            "reasonCode": "source_native_meaningful_image_geometry_unavailable",
                        }
                    )
                    continue
                if cell_width / cell_height >= aspect:
                    rendered_height = cell_height
                    rendered_width = rendered_height * aspect
                else:
                    rendered_width = cell_width
                    rendered_height = rendered_width / aspect
                rendered_area = rendered_width * rendered_height
                slide_scale = min(
                    rendered_width / _SLIDE_WIDTH_PX,
                    rendered_height / _SLIDE_HEIGHT_PX,
                )
                dimension_pass = (
                    rendered_width >= _MEANINGFUL_IMAGE_MIN_WIDTH_PX
                    and rendered_height >= _MEANINGFUL_IMAGE_MIN_HEIGHT_PX
                )
                area_scale_pass = (
                    rendered_area
                    >= _SLIDE_WIDTH_PX
                    * _SLIDE_HEIGHT_PX
                    * _MEANINGFUL_IMAGE_MIN_AREA_RATIO
                    and slide_scale >= _MEANINGFUL_IMAGE_SCALE_FLOOR
                )
                if not (dimension_pass or area_scale_pass):
                    failures.append(
                        {
                            "slotId": slot_id,
                            "reasonCode": "source_native_meaningful_image_capacity_exceeded",
                        }
                    )
    hard_fit = not failures
    rank_utilization = (
        maximum_utilization
        if math.isfinite(maximum_utilization)
        else 1_000_000.0
    )
    rank = [
        round(1.0 - minimum_scale, 6),
        round(rank_utilization, 6),
        round(maximum_image_penalty, 6),
    ]
    return {
        "rendererGeometryFit": hard_fit,
        "rendererGeometryScale": math.floor(minimum_scale * 1_000_000.0) / 1_000_000.0,
        "rendererGeometryUtilization": (
            round(maximum_utilization, 6)
            if math.isfinite(maximum_utilization)
            else None
        ),
        "rendererGeometryColumns": resolved_columns,
        "rendererImageGeometryPenalty": round(maximum_image_penalty, 6),
        "rendererGeometryFailures": failures,
        "rendererMixedLayouts": dict(sorted(mixed_layouts.items())),
        "rendererFitRank": rank,
        "minimumBodyFontPoints": 12.0,
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
                renderer_geometry = _renderer_geometry_fit(
                    slots=slots,
                    assigned=assigned,
                )
                hard_pass = bool(
                    units
                    and not unmapped
                    and not duplicates
                    and not missing_required_slots
                    and not invalid_order
                    and renderer_geometry["rendererGeometryFit"]
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
                    **renderer_geometry,
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
                    *tuple(float(value) for value in item["rendererFitRank"]),
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
                        len(item["rendererGeometryFailures"]),
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
                "selectedRendererGeometryScale": (
                    selected["rendererGeometryScale"]
                    if selected is not None
                    else None
                ),
                "selectedRendererGeometryUtilization": (
                    selected["rendererGeometryUtilization"]
                    if selected is not None
                    else None
                ),
                "selectedRendererMixedLayouts": (
                    selected["rendererMixedLayouts"]
                    if selected is not None
                    else None
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
                "rendererGeometryFailures": diagnostic[
                    "rendererGeometryFailures"
                ],
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
