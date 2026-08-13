"""Deterministic candidate ordering and Pareto planning policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from models.slidespec_v1 import RepairPlan
from services.slidespec_pipeline_performance import (
    INSPECTION_CACHE_VERSION,
    _canonical_digest,
    digest_parts,
)
from services.slidespec_repair import repair_cost

def adjacent_influence_digests(signatures: Sequence[object]) -> tuple[str, ...]:
    """Return a digest that invalidates a changed slide and its direct neighbors."""

    local = tuple(digest_parts("slide-source/1", value) for value in signatures)
    return tuple(
        digest_parts(
            "deck-adjacency/1",
            local[index - 1] if index > 0 else None,
            local[index],
            local[index + 1] if index + 1 < len(local) else None,
        )
        for index in range(len(local))
    )


def inspection_cache_digest(
    *,
    spec_digest: str,
    content_digest: str,
    source_digest: str,
    font_digest: str,
    browser_digest: str,
    adjacent_digest: str,
) -> str:
    return digest_parts(
        INSPECTION_CACHE_VERSION,
        spec_digest,
        content_digest,
        source_digest,
        font_digest,
        browser_digest,
        adjacent_digest,
    )


def _plan_identity(plan: RepairPlan) -> str:
    return _canonical_digest(plan.model_dump(mode="json", by_alias=True, exclude_none=True))


def deterministic_candidate_order(plans: Sequence[RepairPlan]) -> tuple[RepairPlan, ...]:
    """Deduplicate and totally order candidates independently of provider order."""

    unique: dict[str, RepairPlan] = {}
    for plan in plans:
        identity = _plan_identity(plan)
        unique.setdefault(identity, plan)
    return tuple(
        sorted(
            unique.values(),
            key=lambda plan: (repair_cost(plan).sort_key, _plan_identity(plan)),
        )
    )


def _coverage(plan: RepairPlan) -> frozenset[str]:
    if plan.diagnostic_refs:
        return frozenset(plan.diagnostic_refs)
    return frozenset(str(target) for target in plan.scope)


def _sign(value: object, *, origin: float = 0.0) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0
    delta = float(value) - origin
    return 1 if delta > 1e-9 else -1 if delta < -1e-9 else 0


def _operation_effect_direction(operation: Mapping[str, object]) -> tuple[object, ...]:
    """Classify mutation direction without treating magnitude as equivalence.

    Pre-render dominance is only sound when both candidates mutate the same
    owners in the same way.  In particular, an inexpensive leaf translation
    cannot prove that an ancestor/composition translation is redundant.
    """

    kind = str(operation.get("op", ""))
    value = operation.get("value")
    value_map = value if isinstance(value, Mapping) else {}
    precondition = operation.get("precondition")
    precondition_map = precondition if isinstance(precondition, Mapping) else {}
    current = precondition_map.get("expectedBounds", precondition_map.get("current"))
    current_map = current if isinstance(current, Mapping) else {}

    def number(name: str) -> object:
        return operation.get(name, value_map.get(name))

    if kind in {"translateWithinParent", "translateBounds", "translate"}:
        return (
            _sign(number("x") if number("dx") is None else number("dx")),
            _sign(number("y") if number("dy") is None else number("dy")),
        )
    if kind == "setFontScale":
        return (_sign(number("scale"), origin=1.0),)
    if kind in {"setBounds", "resizeWithinBounds", "resizeBounds"}:
        proposed = operation.get("bounds")
        proposed_map = (
            proposed
            if isinstance(proposed, Mapping)
            else operation
            if kind == "resizeWithinBounds"
            else value_map
        )
        return tuple(
            _sign(proposed_map.get(axis), origin=float(current_map.get(axis, 0.0)))
            if isinstance(current_map.get(axis), (int, float))
            else 0
            for axis in ("x", "y", "width", "height")
        )
    if kind == "setSpacing":
        current_gap = precondition_map.get(
            "expectedValue", current_map.get("gap", precondition_map.get("gap"))
        )
        return (
            _sign(number("gap"), origin=float(current_gap))
            if isinstance(current_gap, (int, float))
            else 0,
        )
    # Categorical/structured effects (flow, crop, routing, subtree replacement,
    # etc.) have no safe scalar direction.  Requiring the exact payload keeps
    # alternatives incomparable unless they are genuinely identical.
    effect = {
        key: item
        for key, item in operation.items()
        if key not in {"op", "target", "precondition"}
    }
    return (_canonical_digest(effect),)


def _mutation_equivalence_class(plan: RepairPlan) -> tuple[object, ...]:
    operations = tuple(
        operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        for operation in plan.operations
    )
    targets = tuple(sorted({str(operation.get("target", "")) for operation in operations}))
    effects = tuple(
        sorted(
            (
                str(operation.get("target", "")),
                str(operation.get("op", "")),
                _operation_effect_direction(operation),
            )
            for operation in operations
        )
    )
    return targets, effects


def pareto_prune_plans(
    plans: Sequence[RepairPlan],
) -> tuple[tuple[RepairPlan, ...], tuple[RepairPlan, ...]]:
    """Remove candidates dominated on coverage and every pre-render cost axis."""

    ordered = deterministic_candidate_order(plans)

    def dominates(left: RepairPlan, right: RepairPlan) -> bool:
        # Coverage equality alone is not semantic equivalence: candidates that
        # repair the same issue via a leaf, ancestor, or coordinated composition
        # can have materially different browser outcomes.  Only prune within an
        # exact owner/family/direction class; every other pair is incomparable.
        if _mutation_equivalence_class(left) != _mutation_equivalence_class(right):
            return False
        left_coverage = _coverage(left)
        right_coverage = _coverage(right)
        if not right_coverage or not left_coverage.issuperset(right_coverage):
            return False
        left_cost = repair_cost(left).sort_key[:-1]
        right_cost = repair_cost(right).sort_key[:-1]
        return all(a <= b for a, b in zip(left_cost, right_cost, strict=True)) and any(
            a < b for a, b in zip(left_cost, right_cost, strict=True)
        )

    survivors: list[RepairPlan] = []
    pruned: list[RepairPlan] = []
    for candidate in ordered:
        if any(other is not candidate and dominates(other, candidate) for other in ordered):
            pruned.append(candidate)
        else:
            survivors.append(candidate)
    return tuple(survivors), tuple(pruned)
