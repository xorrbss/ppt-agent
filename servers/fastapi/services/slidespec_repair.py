"""Deterministic SlideSpec repair planning and minimum-change selection."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from models.slidespec_v1 import REPAIR_PLAN_SCHEMA, LayoutNode, RepairPlan, SlideSpec
from services.slidespec_coupled_repair import (
    CoupledRepairRejection,
    build_coupled_repair_candidates,
)
from services.slidespec_geometry_candidates import (
    GeometryCandidateRejection,
    generate_geometry_candidates,
)


CASCADE_POLICY_VERSION = "repair-cascade/1.1"
REPAIR_COST_VERSION = "repair-cost/1.0"
CASCADE_STEPS = (
    "spacing_padding",
    "container_track",
    "bounded_font_scale",
    "connector_reroute",
    "glyph_shrink_to_fit",
    "image_crop_focal_point",
    "isolated_static_translation",
)

_LAYOUT_PRIMITIVE_OPS = frozenset(
    {
        "setTrackSize",
        "setColumnRatio",
        "setGridPlacement",
        "setBounds",
        "replaceLayoutSubtree",
        "rerouteConnector",
    }
)


@dataclass(frozen=True, slots=True)
class RepairProjectionRejection:
    """A deterministic, low-cardinality explanation for a skipped mutation."""

    target: str
    feature: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class RepairCascadeBuildResult:
    """Safe candidates plus fail-closed decisions made while constructing them."""

    plans: tuple[RepairPlan, ...]
    rejections: tuple[RepairProjectionRejection, ...] = ()
    geometry_rejections: tuple[GeometryCandidateRejection, ...] = ()
    coupled_rejections: tuple[CoupledRepairRejection, ...] = ()


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(by_alias=True, exclude_none=True)
        return result if isinstance(result, Mapping) else {}
    values = getattr(value, "__dict__", None)
    return values if isinstance(values, Mapping) else {}


def _get(value: object, *names: str, default: Any = None) -> Any:
    data = _mapping(value)
    for name in names:
        if name in data:
            return data[name]
    return default


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _operations(plan: object) -> tuple[Mapping[str, Any], ...]:
    raw = _get(plan, "operations", default=())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(_mapping(operation) for operation in raw)


@dataclass(frozen=True, order=True)
class RepairCost:
    """Versioned lexicographic cost; ``tie_break`` guarantees stable ordering."""

    layout_primitive_changed: int
    affected_block_count: int
    font_shrink_ratio: float
    normalized_geometry_delta: float
    operation_count: int
    tie_break: str
    version: str = REPAIR_COST_VERSION

    @property
    def sort_key(self) -> tuple[int, int, float, float, int, str]:
        return (
            self.layout_primitive_changed,
            self.affected_block_count,
            self.font_shrink_ratio,
            self.normalized_geometry_delta,
            self.operation_count,
            self.tie_break,
        )


def _bounds_delta(operation: Mapping[str, Any], width: float, height: float) -> float:
    del width, height  # SlideSpec bounds and translation values are normalized.
    explicit = _number(_get(operation, "normalizedDelta", "normalized_delta"), -1)
    if explicit >= 0:
        return explicit
    op = _get(operation, "op", default="")
    if op == "translateWithinParent":
        value = _mapping(_get(operation, "value", default={}))
        dx = _get(operation, "dx", default=value.get("x"))
        dy = _get(operation, "dy", default=value.get("y"))
        return abs(_number(dx)) + abs(_number(dy))
    precondition = _mapping(_get(operation, "precondition", default={}))
    current = _mapping(
        _get(
            operation,
            "currentBounds",
            "current_bounds",
            default=_get(precondition, "expectedBounds", "currentBounds", default={}),
        )
    )
    if op == "resizeWithinBounds" and current:
        return abs(_number(operation.get("width")) - _number(current.get("width"))) + abs(
            _number(operation.get("height")) - _number(current.get("height"))
        )
    proposed = _mapping(_get(operation, "bounds", default={}))
    if not current or not proposed:
        return 0.0
    return sum(
        abs(_number(proposed.get(key)) - _number(current.get(key)))
        for key in ("x", "y", "width", "height")
        if key in proposed and key in current
    )


def repair_cost(plan: object) -> RepairCost:
    """Compute the design-preserving cost without mutating the candidate."""

    operations = _operations(plan)
    targets = {
        str(_get(operation, "target", default=""))
        for operation in operations
        if _get(operation, "target", default="")
    }
    layout_changed = int(
        any(_get(operation, "op", default="") in _LAYOUT_PRIMITIVE_OPS for operation in operations)
    )
    shrink = max(
        (
            max(0.0, 1.0 - _number(_get(operation, "scale"), 1.0))
            for operation in operations
            if _get(operation, "op", default="") == "setFontScale"
        ),
        default=0.0,
    )
    viewport = _mapping(_get(plan, "viewport", default={}))
    width = max(1.0, _number(viewport.get("width"), 1280.0))
    height = max(1.0, _number(viewport.get("height"), 720.0))
    geometry_delta = sum(_bounds_delta(operation, width, height) for operation in operations)
    plan_id = str(_get(plan, "planId", "plan_id", default=""))
    if not plan_id:
        encoded = json.dumps(_mapping(plan), sort_keys=True, separators=(",", ":"), default=str)
        plan_id = hashlib.sha256(encoded.encode()).hexdigest()
    return RepairCost(
        layout_changed,
        len(targets),
        round(shrink, 8),
        round(geometry_delta, 8),
        len(operations),
        plan_id,
    )


def sort_candidates(candidates: Sequence[object]) -> tuple[object, ...]:
    return tuple(sorted(candidates, key=lambda candidate: repair_cost(candidate).sort_key))


def _layout_index(root: LayoutNode) -> dict[str, LayoutNode]:
    result: dict[str, LayoutNode] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        result[node.layoutId] = node
        stack.extend(node.children)
    return result


def realized_repair_cost(
    baseline: SlideSpec, candidate: SlideSpec, plan: RepairPlan
) -> RepairCost:
    """Measure what the rendered candidate spec actually changed.

    Planned operations are useful for ordering evaluation, but their payload can
    over- or under-state the resulting movement.  Publication selection uses this
    post-application cost so a broad rewrite cannot masquerade as a cheap repair.
    """
    before = _layout_index(baseline.layout)
    after = _layout_index(candidate.layout)
    affected = 0
    primitive_changed = 0
    font_shrink = 0.0
    geometry_delta = 0.0

    for layout_id in sorted(set(before) | set(after)):
        old = before.get(layout_id)
        new = after.get(layout_id)
        if old is None or new is None:
            affected += 1
            primitive_changed = 1
            geometry_delta += 1.0
            continue
        old_data = old.model_dump(by_alias=True, exclude_none=True)
        new_data = new.model_dump(by_alias=True, exclude_none=True)
        if old_data == new_data:
            continue
        affected += 1
        if new.fontScale < old.fontScale:
            font_shrink = max(font_shrink, 1.0 - (new.fontScale / old.fontScale))
        if old.bounds is not None and new.bounds is not None:
            geometry_delta += sum(
                abs(getattr(new.bounds, field) - getattr(old.bounds, field))
                for field in ("x", "y", "width", "height")
            )
        elif old.bounds != new.bounds:
            geometry_delta += 1.0

        for data in (old_data, new_data):
            data.pop("bounds", None)
            data.pop("fontScale", None)
            data.pop("children", None)
        old_children = tuple(child.layoutId for child in old.children)
        new_children = tuple(child.layoutId for child in new.children)
        if old_data != new_data or old_children != new_children:
            primitive_changed = 1

    return RepairCost(
        primitive_changed,
        affected,
        round(font_shrink, 8),
        round(geometry_delta, 8),
        len(plan.operations),
        plan.plan_id,
    )


def _items(context: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], ...]:
    raw = context.get(name)
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        return (raw,)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return tuple(item for value in raw if (item := _mapping(value)))
    return ()


def _operation(kind: str, item: Mapping[str, Any], **values: Any) -> dict[str, Any]:
    target = _get(item, "target", "semanticId", "semantic_id", default="")
    if not target:
        raise ValueError(f"{kind} adjustment requires a semantic target")
    return {"op": kind, "target": target, **values}


def _spacing_operations(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _items(context, "spacing"):
        result.append(
            _operation(
                "setSpacing",
                item,
                gap=_get(item, "gap", "candidate"),
                precondition={"currentGap": _get(item, "currentGap", "current")},
            )
        )
    for item in _items(context, "padding"):
        result.append(
            _operation(
                "setPadding",
                item,
                padding=_get(item, "padding", "candidate"),
                precondition={"currentPadding": _get(item, "currentPadding", "current")},
            )
        )
    return result


def _container_operations(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    def set_bounds_allowed(envelope: Mapping[str, Any]) -> bool:
        raw_allowed = envelope.get("allowedOperations")
        allowed = (
            {str(value) for value in raw_allowed}
            if isinstance(raw_allowed, Sequence)
            and not isinstance(raw_allowed, (str, bytes))
            else set()
        )
        return (
            envelope.get("locked") is not True
            and envelope.get("lockSize") is not True
            and "setBounds" in allowed
        )

    result: list[dict[str, Any]] = []
    for item in _items(context, "containerBounds"):
        target = str(_get(item, "target", "semanticId", "semantic_id", default=""))
        imported_envelopes = tuple(
            _mapping(issue.get("mutationEnvelope"))
            for issue in _items(context, "geometryIssues")
            if str(issue.get("target", "")) == target
            and issue.get("requiresEnvelope") is True
        )
        if any(not set_bounds_allowed(envelope) for envelope in imported_envelopes):
            continue
        result.append(
            _operation(
                "setBounds",
                item,
                bounds=_get(item, "bounds", "candidate"),
                precondition={
                    "currentBounds": _get(item, "currentBounds", "current")
                },
            )
        )
    for item in _items(context, "trackSize"):
        result.append(
            _operation(
                "setTrackSize",
                item,
                value=_get(item, "value", "size", "candidate"),
                precondition={"currentSize": _get(item, "currentSize", "current")},
            )
        )
    for item in _items(context, "columnRatio"):
        result.append(
            _operation(
                "setColumnRatio",
                item,
                value=_get(item, "value", "ratio", "candidate"),
                precondition={"currentRatio": _get(item, "currentRatio", "current")},
            )
        )
    return result


def _font_operations(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _items(context, "fontScale"):
        scale = _number(_get(item, "scale", "candidate"), 0.0)
        minimum = _number(_get(item, "minimumScale", "minimum_scale"), 0.85)
        if not minimum <= scale <= 1.0:
            raise ValueError("font scale must remain inside the bounded range")
        result.append(
            _operation(
                "setFontScale",
                item,
                scale=scale,
                bounds=_get(item, "bounds", default={"minScale": minimum, "maxScale": 1.0}),
                precondition={"currentScale": _get(item, "currentScale", "current", default=1.0)},
            )
        )
    return result


def _target(item: Mapping[str, Any]) -> str:
    return str(_get(item, "target", "semanticId", "semantic_id", default=""))


def _reject(
    item: Mapping[str, Any], feature: str, reason_code: str
) -> RepairProjectionRejection:
    return RepairProjectionRejection(_target(item), feature, reason_code)


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return frozenset()
    return frozenset(str(item) for item in value)


def _mutation_allowed(item: Mapping[str, Any], *operations: str) -> str | None:
    """Require an explicit closed mutation envelope for advanced projection.

    Imported legacy nodes may have no envelope.  Treating absence as permission
    would make a newly supported projection silently broader than its source
    contract, so advanced operations intentionally require affirmative evidence.
    """

    envelope = _mapping(item.get("mutationEnvelope", item.get("mutation_envelope", {})))
    if not envelope or bool(envelope.get("locked", False)):
        return "repair.mutation_envelope.missing_or_locked"
    allowed = _string_set(envelope.get("allowedOperations"))
    if not set(operations).issubset(allowed):
        return "repair.mutation_envelope.operation_not_allowed"
    return None


def _chrome_measurement(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], str | None]:
    measurement = _mapping(item.get("measurement", item.get("browserMeasurement", {})))
    if measurement.get("source") != "chrome":
        return measurement, "repair.measurement.chrome_required"
    if measurement.get("telemetryComplete") is not True:
        return measurement, "repair.measurement.incomplete"
    return measurement, None


def _roundtrip_supported(item: Mapping[str, Any], *properties: str) -> str | None:
    proof = _mapping(item.get("roundTrip", item.get("round_trip", {})))
    verified = _string_set(proof.get("verifiedProperties"))
    if proof.get("supported") is not True or not set(properties).issubset(verified):
        return "repair.source_projection.roundtrip_unsupported"
    return None


def _normalized_point(value: object, *, signed: bool = False) -> bool:
    point = _mapping(value)
    low = -1.0 if signed else 0.0
    return all(low <= _number(point.get(axis), low - 1) <= 1.0 for axis in ("x", "y"))


def _normalized_bounds(value: object) -> bool:
    bounds = _mapping(value)
    x = _number(bounds.get("x"), -1)
    y = _number(bounds.get("y"), -1)
    width = _number(bounds.get("width"), -1)
    height = _number(bounds.get("height"), -1)
    return (
        x >= 0
        and y >= 0
        and width > 0
        and height > 0
        and x + width <= 1
        and y + height <= 1
    )


def _connector_operations(
    context: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[RepairProjectionRejection]]:
    operations: list[dict[str, Any]] = []
    rejected: list[RepairProjectionRejection] = []
    for item in _items(context, "connectorRoute"):
        feature = "connector_reroute"
        reason = None if _target(item) else "repair.target.missing"
        reason = reason or _mutation_allowed(item, "rerouteConnector")
        measurement, measurement_reason = _chrome_measurement(item)
        reason = reason or measurement_reason or _roundtrip_supported(item, "connector-route")
        route = _mapping(_get(item, "route", "candidateRoute", "candidate", default={}))
        points = route.get("points")
        anchors = _mapping(measurement.get("endpointAnchors"))
        clearance = _number(measurement.get("minimumClearancePx"), -1)
        anchor_error = _number(anchors.get("maxErrorPx"), float("inf"))
        if reason is None and (
            not isinstance(points, Sequence)
            or isinstance(points, (str, bytes))
            or not 2 <= len(points) <= 16
            or not all(_normalized_point(point) for point in points)
        ):
            reason = "repair.connector.route_invalid"
        if reason is None and (
            anchors.get("startResolved") is not True
            or anchors.get("endResolved") is not True
            or anchor_error > 0.5
        ):
            reason = "repair.connector.anchor_unproven"
        if reason is None and (
            measurement.get("obstacleFree") is not True or clearance < 1.0
        ):
            reason = "repair.connector.clearance_unproven"
        if reason is not None:
            rejected.append(_reject(item, feature, reason))
            continue
        operations.append(
            _operation(
                "rerouteConnector",
                item,
                value=route,
                precondition={"expectedValue": _get(item, "currentRoute", "current")},
            )
        )
    return operations, rejected


def _glyph_font_operations(
    context: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[RepairProjectionRejection]]:
    operations: list[dict[str, Any]] = []
    rejected: list[RepairProjectionRejection] = []
    for item in _items(context, "glyphFit"):
        feature = "glyph_shrink_to_fit"
        reason = None if _target(item) else "repair.target.missing"
        reason = reason or _mutation_allowed(item, "setFontScale")
        measurement, measurement_reason = _chrome_measurement(item)
        reason = reason or measurement_reason or _roundtrip_supported(item, "font-size")
        if reason is None and (
            measurement.get("fontsReady") is not True
            or measurement.get("kind")
            not in {"glyph", "glyph-run", "range-bounds", "owned-text-separation"}
        ):
            reason = "repair.glyph.measurement_unavailable"

        measured_width = _number(measurement.get("measuredWidthPx"), -1)
        measured_height = _number(measurement.get("measuredHeightPx"), -1)
        available_width = _number(measurement.get("availableWidthPx"), -1)
        available_height = _number(measurement.get("availableHeightPx"), -1)
        current_scale = _number(_get(item, "currentScale", "current"), 1.0)
        minimum_scale = _number(_get(item, "minimumScale", "minimum_scale"), 0.85)
        envelope = _mapping(item.get("mutationEnvelope", {}))
        minimum_scale = max(minimum_scale, _number(envelope.get("minFontScale"), 0.5))
        max_shrink = _number(envelope.get("maxFontShrink"), 0.15)
        if reason is None and min(
            measured_width,
            measured_height,
            available_width,
            available_height,
            current_scale,
        ) <= 0:
            reason = "repair.glyph.measurement_invalid"
        separation_probe = measurement.get("kind") == "owned-text-separation"
        requested_scale = _number(item.get("candidateScale"), float("nan"))
        if separation_probe:
            target_bounds = _mapping(measurement.get("targetTextBoundsPx"))
            occluder_bounds = _mapping(measurement.get("occluderTextBoundsPx"))
            target_x = _number(target_bounds.get("x"), float("nan"))
            target_width = _number(target_bounds.get("width"), float("nan"))
            occluder_x = _number(occluder_bounds.get("x"), float("nan"))
            current_right = target_x + target_width
            projected_right = target_x + target_width * (
                requested_scale / current_scale if current_scale > 0 else float("inf")
            )
            if reason is None and (
                not all(
                    math.isfinite(value)
                    for value in (
                        requested_scale,
                        target_x,
                        target_width,
                        occluder_x,
                        projected_right,
                    )
                )
                or target_width <= 0
                or current_right <= occluder_x + 0.5
                or projected_right > occluder_x + 0.5
                or measurement.get("candidateSeparatesTextPaint") is not True
            ):
                reason = "repair.glyph.separation_probe_unproven"
            scale = round(requested_scale, 6)
        else:
            ratio = min(
                1.0,
                available_width / measured_width if measured_width > 0 else 0.0,
                available_height / measured_height if measured_height > 0 else 0.0,
            )
            scale = round(current_scale * ratio, 6)
        if reason is None and scale >= current_scale - 1e-9:
            # Chrome proved that the glyph ink/line box already fits the owned
            # container.  A clipping diagnosis in this state is positional
            # (viewport/ancestor clipping), so font shrink is not an applicable
            # projection rather than an unproven request.
            reason = "repair.glyph.size_reduction_not_applicable"
        if reason is None and (
            scale < minimum_scale - 1e-9
            or 1.0 - (scale / current_scale) > max_shrink + 1e-9
        ):
            reason = "repair.glyph.required_shrink_outside_envelope"
        if reason is not None:
            rejected.append(_reject(item, feature, reason))
            continue
        current_px = _number(measurement.get("fontSizePx"), 0)
        operations.append(
            _operation(
                "setFontScale",
                item,
                scale=scale,
                bounds={
                    "minScale": minimum_scale,
                    "maxScale": current_scale,
                    **(
                        {"minPx": round(current_px * minimum_scale / current_scale, 6),
                         "maxPx": current_px}
                        if current_px > 0 and current_scale > 0
                        else {}
                    ),
                },
                precondition={"currentScale": current_scale},
            )
        )
    return operations, rejected


def _visible_crop_window(
    natural_width: float,
    natural_height: float,
    box_width: float,
    box_height: float,
    focal_x: float,
    focal_y: float,
) -> tuple[float, float, float, float]:
    scale = max(box_width / natural_width, box_height / natural_height)
    visible_width = min(1.0, box_width / (natural_width * scale))
    visible_height = min(1.0, box_height / (natural_height * scale))
    left = min(max(0.0, focal_x - visible_width / 2), 1.0 - visible_width)
    top = min(max(0.0, focal_y - visible_height / 2), 1.0 - visible_height)
    return left, top, visible_width, visible_height


def _protected_region_visible(
    window: tuple[float, float, float, float], region: Mapping[str, Any]
) -> bool:
    left, top, width, height = window
    region_x = _number(region.get("x"), -1)
    region_y = _number(region.get("y"), -1)
    region_width = _number(region.get("width"), -1)
    region_height = _number(region.get("height"), -1)
    return (
        min(region_x, region_y, region_width, region_height) >= 0
        and region_x + region_width <= left + width + 1e-9
        and region_y + region_height <= top + height + 1e-9
        and region_x >= left - 1e-9
        and region_y >= top - 1e-9
    )


def _image_operations(
    context: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[RepairProjectionRejection]]:
    operations: list[dict[str, Any]] = []
    rejected: list[RepairProjectionRejection] = []
    for item in _items(context, "imageCrop"):
        feature = "image_crop_focal_point"
        candidate_fit = str(_get(item, "fit", "candidateFit", default="cover"))
        focal = _mapping(_get(item, "focalPoint", "candidateFocalPoint", default={}))
        requested = ["setImageFit", "setCropFocalPoint"]
        reason = None if _target(item) else "repair.target.missing"
        reason = reason or _mutation_allowed(item, *requested)
        measurement, measurement_reason = _chrome_measurement(item)
        reason = reason or measurement_reason or _roundtrip_supported(
            item, "object-fit", "object-position"
        )
        natural_width = _number(measurement.get("naturalWidthPx"), -1)
        natural_height = _number(measurement.get("naturalHeightPx"), -1)
        box_width = _number(measurement.get("boxWidthPx"), -1)
        box_height = _number(measurement.get("boxHeightPx"), -1)
        focal_x = _number(focal.get("x"), -1)
        focal_y = _number(focal.get("y"), -1)
        if reason is None and (
            measurement.get("decoded") is not True
            or min(natural_width, natural_height, box_width, box_height) <= 0
        ):
            reason = "repair.image.intrinsic_measurement_unavailable"
        if reason is None and (
            candidate_fit not in {"contain", "cover", "fill"}
            or not (0 <= focal_x <= 1 and 0 <= focal_y <= 1)
        ):
            reason = "repair.image.crop_invalid"
        if reason is None and candidate_fit == "cover":
            protected = _mapping(measurement.get("protectedRegion"))
            if not protected or not _protected_region_visible(
                _visible_crop_window(
                    natural_width, natural_height, box_width, box_height, focal_x, focal_y
                ),
                protected,
            ):
                reason = "repair.image.protected_region_visibility_unproven"
        if reason is not None:
            rejected.append(_reject(item, feature, reason))
            continue
        operations.extend(
            (
                _operation(
                    "setImageFit",
                    item,
                    value=candidate_fit,
                    precondition={"expectedValue": _get(item, "currentFit", default="contain")},
                ),
                _operation(
                    "setCropFocalPoint",
                    item,
                    value={"x": focal_x, "y": focal_y},
                    precondition={"expectedValue": _get(
                        item, "currentFocalPoint", default={"x": 0.5, "y": 0.5}
                    )},
                ),
            )
        )
    return operations, rejected


def _static_translation_operations(
    context: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[RepairProjectionRejection]]:
    operations: list[dict[str, Any]] = []
    rejected: list[RepairProjectionRejection] = []
    for item in _items(context, "staticTranslation"):
        feature = "isolated_static_translation"
        reason = None if _target(item) else "repair.target.missing"
        reason = reason or _mutation_allowed(item, "translateWithinParent")
        measurement, measurement_reason = _chrome_measurement(item)
        reason = reason or measurement_reason or _roundtrip_supported(item, "transform")
        delta = _mapping(_get(item, "delta", "candidate", default={}))
        dx = _number(delta.get("x"), float("inf"))
        dy = _number(delta.get("y"), float("inf"))
        envelope = _mapping(item.get("mutationEnvelope", {}))
        limit = _number(envelope.get("maxTranslation"), 0.08)
        after = _mapping(measurement.get("candidateBoundsInParent"))
        contained = (
            0 <= _number(after.get("x"), -1) <= 1
            and 0 <= _number(after.get("y"), -1) <= 1
            and _number(after.get("x"), -1) + _number(after.get("width"), 2) <= 1
            and _number(after.get("y"), -1) + _number(after.get("height"), 2) <= 1
        )
        safe_interval = _mapping(measurement.get("safeTranslationIntervalPx"))
        parent_bounds = _mapping(measurement.get("parentBoundsPx"))
        target_bounds = _mapping(measurement.get("targetBoundsPx"))
        parent_width = _number(parent_bounds.get("width"), -1)
        parent_height = _number(parent_bounds.get("height"), -1)
        current_in_parent_x = (
            (_number(target_bounds.get("x")) - _number(parent_bounds.get("x")))
            / parent_width
            if parent_width > 0
            else float("inf")
        )
        current_in_parent_y = (
            (_number(target_bounds.get("y")) - _number(parent_bounds.get("y")))
            / parent_height
            if parent_height > 0
            else float("inf")
        )
        dx_px = (
            (_number(after.get("x"), float("inf")) - current_in_parent_x)
            * parent_width
            if parent_width > 0
            else float("inf")
        )
        dy_px = (
            (_number(after.get("y"), float("inf")) - current_in_parent_y)
            * parent_height
            if parent_height > 0
            else float("inf")
        )
        interval_complete = all(
            key in safe_interval
            and abs(_number(safe_interval.get(key), float("inf"))) != float("inf")
            for key in ("minX", "maxX", "minY", "maxY")
        )
        within_measured_interval = interval_complete and (
            _number(safe_interval.get("minX")) - 0.5
            <= dx_px
            <= _number(safe_interval.get("maxX")) + 0.5
            and _number(safe_interval.get("minY")) - 0.5
            <= dy_px
            <= _number(safe_interval.get("maxY")) + 0.5
        )
        if reason is None and (
            measurement.get("position") != "static"
            or measurement.get("layoutIsolation") is not True
            or measurement.get("transformComposable") is not True
            or measurement.get("stackingStable") is False
            or abs(_number(measurement.get("flowImpactPx"), float("inf"))) > 0.5
            or _number(measurement.get("maxSiblingDeltaPx"), float("inf")) > 0.5
        ):
            unsupported = str(measurement.get("unsupportedReason") or "").strip()
            reason = unsupported or "repair.static_translation.flow_isolation_unproven"
        if reason is None and (abs(dx) <= 1e-9 and abs(dy) <= 1e-9):
            reason = "repair.static_translation.zero_delta"
        if reason is None and not _normalized_point(delta, signed=True):
            reason = "repair.static_translation.outside_envelope"
        if reason is None and interval_complete and not within_measured_interval:
            reason = "repair.static_translation.outside_containment_interval"
        if (
            reason is None
            and not interval_complete
            and max(abs(dx), abs(dy)) > limit + 1e-9
        ):
            # Backward-compatible fail-closed path for old artifacts that predate
            # the Chrome-measured containment interval.
            reason = "repair.static_translation.outside_envelope"
        current_bounds = _get(item, "currentBounds")
        if reason is None and not _normalized_bounds(current_bounds):
            reason = "repair.static_translation.precondition_unproven"
        if reason is None and not interval_complete and not contained:
            # Legacy payloads only described containment relative to the
            # immediate parent.  A current Chrome probe instead intersects the
            # viewport with every clipping ancestor, so that measured interval
            # is the stronger coordinate-space proof when overflow is visible.
            reason = "repair.static_translation.containment_unproven"
        if reason is not None:
            rejected.append(_reject(item, feature, reason))
            continue
        operations.append(
            _operation(
                "translateWithinParent",
                item,
                value={"x": dx, "y": dy},
                precondition={"expectedBounds": current_bounds},
            )
        )
    return operations, rejected


def _plan(
    context: Mapping[str, Any],
    operations: list[dict[str, Any]],
    *,
    diagnostic_refs: Sequence[str] | None = None,
    scope_targets: Sequence[str] | None = None,
) -> RepairPlan:
    targets = tuple(
        dict.fromkeys(
            (
                *(str(target) for target in (scope_targets or ())),
                *(str(operation["target"]) for operation in operations),
            )
        )
    )
    refs_by_target = _mapping(context.get("diagnosticRefsByTarget", {}))
    targeted_refs = tuple(
        dict.fromkeys(
            str(reference)
            for target in targets
            for reference in (
                refs_by_target.get(target, ())
                if isinstance(refs_by_target.get(target, ()), Sequence)
                and not isinstance(refs_by_target.get(target, ()), (str, bytes))
                else ()
            )
        )
    )
    common = {
        "$schema": REPAIR_PLAN_SCHEMA,
        "slideId": context.get("slideId"),
        "baseSpecRevision": context.get("baseSpecRevision"),
        "baseSpecHash": context.get("baseSpecHash"),
        "expectedContentDigest": context.get("expectedContentDigest"),
        "scope": targets,
        "diagnosticRefs": list(
            diagnostic_refs
            if diagnostic_refs is not None
            else targeted_refs or context.get("diagnosticRefs", ())
        ),
        "strategy": "deterministic_cascade",
        "operations": operations,
        "limits": dict(context.get("limits", {"maxOperations": 8, "maxAffectedBlocks": 4})),
        "preconditions": {
            "slideId": context.get("slideId"),
            "baseSpecRevision": context.get("baseSpecRevision"),
            "baseSpecHash": context.get("baseSpecHash"),
            "expectedContentDigest": context.get("expectedContentDigest"),
        },
        "proposedBy": {
            "kind": "rule_engine",
            "policyVersion": CASCADE_POLICY_VERSION,
            "costVersion": REPAIR_COST_VERSION,
        },
    }
    encoded = json.dumps(common, sort_keys=True, separators=(",", ":"), default=str)
    common["planId"] = "rp_rule_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]
    return RepairPlan.model_validate(common)


def _append_scoped_plans(
    plans: list[RepairPlan], context: Mapping[str, Any], operations: list[dict[str, Any]]
) -> None:
    if not operations:
        return
    by_target: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        by_target.setdefault(str(operation["target"]), []).append(operation)
    plans.extend(_plan(context, target_operations) for target_operations in by_target.values())
    if len(by_target) > 1:
        plans.append(_plan(context, operations))


def _plan_issue_groups(plan: RepairPlan) -> frozenset[str]:
    return frozenset(
        reference.removeprefix("issue-group:")
        for reference in plan.diagnostic_refs
        if reference.startswith("issue-group:")
    )


def _compose_plans(
    context: Mapping[str, Any],
    composition_id: str,
    members: Sequence[RepairPlan],
) -> RepairPlan | None:
    limits = _mapping(context.get("limits"))
    max_operations = int(max(1, min(32, _number(limits.get("maxOperations"), 8))))
    max_affected = int(
        max(
            1,
            min(
                32,
                _number(
                    limits.get(
                        "maxAffectedBlocks", limits.get("maxTouchedLayoutNodes")
                    ),
                    4,
                ),
            ),
        )
    )
    operations: list[dict[str, Any]] = []
    by_target: dict[str, str] = {}
    for member in members:
        for operation in member.operations:
            payload = operation.model_dump(mode="json", by_alias=True, exclude_none=True)
            target = str(payload.get("target", ""))
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if target in by_target:
                if by_target[target] == encoded:
                    continue
                return None
            by_target[target] = encoded
            operations.append(payload)
    if (
        not operations
        or len(operations) > max_operations
        or len(by_target) > max_affected
    ):
        return None
    refs = tuple(
        dict.fromkeys(
            (
                f"composition:{composition_id}",
                *(reference for member in members for reference in member.diagnostic_refs),
            )
        )
    )
    if len(refs) > 64:
        return None
    return _plan(context, operations, diagnostic_refs=refs)


def _cross_issue_compositions(
    context: Mapping[str, Any], plans: Sequence[RepairPlan]
) -> tuple[RepairPlan, ...]:
    raw_groups = _mapping(context.get("geometryCompositionGroups"))
    required = _string_set(context.get("requiredGeometryIssueGroups"))
    if not raw_groups or not required:
        return ()
    beam_width = int(
        max(1, min(32, _number(context.get("maxGeometryCompositions"), 16)))
    )
    atomic_plans = tuple(
        plan
        for plan in plans
        if not any(
            reference.startswith("composition:")
            for reference in plan.diagnostic_refs
        )
    )
    by_group = {
        group_id: tuple(
            sorted(
                (plan for plan in atomic_plans if group_id in _plan_issue_groups(plan)),
                key=lambda plan: repair_cost(plan).sort_key,
            )[:beam_width]
        )
        for group_id in required
    }
    result: list[RepairPlan] = []
    for composition_id, raw_group_ids in sorted(
        raw_groups.items(), key=lambda item: str(item[0])
    ):
        group_ids = tuple(
            dict.fromkeys(str(value) for value in (
                raw_group_ids
                if isinstance(raw_group_ids, Sequence)
                and not isinstance(raw_group_ids, (str, bytes))
                else ()
            ))
        )
        if (
            len(group_ids) < 2
            or not set(group_ids).issubset(required)
            or any(not by_group.get(group_id) for group_id in group_ids)
        ):
            continue
        states: list[tuple[RepairPlan, ...]] = [()]
        for group_id in group_ids:
            next_states: dict[str, tuple[RepairPlan, ...]] = {}
            for state in states:
                if any(group_id in _plan_issue_groups(plan) for plan in state):
                    options: Sequence[RepairPlan | None] = (None,)
                else:
                    options = by_group[group_id]
                for option in options:
                    members = state if option is None else (*state, option)
                    combined = _compose_plans(context, str(composition_id), members)
                    if combined is None:
                        continue
                    signature = json.dumps(
                        [
                            operation.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                            for operation in combined.operations
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    next_states.setdefault(signature, members)
            def state_key(members: tuple[RepairPlan, ...]) -> tuple[Any, ...]:
                combined = _compose_plans(context, str(composition_id), members)
                return repair_cost(combined).sort_key if combined is not None else (
                    2, 33, 2.0, 4.0, 33, "invalid"
                )

            states = sorted(next_states.values(), key=state_key)[:beam_width]
            if not states:
                break
        for members in states:
            covered = frozenset(
                group for plan in members for group in _plan_issue_groups(plan)
            )
            combined = _compose_plans(context, str(composition_id), members)
            if combined is not None and set(group_ids).issubset(covered):
                result.append(combined)
    unique = {plan.plan_id: plan for plan in result}
    return tuple(
        sorted(unique.values(), key=lambda plan: repair_cost(plan).sort_key)[:beam_width]
    )


def build_deterministic_cascade_report(
    context: Mapping[str, Any],
) -> RepairCascadeBuildResult:
    """Build minimum-change candidates before any multi-target fallback.

    A geometry diagnosis can mention several independently repairable blocks.  If
    all of those mutations are bundled into one plan, fixing an outer container
    while also growing an inner title can cancel the intended improvement.  Keep
    operations for one target together, evaluate those plans first, and retain a
    combined candidate only as a last resort for defects that genuinely require
    coordinated edits.
    """

    plans: list[RepairPlan] = []
    rejected: list[RepairProjectionRejection] = []
    for builder in (_spacing_operations, _container_operations, _font_operations):
        _append_scoped_plans(plans, context, builder(context))
    for builder in (
        _connector_operations,
        _glyph_font_operations,
        _image_operations,
        _static_translation_operations,
    ):
        operations, builder_rejections = builder(context)
        _append_scoped_plans(plans, context, operations)
        rejected.extend(builder_rejections)
    geometry = generate_geometry_candidates(context)
    plans.extend(
        _plan(
            context,
            list(candidate.operations),
            diagnostic_refs=candidate.diagnostic_refs,
        )
        for candidate in geometry.candidates
    )
    coupled = build_coupled_repair_candidates(context)
    plans.extend(
        _plan(
            context,
            list(candidate.operations),
            diagnostic_refs=candidate.diagnostic_refs,
            scope_targets=candidate.scope,
        )
        for candidate in coupled.candidates
    )
    plans.extend(_cross_issue_compositions(context, plans))
    plans = list({plan.plan_id: plan for plan in plans}.values())
    return RepairCascadeBuildResult(
        tuple(plans), tuple(rejected), geometry.rejections, coupled.rejections
    )


def build_deterministic_cascade(context: Mapping[str, Any]) -> tuple[RepairPlan, ...]:
    """Backward-compatible candidate-only view of the structured build report."""

    return build_deterministic_cascade_report(context).plans


def next_cascade_action(
    outcome: Literal["accepted", "rejected", "inconclusive"], *, has_more: bool
) -> Literal["stop_accepted", "next_deterministic", "call_llm", "stop_inconclusive"]:
    if outcome == "accepted":
        return "stop_accepted"
    if outcome == "inconclusive":
        return "stop_inconclusive"
    if outcome == "rejected":
        return "next_deterministic" if has_more else "call_llm"
    raise ValueError(f"unsupported outcome: {outcome!r}")


@dataclass(frozen=True)
class CandidateSelection:
    outcome: Literal["accepted", "rejected", "inconclusive"]
    selected: object | None
    evaluated: tuple[object, ...]
    reason_codes: tuple[str, ...]


def _outcome(result: object) -> str:
    if isinstance(result, str):
        return result
    return str(_get(result, "outcome", "status", default=""))


def _reasons(result: object) -> tuple[str, ...]:
    raw = _get(result, "reasonCodes", "reason_codes", default=())
    return tuple(str(value) for value in raw) if isinstance(raw, Sequence) else ()


def select_candidate(
    candidates: Sequence[object],
    *,
    cheap_gate: Callable[[object], object],
    acceptance_gate: Callable[[object], object],
) -> CandidateSelection:
    """Apply cheap gates, then expensive acceptance in minimum-change order.

    A candidate-specific rejection may advance to the next candidate.  Any
    inconclusive result stops immediately and preserves the original revision.
    """

    survivors: list[object] = []
    evaluated: list[object] = []
    rejection_codes: list[str] = []
    for candidate in candidates:
        result = cheap_gate(candidate)
        outcome = _outcome(result)
        if outcome == "inconclusive":
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), _reasons(result) or ("acceptance.inconclusive",)
            )
        if outcome in {"passed", "accepted"}:
            survivors.append(candidate)
        elif outcome in {"failed", "rejected"}:
            rejection_codes.extend(_reasons(result))
        else:
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), ("acceptance.inconclusive",)
            )

    for candidate in sort_candidates(survivors):
        evaluated.append(candidate)
        result = acceptance_gate(candidate)
        outcome = _outcome(result)
        if outcome == "accepted":
            return CandidateSelection("accepted", candidate, tuple(evaluated), _reasons(result))
        if outcome == "inconclusive":
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), _reasons(result) or ("acceptance.inconclusive",)
            )
        if outcome != "rejected":
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), ("acceptance.inconclusive",)
            )
        rejection_codes.extend(_reasons(result))
    return CandidateSelection(
        "rejected",
        None,
        tuple(evaluated),
        tuple(dict.fromkeys(rejection_codes)) or ("acceptance.required_gate_failed",),
    )


__all__ = [
    "CASCADE_POLICY_VERSION",
    "CASCADE_STEPS",
    "REPAIR_COST_VERSION",
    "CandidateSelection",
    "RepairCascadeBuildResult",
    "RepairCost",
    "RepairProjectionRejection",
    "build_deterministic_cascade",
    "build_deterministic_cascade_report",
    "next_cascade_action",
    "repair_cost",
    "realized_repair_cost",
    "select_candidate",
    "sort_candidates",
]
