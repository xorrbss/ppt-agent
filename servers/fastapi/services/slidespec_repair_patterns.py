"""Strict deck-scoped pattern discovery and target-independent repair recipes.

Pattern reuse is only an ordering optimisation.  A rebound plan still traverses
the normal schema, invariant, render, OCR/VQA, publication and read-back gates.
Rendered artifacts, slide-local identifiers and absolute geometry are never
stored in a recipe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Literal

from models.slidespec_v1 import REPAIR_PLAN_SCHEMA, LayoutNode, RenderRequest, RepairPlan
from services.slidespec_repair_pipeline import DomEvidence


PATTERN_POLICY_VERSION = "repair-pattern/2.0"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _TargetSelector:
    target_kind: Literal["layout", "semantic"]
    path: tuple[int, ...]
    shape: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RecipeOperation:
    selector: _TargetSelector
    operation: str
    adjustment: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RepairRecipe:
    recipe_id: str
    strategy: str
    operations: tuple[_RecipeOperation, ...]
    budget: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PatternObservation:
    """Cheap whole-deck inspection output used before any mutation starts."""

    position: int
    slide_id: str
    pattern_key: str
    text_density: float
    available_space: float
    diagnostic_family: str = "diagnostic-family:unknown"

    @property
    def pressure(self) -> float:
        return math.log1p(max(0.0, self.text_density)) - self.available_space


@dataclass(slots=True)
class _RecipeRecord:
    recipe: RepairRecipe
    validated_representatives: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _LayoutIndexes:
    by_id: dict[str, LayoutNode]
    by_semantic: dict[str, LayoutNode]
    path_by_id: dict[str, tuple[int, ...]]
    by_path: dict[tuple[int, ...], LayoutNode]
    parent_by_id: dict[str, LayoutNode | None]
    content_kind_by_semantic: dict[str, str]
    content_text_by_semantic: dict[str, str]
    source_binding_by_semantic: dict[str, Any]


def _layout_indexes(request: RenderRequest) -> _LayoutIndexes:
    by_id: dict[str, LayoutNode] = {}
    by_semantic: dict[str, LayoutNode] = {}
    path_by_id: dict[str, tuple[int, ...]] = {}
    by_path: dict[tuple[int, ...], LayoutNode] = {}
    parent_by_id: dict[str, LayoutNode | None] = {}

    def visit(
        node: LayoutNode,
        path: tuple[int, ...],
        parent: LayoutNode | None,
    ) -> None:
        by_id[node.layoutId] = node
        path_by_id[node.layoutId] = path
        by_path[path] = node
        parent_by_id[node.layoutId] = parent
        if node.contentSemanticId is not None:
            by_semantic[node.contentSemanticId] = node
        for index, child in enumerate(node.children):
            visit(child, (*path, index), node)

    visit(request.spec.layout, (), None)
    return _LayoutIndexes(
        by_id=by_id,
        by_semantic=by_semantic,
        path_by_id=path_by_id,
        by_path=by_path,
        parent_by_id=parent_by_id,
        content_kind_by_semantic={
            node.semanticId: node.kind for node in request.spec.content
        },
        content_text_by_semantic={
            node.semanticId: node.text or "" for node in request.spec.content
        },
        source_binding_by_semantic={
            binding.semanticId: binding
            for binding in (request.source.bindings if request.source is not None else ())
        },
    )


def _constraint_shape(node: LayoutNode) -> str:
    constraints = node.constraints
    if constraints is None:
        return "open"
    return _digest(
        {
            "locked": constraints.locked,
            "allowed": (
                "*"
                if constraints.allowedOperations is None
                else sorted(constraints.allowedOperations)
            ),
            "lock_position": constraints.lockPosition,
            "lock_size": constraints.lockSize,
            "preserve_aspect": constraints.preserveAspectRatio,
            "min_width": constraints.minWidth,
            "min_height": constraints.minHeight,
            "max_width": constraints.maxWidth,
            "max_height": constraints.maxHeight,
            "min_font_scale": constraints.minFontScale,
            "max_font_shrink": constraints.maxFontShrink,
            "max_translation": constraints.maxTranslation,
            "max_resize_delta": constraints.maxResizeDelta,
        }
    )


def _node_shape(indexes: _LayoutIndexes, node: LayoutNode) -> tuple[str, ...]:
    semantic_id = node.contentSemanticId
    parent = indexes.parent_by_id.get(node.layoutId)
    binding = (
        indexes.source_binding_by_semantic.get(semantic_id)
        if semantic_id is not None
        else None
    )
    return (
        node.kind,
        parent.kind if parent is not None else "root",
        indexes.content_kind_by_semantic.get(semantic_id or "", "container"),
        getattr(binding, "tag", "native"),
        getattr(binding, "display", "native"),
        getattr(binding, "position", "native"),
        getattr(binding, "overflow", "native"),
        getattr(binding, "whiteSpace", "native"),
        getattr(binding, "overflowWrap", "native"),
        node.textFitPolicy,
        node.alignment,
        node.justification,
        ",".join(track.unit for track in node.tracks) or "no-tracks",
        f"columns:{len(node.columnRatio)}",
        "grid-placed" if node.gridPlacement is not None else "grid-auto",
        (
            f"intent:{node.intent.role}:{node.intent.alignmentGroup}:"
            f"{node.intent.intentionalOverlapGroup}:{node.intent.allowIntentionalOverlap}"
            if node.intent is not None
            else "intent:none"
        ),
        _constraint_shape(node),
    )


def _tree_shape(indexes: _LayoutIndexes, node: LayoutNode) -> Mapping[str, Any]:
    return {
        "shape": _node_shape(indexes, node),
        "children": [_tree_shape(indexes, child) for child in node.children],
    }


def _target_selector(
    indexes: _LayoutIndexes, target: str
) -> _TargetSelector | None:
    target_kind: Literal["layout", "semantic"]
    node = indexes.by_id.get(target)
    if node is not None:
        target_kind = "layout"
    else:
        node = indexes.by_semantic.get(target)
        target_kind = "semantic"
    if node is None:
        return None
    return _TargetSelector(
        target_kind=target_kind,
        path=indexes.path_by_id[node.layoutId],
        shape=_node_shape(indexes, node),
    )


def _resolve_selector(
    indexes: _LayoutIndexes, selector: _TargetSelector
) -> tuple[str, LayoutNode] | None:
    """Resolve only an exact tree path plus shape; never guess a look-alike."""

    node = indexes.by_path.get(selector.path)
    if node is None or _node_shape(indexes, node) != selector.shape:
        return None
    if selector.target_kind == "semantic":
        if node.contentSemanticId is None:
            return None
        return node.contentSemanticId, node
    return node.layoutId, node


def _defect_selector(
    indexes: _LayoutIndexes, target: str
) -> Mapping[str, Any]:
    selector = _target_selector(indexes, target)
    if selector is None:
        return {"unbound": True}
    return {"path": selector.path, "shape": selector.shape}


def _normalize_constraint_value(
    indexes: _LayoutIndexes, value: object
) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_constraint_value(indexes, item)
            for key, item in value.items()
            if str(key) not in {"constraintId", "constraint_id"}
        }
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_constraint_value(indexes, item) for item in value)
    if isinstance(value, str):
        selector = _target_selector(indexes, value)
        if selector is not None:
            return {
                "target_kind": selector.target_kind,
                "path": selector.path,
                "shape": selector.shape,
            }
    return value


def repair_pattern_key(request: RenderRequest, evidence: DomEvidence) -> str:
    """Return a strict fingerprint with no slide-local IDs or absolute bounds."""

    indexes = _layout_indexes(request)
    defects: list[dict[str, Any]] = []
    for defect in evidence.defects:
        if defect.code == "constraint.evidence.missing" or defect.severity not in {
            "high",
            "medium",
        }:
            continue
        defects.append(
            {
                "code": defect.code,
                "severity": defect.severity,
                "target": _defect_selector(indexes, defect.semantic_id),
                "related": sorted(
                    (
                        _defect_selector(indexes, semantic_id)
                        for semantic_id in defect.related_semantic_ids
                    ),
                    key=_digest,
                ),
            }
        )
    text_area = sum(
        node.bounds.width * node.bounds.height
        for node in indexes.by_id.values()
        if node.bounds is not None
        and node.contentSemanticId is not None
        and indexes.content_text_by_semantic.get(node.contentSemanticId)
    )
    text_characters = sum(
        len(indexes.content_text_by_semantic.get(node.contentSemanticId or "", ""))
        for node in indexes.by_id.values()
    )
    text_density = text_characters / max(text_area, 1e-6)
    density_band = (
        "low" if text_density < 300 else "medium" if text_density < 900 else "high"
    )
    payload = {
        "defects": sorted(defects, key=_digest),
        "layout": _tree_shape(indexes, request.spec.layout),
        "text_density_band": density_band,
        "constraint_footprint": sorted(
            (
                {
                    "kind": constraint.kind,
                    "strength": constraint.strength,
                    "shape": _normalize_constraint_value(
                        indexes,
                        constraint.model_dump(mode="json", by_alias=True),
                    ),
                }
                for constraint in request.spec.constraints
            ),
            key=_digest,
        ),
    }
    return "repair-pattern:" + _digest(payload)


def repair_diagnostic_family(
    request: RenderRequest, evidence: DomEvidence
) -> str:
    """Coarse family for ordering/learning, never for recipe publication."""

    indexes = _layout_indexes(request)
    family: list[tuple[str, str, str, str]] = []
    for defect in evidence.defects:
        if defect.code == "constraint.evidence.missing" or defect.severity not in {
            "high",
            "medium",
        }:
            continue
        node = indexes.by_semantic.get(defect.semantic_id) or indexes.by_id.get(
            defect.semantic_id
        )
        parent = indexes.parent_by_id.get(node.layoutId) if node is not None else None
        family.append(
            (
                defect.code,
                defect.severity,
                node.kind if node is not None else "unbound",
                parent.kind if parent is not None else "root",
            )
        )
    return "diagnostic-family:" + _digest(sorted(family))


def pattern_observation(
    position: int, request: RenderRequest, evidence: DomEvidence
) -> PatternObservation | None:
    actionable = tuple(
        defect
        for defect in evidence.defects
        if defect.code != "constraint.evidence.missing"
        and defect.severity in {"high", "medium"}
    )
    if not actionable:
        return None
    indexes = _layout_indexes(request)
    text_area = 0.0
    occupied_area = 0.0
    text_characters = 0
    for node in indexes.by_id.values():
        if node.bounds is None:
            continue
        area = node.bounds.width * node.bounds.height
        if not node.children:
            occupied_area += area
        semantic_id = node.contentSemanticId
        if semantic_id is not None and indexes.content_text_by_semantic.get(semantic_id):
            text_area += area
            text_characters += len(indexes.content_text_by_semantic.get(semantic_id, ""))
    return PatternObservation(
        position=position,
        slide_id=request.spec.slideId,
        pattern_key=repair_pattern_key(request, evidence),
        text_density=round(text_characters / max(text_area, 1e-6), 6),
        available_space=round(max(0.0, 1.0 - min(1.0, occupied_area)), 6),
        diagnostic_family=repair_diagnostic_family(request, evidence),
    )


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _compile_adjustment(
    node: LayoutNode, raw: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any]] | None:
    operation = str(raw.get("op", ""))
    if operation == "setBounds":
        bounds = raw.get("bounds")
        if node.bounds is None or not isinstance(bounds, Mapping):
            return None
        values = tuple(_number(bounds.get(axis)) for axis in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            return None
        source = node.bounds
        return operation, {
            "mode": "bounds_relative",
            "x_delta_widths": (float(values[0]) - source.x) / source.width,
            "y_delta_heights": (float(values[1]) - source.y) / source.height,
            "width_ratio": float(values[2]) / source.width,
            "height_ratio": float(values[3]) / source.height,
        }
    if operation == "translateWithinParent":
        point = raw.get("value")
        if node.bounds is None or not isinstance(point, Mapping):
            return None
        x = _number(point.get("x"))
        y = _number(point.get("y"))
        if x is None or y is None:
            return None

        def slack_fraction(delta: float, before: float, size: float) -> float:
            slack = 1.0 - before - size if delta >= 0 else before
            return delta / max(slack, 1e-9)

        return operation, {
            "mode": "slack_fraction",
            "x": slack_fraction(x, node.bounds.x, node.bounds.width),
            "y": slack_fraction(y, node.bounds.y, node.bounds.height),
        }
    if operation == "resizeWithinBounds":
        if node.bounds is None:
            return None
        width = _number(raw.get("width"))
        height = _number(raw.get("height"))
        if width is None or height is None:
            return None
        return operation, {
            "mode": "size_ratio",
            "width": width / node.bounds.width,
            "height": height / node.bounds.height,
        }
    if operation == "setFontScale":
        scale = _number(raw.get("scale"))
        if scale is None:
            return None
        payload: dict[str, Any] = {
            "mode": "font_ratio",
            "ratio": scale / node.fontScale,
        }
        if raw.get("bounds") is not None:
            payload["bounds"] = deepcopy(raw["bounds"])
        return operation, payload
    if operation == "setSpacing":
        gap = _number(raw.get("gap"))
        if gap is None:
            return None
        return operation, {"mode": "spacing_delta", "delta": gap - node.spacing}
    if operation == "setPadding":
        padding = raw.get("padding")
        if not isinstance(padding, Mapping):
            return None
        current = node.padding.model_dump(mode="json", by_alias=True)
        values = {key: _number(padding.get(key)) for key in current}
        if any(value is None for value in values.values()):
            return None
        return operation, {
            "mode": "padding_delta",
            "delta": {key: float(values[key]) - float(current[key]) for key in current},
        }
    if operation in {
        "setAlignment",
        "setJustification",
        "setTrackSize",
        "setColumnRatio",
        "setGridPlacement",
        "setLineHeightToken",
        "setTextFitPolicy",
        "setImageFit",
        "setCropFocalPoint",
    }:
        value = raw.get("value")
        if value is None:
            return None
        return operation, {"mode": "structural_value", "value": deepcopy(value)}
    # Connector points, replacement subtrees and unknown operations can embed
    # absolute geometry or slide-local IDs and are intentionally not reusable.
    return None


def _compile_recipe(request: RenderRequest, plan: RepairPlan) -> RepairRecipe | None:
    indexes = _layout_indexes(request)
    operations: list[_RecipeOperation] = []
    for operation in plan.operations:
        raw = operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        target = str(raw.pop("target", ""))
        raw.pop("precondition", None)
        selector = _target_selector(indexes, target)
        node = indexes.by_id.get(target) or indexes.by_semantic.get(target)
        if selector is None or node is None:
            return None
        compiled = _compile_adjustment(node, raw)
        if compiled is None:
            return None
        operation_name, adjustment = compiled
        operations.append(_RecipeOperation(selector, operation_name, adjustment))
    if not operations:
        return None
    payload = {
        "strategy": plan.strategy,
        "operations": [
            {
                "selector": {
                    "target_kind": operation.selector.target_kind,
                    "path": operation.selector.path,
                    "shape": operation.selector.shape,
                },
                "operation": operation.operation,
                "adjustment": operation.adjustment,
            }
            for operation in operations
        ],
    }
    return RepairRecipe(
        recipe_id="recipe:" + _digest(payload)[:24],
        strategy=plan.strategy,
        operations=tuple(operations),
        budget=plan.budget.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


def _bounded(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _instantiate_adjustment(
    node: LayoutNode, operation: str, adjustment: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    mode = adjustment.get("mode")
    constraints = node.constraints
    if mode == "bounds_relative" and node.bounds is not None:
        values = {
            "x": node.bounds.x
            + float(adjustment.get("x_delta_widths", 0.0)) * node.bounds.width,
            "y": node.bounds.y
            + float(adjustment.get("y_delta_heights", 0.0)) * node.bounds.height,
            "width": node.bounds.width * float(adjustment.get("width_ratio", 1.0)),
            "height": node.bounds.height * float(adjustment.get("height_ratio", 1.0)),
        }
        values["width"] = _bounded(
            values["width"], constraints.minWidth if constraints else 1e-6,
            constraints.maxWidth if constraints else 1.0,
        )
        values["height"] = _bounded(
            values["height"], constraints.minHeight if constraints else 1e-6,
            constraints.maxHeight if constraints else 1.0,
        )
        values["x"] = _bounded(values["x"], 0.0, 1.0 - values["width"])
        values["y"] = _bounded(values["y"], 0.0, 1.0 - values["height"])
        return {"op": operation, "bounds": values}
    if mode == "slack_fraction" and node.bounds is not None:
        max_translation = constraints.maxTranslation if constraints else 0.08

        def translate(fraction: float, before: float, size: float) -> float:
            slack = 1.0 - before - size if fraction >= 0 else before
            return _bounded(fraction * slack, -max_translation, max_translation)

        return {
            "op": operation,
            "value": {
                "x": translate(float(adjustment.get("x", 0.0)), node.bounds.x, node.bounds.width),
                "y": translate(float(adjustment.get("y", 0.0)), node.bounds.y, node.bounds.height),
            },
        }
    if mode == "size_ratio" and node.bounds is not None:
        return {
            "op": operation,
            "width": _bounded(node.bounds.width * float(adjustment.get("width", 1.0)), 1e-6, 1.0),
            "height": _bounded(node.bounds.height * float(adjustment.get("height", 1.0)), 1e-6, 1.0),
        }
    if mode == "font_ratio":
        scale = node.fontScale * float(adjustment.get("ratio", 1.0))
        if constraints is not None:
            scale = max(scale, constraints.minFontScale)
        payload: dict[str, Any] = {"op": operation, "scale": _bounded(scale, 0.5, 2.0)}
        if adjustment.get("bounds") is not None:
            payload["bounds"] = deepcopy(adjustment["bounds"])
        return payload
    if mode == "spacing_delta":
        return {"op": operation, "gap": _bounded(node.spacing + float(adjustment.get("delta", 0.0)), 0.0, 512.0)}
    if mode == "padding_delta":
        delta = adjustment.get("delta")
        if not isinstance(delta, Mapping):
            return None
        current = node.padding.model_dump(mode="json", by_alias=True)
        return {
            "op": operation,
            "padding": {
                key: _bounded(float(current[key]) + float(delta.get(key, 0.0)), 0.0, 1.0)
                for key in current
            },
        }
    if mode == "structural_value":
        return {"op": operation, "value": deepcopy(adjustment.get("value"))}
    return None


def _instantiate_recipe(
    recipe: RepairRecipe, request: RenderRequest
) -> RepairPlan | None:
    indexes = _layout_indexes(request)
    operations: list[dict[str, Any]] = []
    scope: list[str] = []
    for recipe_operation in recipe.operations:
        resolved = _resolve_selector(indexes, recipe_operation.selector)
        if resolved is None:
            return None
        target, node = resolved
        payload = _instantiate_adjustment(
            node, recipe_operation.operation, recipe_operation.adjustment
        )
        if payload is None:
            return None
        operations.append({**payload, "target": target})
        scope.append(target)
    identity = _digest(
        {
            "recipe": recipe.recipe_id,
            "slide": request.spec.slideId,
            "revision": request.spec.specRevision,
            "operations": operations,
        }
    )
    try:
        return RepairPlan.model_validate(
            {
                "$schema": REPAIR_PLAN_SCHEMA,
                "planId": f"rp_pattern_{identity[:20]}",
                "slideId": request.spec.slideId,
                "baseSpecRevision": request.spec.specRevision,
                "baseSpecHash": request.spec.digests.spec,
                "expectedContentDigest": request.spec.digests.content,
                "scope": list(dict.fromkeys(scope)),
                "strategy": recipe.strategy,
                "operations": operations,
                "limits": dict(recipe.budget),
                "proposedBy": {
                    "kind": "rule_engine",
                    "policyVersion": PATTERN_POLICY_VERSION,
                    "costVersion": recipe.recipe_id,
                },
            }
        )
    except ValueError:
        return None


def _representatives(
    observations: Sequence[PatternObservation],
) -> tuple[PatternObservation, ...]:
    ordered = sorted(
        observations,
        key=lambda item: (
            item.pressure,
            item.text_density,
            -item.available_space,
            item.position,
        ),
    )
    if len(ordered) <= 3:
        return tuple(ordered)
    indexes = (0, (len(ordered) - 1) // 2, len(ordered) - 1)
    return tuple(ordered[index] for index in indexes)


class RepairPatternRegistry:
    """Request-local registry prepared by one whole-deck inspection pass."""

    def __init__(self) -> None:
        self._records: dict[str, list[_RecipeRecord]] = {}
        self._members: dict[str, tuple[PatternObservation, ...]] = {}
        self._representatives: dict[str, tuple[PatternObservation, ...]] = {}
        self._slide_patterns: dict[str, str] = {}
        self._diagnostic_families: dict[str, tuple[PatternObservation, ...]] = {}
        self._reuse_attempts = 0
        self._reuse_instantiated = 0
        self._reuse_applied = 0
        self._preinspection_count = 0
        self._preinspection_ms = 0.0
        self._preinspection_cache_hits = 0
        self._preinspection_cache_misses = 0
        self._llm_claimed_patterns: set[str] = set()

    def prepare(
        self,
        observations: Sequence[PatternObservation],
        *,
        elapsed_ms: float = 0.0,
        cache_hits: int = 0,
        cache_misses: int = 0,
        inspected_count: int | None = None,
    ) -> None:
        grouped: dict[str, list[PatternObservation]] = {}
        for observation in observations:
            grouped.setdefault(observation.pattern_key, []).append(observation)
            self._slide_patterns[observation.slide_id] = observation.pattern_key
        self._members = {
            key: tuple(sorted(items, key=lambda item: item.position))
            for key, items in grouped.items()
        }
        coarse_grouped: dict[str, list[PatternObservation]] = {}
        for observation in observations:
            coarse_grouped.setdefault(observation.diagnostic_family, []).append(
                observation
            )
        self._diagnostic_families = {
            key: tuple(sorted(items, key=lambda item: item.position))
            for key, items in coarse_grouped.items()
        }
        self._representatives = {
            key: _representatives(items)
            for key, items in self._members.items()
            if len(items) > 1
        }
        self._preinspection_count = max(
            len(observations),
            len(observations) if inspected_count is None else inspected_count,
        )
        self._preinspection_ms = max(0.0, elapsed_ms)
        self._preinspection_cache_hits = max(0, cache_hits)
        self._preinspection_cache_misses = max(0, cache_misses)

    def observe(self, pattern_key: str) -> None:
        """Compatibility no-op: clusters are immutable after ``prepare``."""

    def claim_llm(self, pattern_key: str) -> bool:
        """Allow at most one bounded model proposal per strict pattern/request."""

        if pattern_key in self._llm_claimed_patterns:
            return False
        self._llm_claimed_patterns.add(pattern_key)
        return True

    def representative_waves(self) -> tuple[tuple[int, ...], ...]:
        maximum = max((len(items) for items in self._representatives.values()), default=0)
        return tuple(
            tuple(
                sorted(
                    items[index].position
                    for items in self._representatives.values()
                    if index < len(items)
                )
            )
            for index in range(maximum)
        )

    def _representative_ids(self, pattern_key: str) -> set[str]:
        return {
            item.slide_id for item in self._representatives.get(pattern_key, ())
        }

    def instantiate(
        self,
        pattern_key: str,
        request: RenderRequest,
    ) -> tuple[RepairPlan, ...]:
        if self._slide_patterns.get(request.spec.slideId) != pattern_key:
            return ()
        representatives = self._representative_ids(pattern_key)
        if not representatives:
            return ()  # singleton clusters always use the individual path
        is_representative = request.spec.slideId in representatives
        records = self._records.get(pattern_key, ())
        eligible = tuple(
            record
            for record in records
            if is_representative
            or representatives.issubset(record.validated_representatives)
        )
        if not eligible:
            return ()
        self._reuse_attempts += 1
        plans = tuple(
            plan
            for record in eligible
            if (plan := _instantiate_recipe(record.recipe, request)) is not None
        )
        if plans:
            self._reuse_instantiated += 1
        return plans

    def record_applied(
        self,
        pattern_key: str,
        request: RenderRequest,
        plan: RepairPlan,
    ) -> None:
        if self._slide_patterns.get(request.spec.slideId) != pattern_key:
            return
        representative_ids = self._representative_ids(pattern_key)
        if request.spec.slideId not in representative_ids:
            if plan.provenance.policy_version == PATTERN_POLICY_VERSION:
                self._reuse_applied += 1
            return
        records = self._records.setdefault(pattern_key, [])
        if plan.provenance.policy_version == PATTERN_POLICY_VERSION:
            recipe_id = plan.provenance.cost_version
            for record in records:
                if record.recipe.recipe_id == recipe_id:
                    record.validated_representatives.add(request.spec.slideId)
                    self._reuse_applied += 1
                    return
            return
        recipe = _compile_recipe(request, plan)
        if recipe is None:
            return
        for record in records:
            if record.recipe.recipe_id == recipe.recipe_id:
                record.validated_representatives.add(request.spec.slideId)
                return
        records.append(
            _RecipeRecord(recipe, validated_representatives={request.spec.slideId})
        )
        records.sort(key=lambda item: item.recipe.recipe_id)

    def metrics(self) -> dict[str, int | float]:
        active_recipe_count = 0
        candidate_recipe_count = 0
        for pattern_key, records in self._records.items():
            representatives = self._representative_ids(pattern_key)
            candidate_recipe_count += len(records)
            active_recipe_count += sum(
                representatives.issubset(record.validated_representatives)
                for record in records
            )
        return {
            "pattern_cluster_count": len(self._members),
            "pattern_diagnostic_family_count": len(self._diagnostic_families),
            "pattern_singleton_count": sum(
                len(items) == 1 for items in self._members.values()
            ),
            "pattern_representative_count": sum(
                len(items) for items in self._representatives.values()
            ),
            "pattern_recipe_count": active_recipe_count,
            "pattern_recipe_candidate_count": candidate_recipe_count,
            "pattern_reuse_attempts": self._reuse_attempts,
            "pattern_reuse_hits": self._reuse_instantiated,
            "pattern_reuse_applied": self._reuse_applied,
            "pattern_preinspection_count": self._preinspection_count,
            "pattern_preinspection_ms": round(self._preinspection_ms, 3),
            "pattern_preinspection_cache_hits": self._preinspection_cache_hits,
            "pattern_preinspection_cache_misses": self._preinspection_cache_misses,
        }


__all__ = [
    "PATTERN_POLICY_VERSION",
    "PatternObservation",
    "RepairPatternRegistry",
    "pattern_observation",
    "repair_diagnostic_family",
    "repair_pattern_key",
]
