"""Layout indexing and target selectors for reusable repair patterns."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from models.slidespec_v1 import LayoutNode, RenderRequest
from services.slidespec_repair_patterns import (
    _LayoutIndexes,
    _TargetSelector,
    _digest,
)

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
