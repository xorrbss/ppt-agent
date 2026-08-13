"""Pure structural and geometric enforcement for SlideSpec 1.1 constraints."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
from typing import Any

from models.slidespec_v1 import (
    AlignmentConstraint,
    AnchorConstraint,
    Bounds,
    ContainmentConstraint,
    GapConstraint,
    GroupConstraint,
    ImportedHtmlSource,
    NonOverlapConstraint,
    OrderConstraint,
    ReadabilityConstraint,
    RenderRequest,
    SlideConstraint,
    SlideSpec,
)
from utils.slidespec_projection import projected_source_box_px


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class LayoutObservation:
    bounds: Rect
    font_size_px: float | None = None
    line_height_ratio: float | None = None
    contrast_ratio: float | None = None
    visible: bool | None = None
    truncated: bool | None = None
    z_index: float | None = None
    quad: tuple[tuple[float, float], ...] = ()
    rotation_degrees: float | None = None
    occluded_fraction: float | None = None
    glyph_clipped: bool | None = None
    line_box_clipped: bool | None = None


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    constraint_id: str
    kind: str
    code: str
    strength: str
    priority: int
    confidence: float
    targets: tuple[str, ...]
    magnitude: float = 1
    detail: str = ""

    @property
    def weighted_cost(self) -> float:
        return self.priority * self.confidence * max(0, self.magnitude)


@dataclass(frozen=True, slots=True)
class ConstraintReport:
    violations: tuple[ConstraintViolation, ...] = ()
    unknown_hard: tuple[str, ...] = ()

    @property
    def hard_failure(self) -> bool:
        return bool(self.unknown_hard) or any(
            violation.strength == "hard" for violation in self.violations
        )

    @property
    def soft_cost(self) -> float:
        return sum(
            violation.weighted_cost
            for violation in self.violations
            if violation.strength == "soft"
        )

    @property
    def primary_code(self) -> str | None:
        hard = next(
            (item.code for item in self.violations if item.strength == "hard"),
            None,
        )
        if hard is not None:
            return hard
        if self.unknown_hard:
            return "constraint.evidence.missing"
        return None


def constraint_targets(constraint: SlideConstraint) -> tuple[str, ...]:
    """Return every layout node referenced by a relational constraint."""

    if isinstance(constraint, GroupConstraint):
        return constraint.members
    if isinstance(constraint, AnchorConstraint):
        return (constraint.target, constraint.container)
    if isinstance(constraint, AlignmentConstraint):
        return constraint.targets
    if isinstance(constraint, GapConstraint):
        return (constraint.leading, constraint.trailing)
    if isinstance(constraint, ContainmentConstraint):
        return (*constraint.targets, constraint.container)
    if isinstance(constraint, NonOverlapConstraint):
        return constraint.targets
    if isinstance(constraint, OrderConstraint):
        return (constraint.before, constraint.after)
    if isinstance(constraint, ReadabilityConstraint):
        return constraint.targets
    return ()


def _has_cycle(edges: Sequence[tuple[str, str]]) -> bool:
    graph: dict[str, set[str]] = {}
    for before, after in edges:
        graph.setdefault(before, set()).add(after)
        graph.setdefault(after, set())
    active: set[str] = set()
    complete: set[str] = set()

    def visit(node: str) -> bool:
        if node in active:
            return True
        if node in complete:
            return False
        active.add(node)
        if any(visit(target) for target in graph.get(node, ())):
            return True
        active.remove(node)
        complete.add(node)
        return False

    return any(visit(node) for node in tuple(graph))


def constraint_structure_error(spec: SlideSpec) -> tuple[str, str] | None:
    """Return the first stable structural error, if any."""

    ids = [constraint.constraintId for constraint in spec.constraints]
    if len(ids) != len(set(ids)):
        return "spec.constraint_id.duplicate", ""
    layout_ids = {
        node.layoutId
        for node in _walk_layout(spec.layout)
    }
    for constraint in spec.constraints:
        missing = [
            target
            for target in constraint_targets(constraint)
            if target not in layout_ids
        ]
        if missing:
            return "spec.constraint.reference_missing", (
                f"{constraint.constraintId}:{missing[0]}"
            )
        if isinstance(constraint, AnchorConstraint):
            horizontal = {"left", "centerX", "right"}
            if (constraint.targetEdge in horizontal) != (
                constraint.containerEdge in horizontal
            ):
                return "spec.constraint.anchor_axis_mismatch", constraint.constraintId

    containment_edges = [
        (target, constraint.container)
        for constraint in spec.constraints
        if isinstance(constraint, ContainmentConstraint)
        and constraint.strength == "hard"
        for target in constraint.targets
    ]
    if _has_cycle(containment_edges):
        return "spec.constraint.containment_cycle", ""

    for order_by in ("reading", "x", "y", "z"):
        order_edges = [
            (constraint.before, constraint.after)
            for constraint in spec.constraints
            if isinstance(constraint, OrderConstraint)
            and constraint.strength == "hard"
            and constraint.orderBy == order_by
        ]
        if _has_cycle(order_edges):
            return "spec.constraint.order_cycle", order_by

    anchors: dict[tuple[str, str, str, str], list[tuple[float, float]]] = {}
    for constraint in spec.constraints:
        if not isinstance(constraint, AnchorConstraint) or constraint.strength != "hard":
            continue
        key = (
            constraint.target,
            constraint.targetEdge,
            constraint.container,
            constraint.containerEdge,
        )
        anchors.setdefault(key, []).append(
            (
                constraint.offset - constraint.tolerance,
                constraint.offset + constraint.tolerance,
            )
        )
    for ranges in anchors.values():
        if max(lower for lower, _ in ranges) > min(upper for _, upper in ranges):
            return "spec.constraint.hard_conflict", "anchor"
    # Import locally to keep the solver and evaluator independently testable.
    # The solver covers conflicts that only emerge when anchor/alignment/gap,
    # containment, non-overlap, group semantics and mutation envelopes interact.
    from utils.slidespec_solver import solve_constraints

    feasibility = solve_constraints(spec)
    if not feasibility.feasible:
        if not feasibility.search_complete:
            return "spec.constraint.feasibility_unknown", ",".join(
                feasibility.reason_codes
            )
        return "spec.constraint.unsatisfiable", ",".join(feasibility.unsat_core)
    return None


def _walk_layout(root: Any) -> Sequence[Any]:
    result = [root]
    for child in root.children:
        result.extend(_walk_layout(child))
    return result


def absolute_layout_bounds(spec: SlideSpec) -> dict[str, Rect]:
    """Resolve parent-normalized bounds into canvas-normalized rectangles."""

    result: dict[str, Rect] = {}

    def visit(node: Any, parent: Rect, *, root: bool = False) -> None:
        bounds: Bounds | None = node.bounds
        if root and bounds is None:
            current = Rect(0, 0, 1, 1)
        elif bounds is None:
            current = parent
        else:
            current = Rect(
                parent.x + bounds.x * parent.width,
                parent.y + bounds.y * parent.height,
                bounds.width * parent.width,
                bounds.height * parent.height,
            )
        result[node.layoutId] = current
        for child in node.children:
            visit(child, current)

    visit(spec.layout, Rect(0, 0, 1, 1), root=True)
    return result


def _edge(rect: Rect, edge: str) -> float:
    return {
        "left": rect.x,
        "centerX": rect.x + rect.width / 2,
        "right": rect.right,
        "top": rect.y,
        "centerY": rect.y + rect.height / 2,
        "bottom": rect.bottom,
    }[edge]


def _observation_polygon(
    target: str,
    rect: Rect,
    observations: Mapping[str, LayoutObservation] | None,
) -> tuple[tuple[float, float], ...]:
    observation = observations.get(target) if observations else None
    if observation is not None and len(observation.quad) >= 3:
        return observation.quad
    return (
        (rect.x, rect.y),
        (rect.right, rect.y),
        (rect.right, rect.bottom),
        (rect.x, rect.bottom),
    )


def _project_polygon(
    polygon: Sequence[tuple[float, float]], axis: tuple[float, float]
) -> tuple[float, float]:
    values = [x * axis[0] + y * axis[1] for x, y in polygon]
    return min(values), max(values)


def _polygons_overlap(
    left: Sequence[tuple[float, float]],
    right: Sequence[tuple[float, float]],
    *,
    tolerance: float = 0,
) -> bool:
    """Return overlap for convex browser quads using the separating-axis test."""

    for polygon in (left, right):
        for index, point in enumerate(polygon):
            following = polygon[(index + 1) % len(polygon)]
            axis = (-(following[1] - point[1]), following[0] - point[0])
            length = math.hypot(*axis)
            if length <= 1e-12:
                continue
            unit = (axis[0] / length, axis[1] / length)
            left_min, left_max = _project_polygon(left, unit)
            right_min, right_max = _project_polygon(right, unit)
            if left_max <= right_min + tolerance or right_max <= left_min + tolerance:
                return False
    return True


def _reading_order(spec: SlideSpec) -> dict[str, int]:
    by_semantic = {node.semanticId: node.readingOrder for node in spec.content}
    return {
        node.layoutId: by_semantic[node.contentSemanticId]
        for node in _walk_layout(spec.layout)
        if node.contentSemanticId in by_semantic
    }


def _violation(
    constraint: SlideConstraint,
    code: str,
    magnitude: float,
    detail: str = "",
) -> ConstraintViolation:
    return ConstraintViolation(
        constraint_id=constraint.constraintId,
        kind=constraint.kind,
        code=code,
        strength=constraint.strength,
        priority=constraint.priority,
        confidence=constraint.confidence,
        targets=constraint_targets(constraint),
        magnitude=max(0, magnitude),
        detail=detail,
    )


def _group_transition_violations(
    constraint: GroupConstraint,
    baseline_rects: Mapping[str, Rect],
    candidate_rects: Mapping[str, Rect],
    baseline_observations: Mapping[str, LayoutObservation] | None = None,
    candidate_observations: Mapping[str, LayoutObservation] | None = None,
) -> tuple[list[ConstraintViolation], bool]:
    """Compare one logical group in two coordinate spaces."""

    pairs = [
        (baseline_rects.get(member), candidate_rects.get(member))
        for member in constraint.members
    ]
    if any(before is None or after is None for before, after in pairs):
        return [], constraint.strength == "hard"

    violations: list[ConstraintViolation] = []
    deltas = [
        (after.x - before.x, after.y - before.y)
        for before, after in pairs
        if before is not None and after is not None
    ]
    tolerance = 0.002
    moved = [abs(dx) > tolerance or abs(dy) > tolerance for dx, dy in deltas]
    if constraint.atomicTranslation and any(moved):
        reference = deltas[0]
        magnitude = max(
            abs(dx - reference[0]) + abs(dy - reference[1])
            for dx, dy in deltas
        )
        if not all(moved) or magnitude > tolerance:
            violations.append(
                _violation(
                    constraint,
                    "constraint.group.atomic_translation",
                    max(tolerance, magnitude),
                )
            )
    if constraint.coordinatedScale:
        scales = [
            (after.width / before.width, after.height / before.height)
            for before, after in pairs
            if before is not None
            and after is not None
            and before.width > 1e-9
            and before.height > 1e-9
        ]
        if len(scales) != len(pairs):
            return violations, constraint.strength == "hard"
        scale_changed = any(
            abs(width - 1) > tolerance or abs(height - 1) > tolerance
            for width, height in scales
        )
        if scale_changed:
            reference = scales[0]
            magnitude = max(
                abs(width - reference[0]) + abs(height - reference[1])
                for width, height in scales
            )
            if magnitude > tolerance:
                violations.append(
                    _violation(
                        constraint,
                        "constraint.group.coordinated_scale",
                        magnitude,
                    )
                )
    if constraint.coordinatedRotation and candidate_observations is not None:
        baseline_observations = baseline_observations or candidate_observations
        rotations: list[float] = []
        for member in constraint.members:
            before = baseline_observations.get(member)
            after = candidate_observations.get(member)
            if (
                before is None
                or after is None
                or before.rotation_degrees is None
                or after.rotation_degrees is None
            ):
                return violations, constraint.strength == "hard"
            rotations.append(after.rotation_degrees - before.rotation_degrees)
        if rotations and max(rotations) - min(rotations) > 0.1:
            violations.append(
                _violation(
                    constraint,
                    "constraint.group.coordinated_rotation",
                    max(rotations) - min(rotations),
                )
            )
    if constraint.preserveInternalOrder:
        for index, left_id in enumerate(constraint.members):
            for right_id in constraint.members[index + 1 :]:
                before_left = baseline_rects[left_id]
                before_right = baseline_rects[right_id]
                after_left = candidate_rects[left_id]
                after_right = candidate_rects[right_id]
                x_flipped = (
                    abs(before_left.x - before_right.x) > tolerance
                    and (before_left.x < before_right.x)
                    != (after_left.x < after_right.x)
                )
                y_flipped = (
                    abs(before_left.y - before_right.y) > tolerance
                    and (before_left.y < before_right.y)
                    != (after_left.y < after_right.y)
                )
                if x_flipped or y_flipped:
                    violations.append(
                        _violation(
                            constraint,
                            "constraint.group.order_changed",
                            1,
                            f"{left_id}:{right_id}",
                        )
                    )
    return violations, False


def evaluate_constraints(
    spec: SlideSpec,
    *,
    observations: Mapping[str, LayoutObservation] | None = None,
    baseline_observations: Mapping[str, LayoutObservation] | None = None,
    source: ImportedHtmlSource | None = None,
) -> ConstraintReport:
    """Evaluate geometry from the spec or actual browser observations."""

    if observations is None:
        rects = absolute_layout_bounds(spec)
    else:
        rects = {layout_id: item.bounds for layout_id, item in observations.items()}
    reading = _reading_order(spec)
    imported_font = {
        binding.layoutId: binding.fontSizePx
        for binding in (source.bindings if source is not None else ())
    }
    layout_scale = {
        node.layoutId: node.fontScale for node in _walk_layout(spec.layout)
    }
    text_fit = {
        node.layoutId: node.textFitPolicy for node in _walk_layout(spec.layout)
    }
    violations: list[ConstraintViolation] = []
    unknown: list[str] = []

    def require_rect(constraint: SlideConstraint, target: str) -> Rect | None:
        rect = rects.get(target)
        if rect is None and constraint.strength == "hard" and observations is not None:
            unknown.append(constraint.constraintId)
        return rect

    for constraint in spec.constraints:
        if isinstance(constraint, GroupConstraint):
            if observations is not None:
                baseline_rects = {
                    layout_id: item.bounds
                    for layout_id, item in (
                        baseline_observations or observations
                    ).items()
                }
                group_violations, group_unknown = _group_transition_violations(
                    constraint,
                    baseline_rects,
                    rects,
                    baseline_observations,
                    observations,
                )
                violations.extend(group_violations)
                if group_unknown:
                    unknown.append(constraint.constraintId)
            continue
        if isinstance(constraint, AnchorConstraint):
            target = require_rect(constraint, constraint.target)
            container = require_rect(constraint, constraint.container)
            if target is None or container is None:
                continue
            delta = abs(
                (_edge(target, constraint.targetEdge) - _edge(container, constraint.containerEdge))
                - constraint.offset
            )
            if delta > constraint.tolerance:
                violations.append(
                    _violation(constraint, "constraint.anchor.violated", delta)
                )
            continue
        if isinstance(constraint, AlignmentConstraint):
            values = [
                _edge(rect, constraint.edge)
                for target in constraint.targets
                if (rect := require_rect(constraint, target)) is not None
            ]
            if len(values) != len(constraint.targets):
                continue
            spread = max(values) - min(values)
            if spread > constraint.tolerance:
                violations.append(
                    _violation(constraint, "constraint.alignment.violated", spread)
                )
            continue
        if isinstance(constraint, GapConstraint):
            leading = require_rect(constraint, constraint.leading)
            trailing = require_rect(constraint, constraint.trailing)
            if leading is None or trailing is None:
                continue
            gap = (
                trailing.x - leading.right
                if constraint.axis == "horizontal"
                else trailing.y - leading.bottom
            )
            outside = max(
                0.0,
                constraint.minimum - gap - constraint.tolerance,
                gap - (constraint.maximum if constraint.maximum is not None else 1)
                - constraint.tolerance,
            )
            preferred = (
                abs(gap - constraint.preferred) - constraint.tolerance
                if constraint.preferred is not None
                else 0
            )
            magnitude = max(outside, preferred if constraint.strength == "soft" else 0)
            if magnitude > 0:
                violations.append(
                    _violation(constraint, "constraint.gap.violated", magnitude)
                )
            continue
        if isinstance(constraint, ContainmentConstraint):
            container = require_rect(constraint, constraint.container)
            if container is None:
                continue
            allowed = Rect(
                container.x + constraint.insets.left * container.width,
                container.y + constraint.insets.top * container.height,
                container.width * (1 - constraint.insets.left - constraint.insets.right),
                container.height * (1 - constraint.insets.top - constraint.insets.bottom),
            )
            for target_id in constraint.targets:
                target = require_rect(constraint, target_id)
                if target is None:
                    continue
                points = _observation_polygon(target_id, target, observations)
                excess = max(
                    [0.0]
                    + [
                        max(
                            allowed.x - x - constraint.tolerance,
                            allowed.y - y - constraint.tolerance,
                            x - allowed.right - constraint.tolerance,
                            y - allowed.bottom - constraint.tolerance,
                        )
                        for x, y in points
                    ]
                )
                if excess > 0:
                    violations.append(
                        _violation(
                            constraint,
                            "constraint.containment.violated",
                            excess,
                            target_id,
                        )
                    )
            continue
        if isinstance(constraint, NonOverlapConstraint):
            for index, left_id in enumerate(constraint.targets):
                left = require_rect(constraint, left_id)
                if left is None:
                    continue
                for right_id in constraint.targets[index + 1 :]:
                    right = require_rect(constraint, right_id)
                    if right is None:
                        continue
                    left_polygon = _observation_polygon(left_id, left, observations)
                    right_polygon = _observation_polygon(right_id, right, observations)
                    overlaps = _polygons_overlap(
                        left_polygon,
                        right_polygon,
                        tolerance=-constraint.clearance + constraint.tolerance,
                    )
                    if overlaps:
                        horizontal = min(left.right, right.right) - max(left.x, right.x)
                        vertical = min(left.bottom, right.bottom) - max(left.y, right.y)
                        overlap = min(horizontal, vertical) + constraint.clearance
                        violations.append(
                            _violation(
                                constraint,
                                "constraint.non_overlap.violated",
                                max(constraint.tolerance, overlap),
                                f"{left_id}:{right_id}",
                            )
                        )
            continue
        if isinstance(constraint, OrderConstraint):
            if constraint.orderBy == "reading":
                before = reading.get(constraint.before)
                after = reading.get(constraint.after)
                if before is None or after is None:
                    if constraint.strength == "hard":
                        unknown.append(constraint.constraintId)
                    continue
                bad = before >= after
                magnitude = float(before - after + 1) if bad else 0
            elif constraint.orderBy in {"x", "y"}:
                before_rect = require_rect(constraint, constraint.before)
                after_rect = require_rect(constraint, constraint.after)
                if before_rect is None or after_rect is None:
                    continue
                gap = (
                    after_rect.x - before_rect.right
                    if constraint.orderBy == "x"
                    else after_rect.y - before_rect.bottom
                )
                magnitude = max(
                    0,
                    constraint.minimumSeparation - gap - constraint.tolerance,
                )
                bad = magnitude > 0
            else:
                before_obs = observations.get(constraint.before) if observations else None
                after_obs = observations.get(constraint.after) if observations else None
                if (
                    before_obs is None
                    or after_obs is None
                    or before_obs.z_index is None
                    or after_obs.z_index is None
                ):
                    if observations is not None and constraint.strength == "hard":
                        unknown.append(constraint.constraintId)
                    continue
                bad = before_obs.z_index >= after_obs.z_index
                magnitude = (
                    max(1, before_obs.z_index - after_obs.z_index + 1) if bad else 0
                )
            if bad:
                violations.append(
                    _violation(constraint, "constraint.order.violated", magnitude)
                )
            continue
        if isinstance(constraint, ReadabilityConstraint):
            for target_id in constraint.targets:
                observation = observations.get(target_id) if observations else None
                font_size = (
                    observation.font_size_px
                    if observation is not None
                    else (
                        imported_font.get(target_id) * layout_scale.get(target_id, 1)
                        if imported_font.get(target_id) is not None
                        else None
                    )
                )
                checks = (
                    (
                        "font_size",
                        constraint.minFontSizePx,
                        font_size,
                        lambda actual, minimum: minimum - actual,
                    ),
                    (
                        "line_height",
                        constraint.minLineHeightRatio,
                        observation.line_height_ratio if observation else None,
                        lambda actual, minimum: minimum - actual,
                    ),
                    (
                        "contrast",
                        constraint.minContrastRatio,
                        observation.contrast_ratio if observation else None,
                        lambda actual, minimum: minimum - actual,
                    ),
                )
                for name, expected, actual, difference in checks:
                    if expected is None:
                        continue
                    if actual is None:
                        if observations is not None and constraint.strength == "hard":
                            unknown.append(constraint.constraintId)
                        continue
                    magnitude = difference(actual, expected)
                    if magnitude > 0:
                        violations.append(
                            _violation(
                                constraint,
                                "constraint.readability.violated",
                                magnitude,
                                f"{target_id}:{name}",
                            )
                        )
                if observation is None:
                    if observations is not None and constraint.strength == "hard":
                        unknown.append(constraint.constraintId)
                    elif (
                        observations is None
                        and constraint.noTruncation
                        and text_fit.get(target_id) == "truncate"
                    ):
                        violations.append(
                            _violation(
                                constraint,
                                "constraint.readability.violated",
                                1,
                                f"{target_id}:text_fit",
                            )
                        )
                    continue
                if constraint.requireVisible:
                    if observation.visible is None and constraint.strength == "hard":
                        unknown.append(constraint.constraintId)
                    elif observation.visible is False:
                        violations.append(
                            _violation(
                                constraint,
                                "constraint.readability.violated",
                                1,
                                f"{target_id}:visibility",
                            )
                        )
                    if (
                        observation.occluded_fraction is None
                        and constraint.strength == "hard"
                    ):
                        unknown.append(constraint.constraintId)
                    elif (
                        observation.occluded_fraction is not None
                        and observation.occluded_fraction > 0.2
                    ):
                        violations.append(
                            _violation(
                                constraint,
                                "constraint.readability.violated",
                                observation.occluded_fraction,
                                f"{target_id}:occlusion",
                            )
                        )
                if constraint.noTruncation:
                    clipping = (
                        observation.truncated,
                        observation.glyph_clipped,
                        observation.line_box_clipped,
                    )
                    if any(value is None for value in clipping) and constraint.strength == "hard":
                        unknown.append(constraint.constraintId)
                    elif any(value is True for value in clipping):
                        violations.append(
                            _violation(
                                constraint,
                                "constraint.readability.violated",
                                1,
                                f"{target_id}:truncation",
                            )
                        )

    return ConstraintReport(
        violations=tuple(violations),
        unknown_hard=tuple(dict.fromkeys(unknown)),
    )


def has_soft_constraint_regression(
    baseline: ConstraintReport,
    candidate: ConstraintReport,
    *,
    tolerance: float = 1e-6,
) -> bool:
    """Prevent a repair from paying more weighted soft-constraint cost."""

    return candidate.soft_cost > baseline.soft_cost + tolerance


def evaluate_repair_transition(
    base: SlideSpec,
    candidate: SlideSpec,
    *,
    source: ImportedHtmlSource | None = None,
) -> ConstraintReport:
    """Validate group transition semantics plus candidate geometry."""

    base_rects = absolute_layout_bounds(base)
    candidate_rects = absolute_layout_bounds(candidate)
    violations = list(evaluate_constraints(candidate, source=source).violations)
    unknown: list[str] = []
    for constraint in base.constraints:
        if not isinstance(constraint, GroupConstraint):
            continue
        group_violations, group_unknown = _group_transition_violations(
            constraint, base_rects, candidate_rects
        )
        violations.extend(group_violations)
        if group_unknown:
            unknown.append(constraint.constraintId)
    return ConstraintReport(tuple(violations), tuple(dict.fromkeys(unknown)))


_RGB = re.compile(
    r"rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)(?:\s*,\s*(\d+(?:\.\d+)?))?\s*\)",
    re.IGNORECASE,
)


def _parse_color(value: object) -> tuple[float, float, float] | None:
    match = _RGB.fullmatch(str(value or "").strip())
    if match is None:
        return None
    if match.group(4) is not None and float(match.group(4)) < 0.999:
        return None
    return tuple(float(match.group(index)) / 255 for index in (1, 2, 3))  # type: ignore[return-value]


def _luminance(color: tuple[float, float, float]) -> float:
    channels = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in color
    ]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(foreground: object, background: object) -> float | None:
    colors = (_parse_color(foreground), _parse_color(background))
    if None in colors:
        return None
    first, second = (_luminance(color) for color in colors if color is not None)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def observations_from_browser_metrics(
    request: RenderRequest,
    metrics: Mapping[str, Any],
) -> dict[str, LayoutObservation]:
    """Map browser qid telemetry onto canonical layout ids."""

    source = request.source
    if source is None:
        return {}
    raw_elements = metrics.get("elements", metrics.get("nodes", ()))
    if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
        return {}
    by_qid = {
        str(item.get("qid", "")): item
        for item in raw_elements
        if isinstance(item, Mapping) and str(item.get("qid", ""))
    }
    viewport = metrics.get("viewport", {})
    viewport = viewport if isinstance(viewport, Mapping) else {}
    measured_width = _number(metrics.get("width")) or _number(viewport.get("width"))
    measured_height = _number(metrics.get("height")) or _number(viewport.get("height"))
    width = measured_width or source.viewportWidth
    height = measured_height or source.viewportHeight
    result: dict[str, LayoutObservation] = {}
    # The authored canvas is not represented by a qid binding, but relational
    # constraints legitimately target it as their container.  Only synthesize
    # that observation from an actual browser viewport measurement: falling
    # back to the declared source viewport here would turn missing telemetry
    # into a false success instead of preserving hard-constraint fail-closed.
    if (
        measured_width is not None
        and measured_height is not None
        and measured_width > 0
        and measured_height > 0
    ):
        result[request.spec.layout.layoutId] = LayoutObservation(
            bounds=Rect(0.0, 0.0, 1.0, 1.0),
            visible=True,
            truncated=False,
            z_index=0.0,
            quad=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
            rotation_degrees=0.0,
            occluded_fraction=0.0,
            glyph_clipped=False,
            line_box_clipped=False,
        )
    for binding in source.bindings:
        item = by_qid.get(binding.qid)
        if not isinstance(item, Mapping):
            continue
        bbox = item.get("bbox", {})
        if not isinstance(bbox, Mapping):
            continue
        x = _number(bbox.get("x"))
        y = _number(bbox.get("y"))
        box_width = _number(bbox.get("width"))
        box_height = _number(bbox.get("height"))
        if None in (x, y, box_width, box_height):
            continue
        style = item.get("computed_style", item.get("style", {}))
        style = style if isinstance(style, Mapping) else {}
        font_size = _number(item.get("font_size")) or _number(
            str(style.get("fontSize", "")).removesuffix("px")
        )
        line_height = _number(str(style.get("lineHeight", "")).removesuffix("px"))
        line_height_ratio = (
            line_height / font_size if line_height is not None and font_size else None
        )
        display = str(style.get("display", "")).lower()
        visibility = str(style.get("visibility", "")).lower()
        opacity = _number(style.get("opacity"))
        visible = (
            box_width > 0.5
            and box_height > 0.5
            and display != "none"
            and visibility not in {"hidden", "collapse"}
            and (opacity is None or opacity > 0.01)
        )
        clipped = item.get("clipped")
        if isinstance(clipped, bool):
            truncated = clipped
        else:
            client_width = _number(item.get("client_width", item.get("clientWidth")))
            client_height = _number(item.get("client_height", item.get("clientHeight")))
            scroll_width = _number(item.get("scroll_width", item.get("scrollWidth")))
            scroll_height = _number(item.get("scroll_height", item.get("scrollHeight")))
            overflow = {
                str(style.get("overflow", "")).lower(),
                str(style.get("overflowX", "")).lower(),
                str(style.get("overflowY", "")).lower(),
            }
            truncated = (
                bool(overflow.intersection({"hidden", "clip"}))
                and None not in (client_width, client_height, scroll_width, scroll_height)
                and (
                    scroll_width > client_width + 1  # type: ignore[operator]
                    or scroll_height > client_height + 1  # type: ignore[operator]
                )
            )
        direct_contrast = _number(item.get("contrast_ratio", item.get("contrastRatio")))
        contrast_ratio = (
            direct_contrast
            if direct_contrast is not None
            else _contrast(style.get("color"), style.get("backgroundColor"))
        )
        z_index = _number(
            item.get(
                "effective_z_index",
                item.get("effectiveZIndex", style.get("zIndex")),
            )
        )
        quad_value = item.get("quad", ())
        quad: list[tuple[float, float]] = []
        if isinstance(quad_value, Sequence) and not isinstance(quad_value, (str, bytes)):
            for point in quad_value:
                if isinstance(point, Mapping):
                    point_x = _number(point.get("x"))
                    point_y = _number(point.get("y"))
                elif (
                    isinstance(point, Sequence)
                    and not isinstance(point, (str, bytes))
                    and len(point) >= 2
                ):
                    point_x = _number(point[0])
                    point_y = _number(point[1])
                else:
                    continue
                if point_x is not None and point_y is not None:
                    quad.append((point_x / width, point_y / height))
        glyph_clipped = item.get("glyph_clipped", item.get("glyphClipped"))
        line_box_clipped = item.get("line_box_clipped", item.get("lineBoxClipped"))
        result[binding.layoutId] = LayoutObservation(
            bounds=Rect(x / width, y / height, box_width / width, box_height / height),
            font_size_px=font_size,
            line_height_ratio=line_height_ratio,
            contrast_ratio=contrast_ratio,
            visible=visible,
            truncated=truncated,
            z_index=z_index,
            quad=tuple(quad),
            rotation_degrees=_number(
                item.get("rotation_degrees", item.get("rotationDegrees"))
            ),
            occluded_fraction=_number(
                item.get("occluded_fraction", item.get("occludedFraction"))
            ),
            glyph_clipped=glyph_clipped if isinstance(glyph_clipped, bool) else None,
            line_box_clipped=(
                line_box_clipped if isinstance(line_box_clipped, bool) else None
            ),
        )
    return result


def imported_projection_roundtrip_errors(
    request: RenderRequest,
    metrics: Mapping[str, Any],
    *,
    pixel_tolerance: float = 2.0,
) -> tuple[str, ...]:
    """Verify that authored-HTML CSS projection survived a real browser round trip."""

    source = request.source
    if source is None:
        return ()
    raw_elements = metrics.get("elements", metrics.get("nodes", ()))
    if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
        return ("projection.telemetry.missing",)
    by_qid = {
        str(item.get("qid", "")): item
        for item in raw_elements
        if isinstance(item, Mapping) and str(item.get("qid", ""))
    }
    layout = {node.layoutId: node for node in _walk_layout(request.spec.layout)}
    binding_by_semantic = {
        binding.semanticId: binding for binding in source.bindings
    }
    content_by_semantic = {
        content.semanticId: content for content in request.spec.content
    }
    errors: list[str] = []

    def css_px(value: object) -> float | None:
        return _number(str(value or "").strip().removesuffix("px"))

    def css_number(value: object, fallback: float) -> float:
        parsed = css_px(value)
        return fallback if parsed is None else parsed

    def flow_value(value: object, *, alignment: bool) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "normal": "stretch" if alignment else "start",
            "flex-start": "start",
            "start": "start",
            "flex-end": "end",
            "end": "end",
            "center": "center",
            "stretch": "stretch",
            "space-between": "space-between",
        }
        # The importer deliberately projects CSS values outside SlideSpec's
        # closed flow vocabulary to its safe schema defaults.  Round-trip must
        # use the identical normalization or untouched values such as
        # ``align-items: baseline`` become false candidate failures.
        return aliases.get(normalized, "stretch" if alignment else "start")

    def ancestor_translation_px(binding: Any) -> tuple[float, float]:
        """Return inherited paint translation from changed authored ancestors."""

        dx = dy = 0.0
        content = content_by_semantic.get(binding.semanticId)
        parent_semantic_id = content.parentSemanticId if content is not None else None
        seen: set[str] = set()
        while parent_semantic_id and parent_semantic_id not in seen:
            seen.add(parent_semantic_id)
            parent_binding = binding_by_semantic.get(parent_semantic_id)
            if parent_binding is None:
                break
            parent_node = layout.get(parent_binding.layoutId)
            if parent_node is None or parent_node.bounds is None:
                break
            dx += (
                parent_node.bounds.x - parent_binding.bounds.x
            ) * source.viewportWidth
            dy += (
                parent_node.bounds.y - parent_binding.bounds.y
            ) * source.viewportHeight
            parent_content = content_by_semantic.get(parent_semantic_id)
            parent_semantic_id = (
                parent_content.parentSemanticId
                if parent_content is not None
                else None
            )
        return dx, dy

    def safe_resized_ancestor_reflow(
        binding: Any,
        actual: tuple[float | None, ...],
        expected: tuple[float, ...],
    ) -> bool:
        """Accept only size-preserving child reflow under an exact resized ancestor."""

        if any(value is None for value in actual):
            return False
        actual_box = tuple(float(value) for value in actual if value is not None)
        if len(actual_box) != 4 or any(
            abs(actual_box[index] - expected[index]) > pixel_tolerance
            for index in (2, 3)
        ):
            # Wrapping or glyph-box size changes remain fail closed.
            return False
        content = content_by_semantic.get(binding.semanticId)
        parent_semantic_id = content.parentSemanticId if content is not None else None
        seen: set[str] = set()
        metric_parent_qid = str(by_qid.get(binding.qid, {}).get("parent_qid", ""))
        metric_chain: set[str] = set()
        while metric_parent_qid and metric_parent_qid not in metric_chain:
            metric_chain.add(metric_parent_qid)
            parent_metric = by_qid.get(metric_parent_qid)
            metric_parent_qid = (
                str(parent_metric.get("parent_qid", ""))
                if isinstance(parent_metric, Mapping)
                else ""
            )
        while parent_semantic_id and parent_semantic_id not in seen:
            seen.add(parent_semantic_id)
            parent_binding = binding_by_semantic.get(parent_semantic_id)
            if parent_binding is None:
                return False
            parent_node = layout.get(parent_binding.layoutId)
            parent_item = by_qid.get(parent_binding.qid)
            parent_bbox = (
                parent_item.get("bbox")
                if isinstance(parent_item, Mapping)
                else None
            )
            resized = bool(
                parent_node is not None
                and parent_node.bounds is not None
                and (
                    abs(parent_node.bounds.width - parent_binding.bounds.width)
                    > 1e-9
                    or abs(parent_node.bounds.height - parent_binding.bounds.height)
                    > 1e-9
                )
            )
            if resized:
                envelope = parent_node.constraints
                allowed = set(envelope.allowedOperations or ()) if envelope else set()
                if (
                    parent_binding.qid not in metric_chain
                    or envelope is None
                    or envelope.locked
                    or envelope.lockSize
                    or "setBounds" not in allowed
                    or not isinstance(parent_bbox, Mapping)
                ):
                    return False
                parent_actual = tuple(
                    _number(parent_bbox.get(key))
                    for key in ("x", "y", "width", "height")
                )
                if any(value is None for value in parent_actual):
                    return False
                px, py, pw, ph = (
                    float(value) for value in parent_actual if value is not None
                )
                ax, ay, aw, ah = actual_box
                item = by_qid.get(binding.qid, {})
                glyph_clipped = bool(
                    item.get("glyph_clipped")
                    if isinstance(item, Mapping)
                    else True
                )
                return bool(
                    not glyph_clipped
                    and ax >= px - pixel_tolerance
                    and ay >= py - pixel_tolerance
                    and ax + aw <= px + pw + pixel_tolerance
                    and ay + ah <= py + ph + pixel_tolerance
                )
            parent_content = content_by_semantic.get(parent_semantic_id)
            parent_semantic_id = (
                parent_content.parentSemanticId
                if parent_content is not None
                else None
            )
        return False

    def track_ratios(value: object) -> tuple[float, ...] | None:
        parts = str(value or "").strip().split()
        parsed = tuple(css_px(part) for part in parts)
        if not parsed or any(item is None or item <= 0 for item in parsed):
            return None
        total = sum(float(item) for item in parsed if item is not None)
        return tuple(float(item) / total for item in parsed if item is not None)

    metrics_width = _number(metrics.get("width"))
    metrics_height = _number(metrics.get("height"))
    if (
        metrics_width is None
        or metrics_height is None
        or abs(metrics_width - source.viewportWidth) > pixel_tolerance
        or abs(metrics_height - source.viewportHeight) > pixel_tolerance
    ):
        errors.append("projection.viewport.mismatch")

    for binding in source.bindings:
        node = layout.get(binding.layoutId)
        item = by_qid.get(binding.qid)
        if node is None or node.bounds is None or not isinstance(item, Mapping):
            errors.append(f"projection.binding.missing:{binding.qid}")
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, Mapping):
            errors.append(f"projection.bounds.missing:{binding.qid}")
            continue
        inherited_x, inherited_y = ancestor_translation_px(binding)
        # Imported bounds are normalized/clamped to the canvas, while the source
        # binding retains the exact Chrome rectangle (including a deliberate
        # footer overhang).  Apply only the canonical delta to that exact source
        # rectangle; comparing every candidate to the clamped baseline creates
        # false mismatches and double-counts parent translations on descendants.
        projected_x, projected_y, projected_width, projected_height = (
            projected_source_box_px(source, node, binding)
        )
        expected = (
            projected_x + inherited_x,
            projected_y + inherited_y,
            projected_width,
            projected_height,
        )
        actual = tuple(
            _number(bbox.get(key)) for key in ("x", "y", "width", "height")
        )
        bounds_mismatch = any(value is None for value in actual) or any(
            abs(float(got) - wanted) > pixel_tolerance
            for got, wanted in zip(actual, expected, strict=True)
            if got is not None
        )
        if bounds_mismatch and not safe_resized_ancestor_reflow(
            binding, actual, expected
        ):
            errors.append(f"projection.bounds.mismatch:{binding.qid}")
        style = item.get("computed_style", item.get("style", {}))
        if not isinstance(style, Mapping):
            errors.append(f"projection.style.missing:{binding.qid}")
            continue
        checks: list[tuple[str, float, float]] = []
        if node.fontScale != 1 and binding.fontSizePx is not None:
            checks.append(
                (
                    "font-size",
                    binding.fontSizePx * node.fontScale,
                    css_number(style.get("fontSize"), -1),
                )
            )
        if abs(node.spacing - binding.gapPx) > 1e-6:
            checks.append(("gap", node.spacing, css_number(style.get("gap"), -1)))
        desired_padding = (
            node.padding.top * projected_height,
            node.padding.right * projected_width,
            node.padding.bottom * projected_height,
            node.padding.left * projected_width,
        )
        for name, wanted, css_name in zip(
            ("padding-top", "padding-right", "padding-bottom", "padding-left"),
            desired_padding,
            ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"),
            strict=True,
        ):
            checks.append((name, wanted, css_number(style.get(css_name), -1)))
        for name, wanted, got in checks:
            if abs(wanted - got) > pixel_tolerance:
                errors.append(f"projection.{name}.mismatch:{binding.qid}")
        if node.alignment != flow_value(binding.alignItems, alignment=True):
            if flow_value(style.get("alignItems"), alignment=True) != node.alignment:
                errors.append(f"projection.alignment.mismatch:{binding.qid}")
        if node.justification != flow_value(binding.justifyContent, alignment=False):
            if flow_value(style.get("justifyContent"), alignment=False) != node.justification:
                errors.append(f"projection.justification.mismatch:{binding.qid}")
        if node.columnRatio != binding.columnRatio or node.tracks:
            actual_ratios = track_ratios(style.get("gridTemplateColumns"))
            comparable_tracks = node.columnRatio or (
                tuple(track.value for track in node.tracks)
                if all(track.unit != "content" for track in node.tracks)
                else ()
            )
            mismatch = actual_ratios is None or len(actual_ratios) != len(
                node.columnRatio or node.tracks
            )
            if comparable_tracks and actual_ratios is not None:
                expected_total = sum(comparable_tracks)
                expected_ratios = tuple(
                    value / expected_total for value in comparable_tracks
                )
                mismatch = mismatch or any(
                    abs(actual - expected) > 0.02
                    for actual, expected in zip(
                        actual_ratios, expected_ratios, strict=True
                    )
                )
            if mismatch:
                errors.append(f"projection.grid-tracks.mismatch:{binding.qid}")
        if node.gridPlacement != binding.gridPlacement and node.gridPlacement is not None:
            placement = node.gridPlacement
            grid_column = str(style.get("gridColumn", "")).replace(" ", "")
            grid_row = str(style.get("gridRow", "")).replace(" ", "")
            expected_column = f"{placement.column + 1}/span{placement.columnSpan}"
            expected_row = f"{placement.row + 1}/span{placement.rowSpan}"
            if grid_column != expected_column:
                errors.append(f"projection.grid-column.mismatch:{binding.qid}")
            if grid_row != expected_row:
                errors.append(f"projection.grid-row.mismatch:{binding.qid}")
        if node.lineHeightToken is not None:
            token = next(
                (
                    item
                    for item in request.spec.tokens
                    if item.name == node.lineHeightToken
                ),
                None,
            )
            actual_line_height = css_px(style.get("lineHeight"))
            actual_font_size = css_px(style.get("fontSize"))
            if token is None or actual_line_height is None:
                errors.append(f"projection.line-height.mismatch:{binding.qid}")
            else:
                token_value = token.value
                expected_line_height: float | None = None
                if isinstance(token_value, (int, float)) and not isinstance(
                    token_value, bool
                ):
                    expected_line_height = (
                        float(token_value) * actual_font_size
                        if actual_font_size is not None
                        else None
                    )
                else:
                    expected_line_height = css_px(token_value)
                if (
                    expected_line_height is None
                    or abs(actual_line_height - expected_line_height) > pixel_tolerance
                ):
                    errors.append(f"projection.line-height.mismatch:{binding.qid}")
        if node.textFitPolicy != binding.textFitPolicy:
            actual_text_fit = {
                "white-space": str(style.get("whiteSpace", "")).lower(),
                "overflow": str(style.get("overflow", "")).lower(),
                "overflow-wrap": str(style.get("overflowWrap", "")).lower(),
                "text-overflow": str(style.get("textOverflow", "")).lower(),
            }
            text_fit_styles = {
                "none": {
                    "white-space": "nowrap",
                    "overflow": "visible",
                    "overflow-wrap": "normal",
                    "text-overflow": "clip",
                },
                "wrap": {
                    "white-space": "normal",
                    "overflow": "visible",
                    "overflow-wrap": "anywhere",
                    "text-overflow": "clip",
                },
                "truncate": {
                    "white-space": "nowrap",
                    "overflow": "hidden",
                    "overflow-wrap": "normal",
                    "text-overflow": "ellipsis",
                },
            }
            expected_text_fit = text_fit_styles.get(node.textFitPolicy)
            if expected_text_fit is None:
                errors.append(f"projection.text-fit.unsupported:{binding.qid}")
                continue
            if any(actual_text_fit.get(key) != value for key, value in expected_text_fit.items()):
                errors.append(f"projection.text-fit.mismatch:{binding.qid}")
        if node.imageFit != binding.objectFit and str(style.get("objectFit", "")) != node.imageFit:
            errors.append(f"projection.image-fit.mismatch:{binding.qid}")
        if node.cropFocalPoint != binding.cropFocalPoint:
            expected_position = (
                f"{node.cropFocalPoint.x * 100:g}% {node.cropFocalPoint.y * 100:g}%"
            )
            if str(style.get("objectPosition", "")).strip() != expected_position:
                errors.append(f"projection.crop.mismatch:{binding.qid}")
    return tuple(errors)


__all__ = [
    "ConstraintReport",
    "ConstraintViolation",
    "LayoutObservation",
    "Rect",
    "absolute_layout_bounds",
    "constraint_targets",
    "constraint_structure_error",
    "evaluate_constraints",
    "evaluate_repair_transition",
    "imported_projection_roundtrip_errors",
    "has_soft_constraint_regression",
    "observations_from_browser_metrics",
]
