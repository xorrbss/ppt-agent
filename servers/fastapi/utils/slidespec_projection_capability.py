"""Per-operation capability decisions for measured source projection."""

from __future__ import annotations

from typing import Any

from models.slidespec_v1 import LayoutNode, RenderRequest
from utils.slidespec_projection import (
    _ALL_OPERATIONS,
    _FLOW_DISPLAYS,
    _GRID_DISPLAYS,
    _POSITIONED,
    ProjectionCapability,
    _ProjectionIndex,
    _axis_change,
    _operation_name,
    _operation_target,
    _operation_value,
    _parent_binding,
    _resolve,
    _size_change,
)

def _reject(
    operation: str,
    target: str,
    node: LayoutNode | None,
    binding: Any | None,
    reason_code: str,
) -> ProjectionCapability:
    return ProjectionCapability(
        operation,
        target,
        node.layoutId if node is not None else None,
        node.contentSemanticId if node is not None else None,
        str(binding.qid) if binding is not None else None,
        False,
        reason_code,
    )


def _capability(
    request: RenderRequest,
    index: _ProjectionIndex,
    operation: object,
    *,
    enforce_mutation_envelope: bool = True,
) -> ProjectionCapability:
    op = _operation_name(operation)
    target = _operation_target(operation)
    node = _resolve(index, target)
    if node is None:
        return _reject(op, target, None, None, "repair.target.missing")
    if op not in _ALL_OPERATIONS:
        return _reject(op, target, node, None, "repair.operation.unsupported")
    source_binding = index.bindings_by_layout.get(node.layoutId)
    # These operations have no imported-source contract at all.  Preserve the
    # precise projection reason even when a mutation envelope also excludes them.
    if request.source is not None and op == "rerouteConnector":
        return _reject(
            op,
            target,
            node,
            source_binding,
            "render.source_projection.connector_unsupported",
        )
    if request.source is not None and op == "replaceLayoutSubtree":
        return _reject(
            op,
            target,
            node,
            source_binding,
            "render.source_projection.subtree_unsupported",
        )
    envelope = node.constraints
    if enforce_mutation_envelope and envelope is not None:
        if envelope.locked:
            return _reject(op, target, node, None, "repair.mutation_envelope.locked")
        if envelope.allowedOperations is not None and op not in envelope.allowedOperations:
            return _reject(
                op, target, node, None, "repair.mutation_envelope.operation_not_allowed"
            )
    if request.source is None:
        return ProjectionCapability(
            op,
            target,
            node.layoutId,
            node.contentSemanticId,
            None,
            True,
            proof=("native-canonical-render",),
        )
    binding = index.bindings_by_layout.get(node.layoutId)
    if binding is None or (
        node.contentSemanticId is not None
        and binding.semanticId != node.contentSemanticId
    ):
        return _reject(op, target, node, binding, "render.source_binding.invalid")
    if binding.qid in index.duplicate_qids:
        return _reject(
            op, target, node, binding, "render.source_binding.qid_duplicate"
        )

    proof: tuple[str, ...]
    if op == "setSpacing":
        if binding.display not in _FLOW_DISPLAYS:
            return _reject(op, target, node, binding, "render.source_projection.gap_unsupported")
        proof = ("computed-style:gap",)
    elif op == "setPadding":
        if node.bounds is None:
            return _reject(op, target, node, binding, "repair.bounds.required")
        proof = (
            "computed-style:padding-top",
            "computed-style:padding-right",
            "computed-style:padding-bottom",
            "computed-style:padding-left",
        )
    elif op in {"setAlignment", "setJustification"}:
        if binding.display not in _FLOW_DISPLAYS:
            suffix = "alignment" if op == "setAlignment" else "justification"
            return _reject(
                op,
                target,
                node,
                binding,
                f"render.source_projection.{suffix}_unsupported",
            )
        proof = (
            "computed-style:align-items"
            if op == "setAlignment"
            else "computed-style:justify-content",
        )
    elif op in {"setTrackSize", "setColumnRatio"}:
        if binding.display not in _GRID_DISPLAYS:
            return _reject(op, target, node, binding, "render.source_projection.grid_unsupported")
        proof = ("computed-style:grid-template-columns",)
    elif op == "setGridPlacement":
        parent = _parent_binding(index, node)
        if parent is None or parent.display not in _GRID_DISPLAYS:
            return _reject(
                op,
                target,
                node,
                binding,
                "render.source_projection.grid_placement_unsupported",
            )
        proof = ("computed-style:grid-column", "computed-style:grid-row")
    elif op in {"setBounds", "translateWithinParent", "resizeWithinBounds"}:
        if node.bounds is None:
            return _reject(op, target, node, binding, "repair.bounds.required")
        x_changed, y_changed = _axis_change(operation, node)
        width_changed, height_changed = _size_change(operation, node)
        if x_changed or y_changed:
            if binding.position == "static":
                if not (
                    binding.transform == "none" and binding.staticTranslationSafe
                ):
                    return _reject(
                        op,
                        target,
                        node,
                        binding,
                        "render.source_projection.position_unsupported",
                    )
                proof = ("computed-style:transform", "browser-bounds")
            elif binding.position not in _POSITIONED:
                return _reject(
                    op,
                    target,
                    node,
                    binding,
                    "render.source_projection.position_unsupported",
                )
        if binding.position in _POSITIONED:
            if (
                (x_changed or width_changed)
                and binding.leftPx is None
                and binding.rightPx is None
            ):
                return _reject(
                    op,
                    target,
                    node,
                    binding,
                    "render.source_projection.horizontal_anchor_missing",
                )
            if (
                (y_changed or height_changed)
                and binding.topPx is None
                and binding.bottomPx is None
            ):
                return _reject(
                    op,
                    target,
                    node,
                    binding,
                    "render.source_projection.vertical_anchor_missing",
                )
            proof = (
                "computed-style:position-anchor",
                "browser-bounds:size"
                if width_changed or height_changed
                else "browser-bounds",
            )
        elif not (x_changed or y_changed):
            proof = ("browser-bounds:size",)
        elif width_changed or height_changed:
            proof = tuple(dict.fromkeys((*proof, "browser-bounds:size")))
    elif op == "setFontScale":
        if binding.fontSizePx is None:
            return _reject(
                op, target, node, binding, "render.source_projection.font_size_missing"
            )
        proof = ("computed-style:font-size",)
    elif op == "setLineHeightToken":
        token_name = _operation_value(operation, "value")
        token = next((item for item in request.spec.tokens if item.name == token_name), None)
        if token is None:
            return _reject(
                op,
                target,
                node,
                binding,
                "render.source_projection.line_height_token_missing",
            )
        proof = ("computed-style:line-height",)
    elif op == "setTextFitPolicy":
        value = _operation_value(operation, "value")
        if value not in {"none", "wrap", "truncate"}:
            return _reject(
                op,
                target,
                node,
                binding,
                "render.source_projection.text_fit_unsupported",
            )
        proof = (
            "computed-style:white-space",
            "computed-style:overflow",
            "computed-style:overflow-wrap",
            "computed-style:text-overflow",
        )
    elif op in {"setImageFit", "setCropFocalPoint"}:
        if binding.tag != "img":
            return _reject(
                op, target, node, binding, "render.source_projection.image_unsupported"
            )
        proof = ("computed-style:object-fit", "computed-style:object-position")
    elif op == "rerouteConnector":
        return _reject(
            op, target, node, binding, "render.source_projection.connector_unsupported"
        )
    else:  # replaceLayoutSubtree has no authored DOM mutation contract.
        return _reject(
            op, target, node, binding, "render.source_projection.subtree_unsupported"
        )
    return ProjectionCapability(
        op,
        target,
        node.layoutId,
        node.contentSemanticId,
        str(binding.qid),
        True,
        proof=proof,
    )

