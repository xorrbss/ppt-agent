"""Compose deterministic repair plans with disjoint conflict footprints."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import hashlib
import json

from models.slidespec_v1 import RepairPlan
from services.slidespec_pipeline_performance import deterministic_candidate_order
from services.slidespec_repair_pipeline import RepairConflictFootprint

PlanFootprint = Callable[[RepairPlan], RepairConflictFootprint]

def _default_plan_footprint(plan: RepairPlan) -> RepairConflictFootprint:
    nodes = set(plan.scope)
    nodes.update(operation.target for operation in plan.operations)
    ancestors: set[str] = set()
    parent_space: set[str] = set()
    constraints: set[str] = set()
    coupled: set[str] = set()
    for reference in plan.diagnostic_refs:
        prefix, separator, value = reference.partition(":")
        if not separator or not value:
            continue
        if prefix in {"ancestor", "descendant-cone"}:
            ancestors.add(value)
        elif prefix in {"parent-space", "space-pool"}:
            parent_space.add(value)
        elif prefix in {"constraint", "footprint"}:
            constraints.add(value)
        elif prefix in {"connector", "group", "table", "auto-layout"}:
            coupled.add(f"{prefix}:{value}")
    # Tree-like ids can express ancestry without a separate layout callback.
    for node in tuple(nodes):
        pieces = node.replace("\\", "/").split("/")
        ancestors.update("/".join(pieces[:index]) for index in range(1, len(pieces)))
    return RepairConflictFootprint(
        nodes=frozenset(nodes),
        ancestor_cones=frozenset(ancestors),
        parent_space_pools=frozenset(parent_space),
        constraints=frozenset(constraints),
        coupled_containers=frozenset(coupled),
    )


def _footprints_conflict(
    left: RepairConflictFootprint, right: RepairConflictFootprint
) -> bool:
    return bool(
        left.nodes.intersection(right.nodes)
        or left.nodes.intersection(right.ancestor_cones)
        or right.nodes.intersection(left.ancestor_cones)
        or left.ancestor_cones.intersection(right.ancestor_cones)
        or left.parent_space_pools.intersection(right.parent_space_pools)
        or left.constraints.intersection(right.constraints)
        or left.coupled_containers.intersection(right.coupled_containers)
    )


def _merge_nonconflicting_plans(
    members: Sequence[RepairPlan],
) -> RepairPlan | None:
    if len(members) < 2:
        return None
    first = members[0]
    identity = (
        first.slide_id,
        first.base_spec_revision,
        first.base_spec_hash,
        first.expected_content_digest,
        first.provenance.source,
    )
    if any(
        (
            member.slide_id,
            member.base_spec_revision,
            member.base_spec_hash,
            member.expected_content_digest,
            member.provenance.source,
        )
        != identity
        for member in members[1:]
    ):
        return None
    operations = tuple(
        operation for member in members for operation in member.operations
    )
    scope = tuple(
        dict.fromkeys(
            (
                *(target for member in members for target in member.scope),
                *(operation.target for operation in operations),
            )
        )
    )
    if len(operations) > 16 or len(scope) > 8:
        return None
    max_operations = min(member.budget.maxOperations for member in members)
    max_touched = min(member.budget.maxTouchedLayoutNodes for member in members)
    if len(operations) > max_operations or len(scope) > max_touched:
        return None
    refs = tuple(
        dict.fromkeys(
            reference
            for member in members
            for reference in member.diagnostic_refs
        )
    )
    payload = first.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["scope"] = list(scope)
    payload["diagnosticRefs"] = list(refs)
    payload["operations"] = [
        operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        for operation in operations
    ]
    payload["strategy"] = "deterministic_cascade"
    payload["limits"] = {
        "maxOperations": max_operations,
        "maxAffectedBlocks": max_touched,
        "maxLayoutDepth": min(member.budget.maxLayoutDepth for member in members),
    }
    payload.pop("planId", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["planId"] = "rp_composite_" + hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()[:20]
    try:
        return RepairPlan.model_validate(payload)
    except ValueError:
        return None


def _composite_candidates(
    plans: Sequence[RepairPlan],
    footprint_for: PlanFootprint,
) -> tuple[tuple[RepairPlan, ...], dict[str, tuple[RepairPlan, ...]]]:
    """Greedily color the conflict graph into deterministic independent sets."""

    groups: list[list[RepairPlan]] = []
    footprints: dict[str, RepairConflictFootprint] = {
        plan.plan_id: footprint_for(plan) for plan in plans
    }
    for plan in deterministic_candidate_order(plans):
        for group in groups:
            if all(
                not _footprints_conflict(
                    footprints[plan.plan_id], footprints[member.plan_id]
                )
                for member in group
            ):
                group.append(plan)
                break
        else:
            groups.append([plan])
    composites: list[RepairPlan] = []
    members_by_id: dict[str, tuple[RepairPlan, ...]] = {}
    for group in groups[:8]:
        composite = _merge_nonconflicting_plans(group)
        if composite is None:
            continue
        composites.append(composite)
        members_by_id[composite.plan_id] = tuple(group)
    return tuple(composites), members_by_id


