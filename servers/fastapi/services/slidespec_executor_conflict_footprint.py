"""Derive conservative conflict footprints for SlideSpec repair plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from models.slidespec_v1 import LayoutNode, RenderRequest, RepairPlan
from services.slidespec_repair_pipeline import RepairConflictFootprint

def _repair_conflict_footprint(
    request: RenderRequest, plan: RepairPlan
) -> RepairConflictFootprint:
    """Resolve publication dependencies from the canonical layout/constraints."""

    by_id: dict[str, LayoutNode] = {}
    by_semantic: dict[str, LayoutNode] = {}
    parent_by_id: dict[str, LayoutNode | None] = {}

    def visit(node: LayoutNode, parent: LayoutNode | None) -> None:
        by_id[node.layoutId] = node
        parent_by_id[node.layoutId] = parent
        if node.contentSemanticId is not None:
            by_semantic[node.contentSemanticId] = node
        for child in node.children:
            visit(child, node)

    visit(request.spec.layout, None)
    target_refs = set(plan.scope)
    target_refs.update(operation.target for operation in plan.operations)
    target_nodes: set[str] = set()
    for target in target_refs:
        node = by_id.get(target) or by_semantic.get(target)
        target_nodes.add(node.layoutId if node is not None else target)

    ancestor_cones: set[str] = set()
    parent_space_pools: set[str] = set()
    coupled_containers: set[str] = set()
    coupled_kinds = {"connector", "group", "table", "grid", "stack", "frame"}
    content_kinds = {
        content.semanticId: content.kind for content in request.spec.content
    }
    for target in tuple(target_nodes):
        node = by_id.get(target)
        if node is None:
            continue
        parent = parent_by_id.get(node.layoutId)
        if parent is not None:
            parent_space_pools.add(parent.layoutId)
        current: LayoutNode | None = node
        while current is not None:
            if (
                current.kind in coupled_kinds
                or content_kinds.get(current.contentSemanticId or "")
                in coupled_kinds
            ):
                coupled_containers.add(current.layoutId)
            current = parent_by_id.get(current.layoutId)
            if current is not None:
                ancestor_cones.add(current.layoutId)

    def string_refs(value: object) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, Mapping):
            return {
                item
                for nested in value.values()
                for item in string_refs(nested)
            }
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return {item for nested in value for item in string_refs(nested)}
        return set()

    constraints: set[str] = set()
    dependency_refs = target_refs | target_nodes
    for constraint in request.spec.constraints:
        raw = constraint.model_dump(mode="json", by_alias=True, exclude_none=True)
        if dependency_refs.intersection(string_refs(raw)):
            constraints.add(
                str(raw.get("constraintId") or raw.get("id") or constraint.kind)
            )
    return RepairConflictFootprint(
        nodes=frozenset(target_nodes),
        ancestor_cones=frozenset(ancestor_cones),
        parent_space_pools=frozenset(parent_space_pools),
        constraints=frozenset(constraints),
        coupled_containers=frozenset(coupled_containers),
    )
