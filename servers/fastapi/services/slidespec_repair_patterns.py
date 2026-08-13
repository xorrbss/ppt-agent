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


from services.slidespec_repair_pattern_index import (
    _constraint_shape,
    _layout_indexes,
    _node_shape,
    _resolve_selector,
    _target_selector,
    _tree_shape,
)



from services.slidespec_repair_pattern_identity import (
    _defect_selector,
    _normalize_constraint_value,
    pattern_observation,
    repair_diagnostic_family,
    repair_pattern_key,
)



from services.slidespec_repair_pattern_recipe import (
    _compile_adjustment,
    _compile_recipe,
    _number,
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
# ruff: noqa: E402, F401
