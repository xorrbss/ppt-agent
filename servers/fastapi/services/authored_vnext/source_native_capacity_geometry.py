"""Source-slot geometry evaluation shared by target capacity admission.

Dependencies are supplied by the facade so its existing patch seams remain live.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def _renderer_geometry_fit(
    *,
    dependencies: object,
    slots: Sequence[Mapping[str, Any]],
    assigned: Mapping[str, Sequence[object]],
) -> dict[str, Any]:
    _value = dependencies._value
    _mapping = dependencies._mapping
    _slot_number = dependencies._slot_number
    _AdmissionUnit = dependencies._AdmissionUnit
    _renderer_component_geometry = dependencies._renderer_component_geometry
    _renderer_text_geometry = dependencies._renderer_text_geometry
    _is_meaningful_image = dependencies._is_meaningful_image
    unit_text = dependencies.unit_text
    _SLIDE_WIDTH_PX = dependencies._SLIDE_WIDTH_PX
    _SLIDE_HEIGHT_PX = dependencies._SLIDE_HEIGHT_PX
    _MEANINGFUL_IMAGE_MIN_WIDTH_PX = dependencies._MEANINGFUL_IMAGE_MIN_WIDTH_PX
    _MEANINGFUL_IMAGE_MIN_HEIGHT_PX = dependencies._MEANINGFUL_IMAGE_MIN_HEIGHT_PX
    _MEANINGFUL_IMAGE_MIN_AREA_RATIO = dependencies._MEANINGFUL_IMAGE_MIN_AREA_RATIO
    _MEANINGFUL_IMAGE_SCALE_FLOOR = dependencies._MEANINGFUL_IMAGE_SCALE_FLOOR
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
