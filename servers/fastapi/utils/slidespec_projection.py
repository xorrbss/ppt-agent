"""Authoritative capability and dry-planning boundary for source projection.

Imported authored HTML is not a second layout engine.  A repair is eligible only
when every typed operation can be projected through a measured source binding and
the resulting CSS property has a browser round-trip proof.  This module owns that
decision for deterministic candidates, LLM context/preflight, and the renderer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from models.slidespec_v1 import (
    ImportedHtmlBinding,
    ImportedHtmlSource,
    LayoutNode,
    RenderRequest,
    RepairPlan,
)


PROJECTION_CAPABILITY_VERSION = "slidespec-projection-capability/1.0"

_FLOW_DISPLAYS = frozenset({"flex", "inline-flex", "grid", "inline-grid"})
_GRID_DISPLAYS = frozenset({"grid", "inline-grid"})
_POSITIONED = frozenset({"relative", "absolute", "fixed", "sticky"})
_ALL_OPERATIONS = (
    "setSpacing",
    "setPadding",
    "setAlignment",
    "setJustification",
    "setTrackSize",
    "setColumnRatio",
    "setGridPlacement",
    "setBounds",
    "translateWithinParent",
    "resizeWithinBounds",
    "setFontScale",
    "setLineHeightToken",
    "setTextFitPolicy",
    "setImageFit",
    "setCropFocalPoint",
    "rerouteConnector",
    "replaceLayoutSubtree",
)


@dataclass(frozen=True, slots=True)
class ProjectionCapability:
    operation: str
    target: str
    layout_id: str | None
    semantic_id: str | None
    qid: str | None
    supported: bool
    reason_code: str | None = None
    proof: tuple[str, ...] = ()

    def telemetry(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "operation": self.operation,
            "layout_id": self.layout_id,
            "semantic_id": self.semantic_id,
            "qid": self.qid,
            "reason_code": self.reason_code,
            "proof": list(self.proof),
        }


@dataclass(frozen=True, slots=True)
class ProjectionDryRun:
    capabilities: tuple[ProjectionCapability, ...]

    @property
    def supported(self) -> bool:
        return all(item.supported for item in self.capabilities)

    @property
    def rejections(self) -> tuple[ProjectionCapability, ...]:
        return tuple(item for item in self.capabilities if not item.supported)

    @property
    def reason_code(self) -> str | None:
        rejection = next(iter(self.rejections), None)
        return rejection.reason_code if rejection is not None else None


@dataclass(frozen=True, slots=True)
class _ProjectionIndex:
    nodes_by_layout: Mapping[str, LayoutNode]
    nodes_by_semantic: Mapping[str, LayoutNode]
    parents_by_layout: Mapping[str, LayoutNode | None]
    bindings_by_layout: Mapping[str, Any]
    bindings_by_semantic: Mapping[str, Any]
    content_parent: Mapping[str, str | None]
    duplicate_qids: frozenset[str]


def _index(request: RenderRequest) -> _ProjectionIndex:
    nodes_by_layout: dict[str, LayoutNode] = {}
    nodes_by_semantic: dict[str, LayoutNode] = {}
    parents_by_layout: dict[str, LayoutNode | None] = {}

    def visit(node: LayoutNode, parent: LayoutNode | None) -> None:
        nodes_by_layout[node.layoutId] = node
        parents_by_layout[node.layoutId] = parent
        if node.contentSemanticId is not None:
            nodes_by_semantic[node.contentSemanticId] = node
        for child in node.children:
            visit(child, node)

    visit(request.spec.layout, None)
    source = request.source
    bindings = source.bindings if source is not None else ()
    counts: dict[str, int] = {}
    for binding in bindings:
        counts[binding.qid] = counts.get(binding.qid, 0) + 1
    return _ProjectionIndex(
        nodes_by_layout=nodes_by_layout,
        nodes_by_semantic=nodes_by_semantic,
        parents_by_layout=parents_by_layout,
        bindings_by_layout={binding.layoutId: binding for binding in bindings},
        bindings_by_semantic={binding.semanticId: binding for binding in bindings},
        content_parent={item.semanticId: item.parentSemanticId for item in request.spec.content},
        duplicate_qids=frozenset(qid for qid, count in counts.items() if count > 1),
    )


def _operation_name(operation: object) -> str:
    if isinstance(operation, Mapping):
        return str(operation.get("op", ""))
    return str(getattr(operation, "op", ""))


def _operation_target(operation: object) -> str:
    if isinstance(operation, Mapping):
        return str(operation.get("target", ""))
    return str(getattr(operation, "target", ""))


def _operation_value(operation: object, name: str, default: Any = None) -> Any:
    if isinstance(operation, Mapping):
        return operation.get(name, default)
    return getattr(operation, name, default)


def _resolve(index: _ProjectionIndex, target: str) -> LayoutNode | None:
    return index.nodes_by_layout.get(target) or index.nodes_by_semantic.get(target)


def _parent_binding(index: _ProjectionIndex, node: LayoutNode) -> Any | None:
    if node.contentSemanticId is not None:
        parent_semantic = index.content_parent.get(node.contentSemanticId)
        if parent_semantic is not None:
            binding = index.bindings_by_semantic.get(parent_semantic)
            if binding is not None:
                return binding
    parent = index.parents_by_layout.get(node.layoutId)
    while parent is not None:
        binding = index.bindings_by_layout.get(parent.layoutId)
        if binding is not None:
            return binding
        parent = index.parents_by_layout.get(parent.layoutId)
    return None


def _axis_change(operation: object, node: LayoutNode) -> tuple[bool, bool]:
    if _operation_name(operation) == "translateWithinParent":
        value = _operation_value(operation, "value")
        x = value.get("x", 0) if isinstance(value, Mapping) else getattr(value, "x", 0)
        y = value.get("y", 0) if isinstance(value, Mapping) else getattr(value, "y", 0)
        return abs(float(x)) > 1e-12, abs(float(y)) > 1e-12
    bounds = _operation_value(operation, "bounds")
    if bounds is None or node.bounds is None:
        return False, False
    x = bounds.get("x") if isinstance(bounds, Mapping) else getattr(bounds, "x", None)
    y = bounds.get("y") if isinstance(bounds, Mapping) else getattr(bounds, "y", None)
    return x != node.bounds.x, y != node.bounds.y


def _size_change(operation: object, node: LayoutNode) -> tuple[bool, bool]:
    op = _operation_name(operation)
    if node.bounds is None:
        return False, False
    if op == "resizeWithinBounds":
        width = _operation_value(operation, "width", node.bounds.width)
        height = _operation_value(operation, "height", node.bounds.height)
    else:
        bounds = _operation_value(operation, "bounds")
        if bounds is None:
            return False, False
        width = (
            bounds.get("width")
            if isinstance(bounds, Mapping)
            else getattr(bounds, "width", None)
        )
        height = (
            bounds.get("height")
            if isinstance(bounds, Mapping)
            else getattr(bounds, "height", None)
        )
    return width != node.bounds.width, height != node.bounds.height


def projected_source_box_px(
    source: ImportedHtmlSource,
    node: LayoutNode,
    binding: ImportedHtmlBinding,
) -> tuple[float, float, float, float]:
    """Apply canonical deltas to the importer's unclipped Chrome rectangle."""

    if node.bounds is None:
        raise ValueError("projected source bounds require canonical bounds")
    return (
        binding.sourceBoundsPx.x
        + (node.bounds.x - binding.bounds.x) * source.viewportWidth,
        binding.sourceBoundsPx.y
        + (node.bounds.y - binding.bounds.y) * source.viewportHeight,
        binding.sourceBoundsPx.width
        + (node.bounds.width - binding.bounds.width) * source.viewportWidth,
        binding.sourceBoundsPx.height
        + (node.bounds.height - binding.bounds.height) * source.viewportHeight,
    )


def project_positioned_bounds_styles(
    binding: ImportedHtmlBinding,
    *,
    delta_x_px: float = 0.0,
    delta_y_px: float = 0.0,
    delta_width_px: float = 0.0,
    delta_height_px: float = 0.0,
) -> dict[str, str]:
    """Project a relative bounds delta without dual/auto-anchor distortion."""

    styles: dict[str, str] = {}
    horizontal_changed = (
        abs(delta_x_px) > 1e-12 or abs(delta_width_px) > 1e-12
    )
    vertical_changed = (
        abs(delta_y_px) > 1e-12 or abs(delta_height_px) > 1e-12
    )
    if horizontal_changed:
        if binding.leftPx is not None:
            styles["left"] = f"{binding.leftPx + delta_x_px:.4f}px"
        if binding.rightPx is not None:
            styles["right"] = (
                f"{binding.rightPx - delta_x_px - delta_width_px:.4f}px"
            )
    if vertical_changed:
        if binding.topPx is not None:
            styles["top"] = f"{binding.topPx + delta_y_px:.4f}px"
        if binding.bottomPx is not None:
            styles["bottom"] = (
                f"{binding.bottomPx - delta_y_px - delta_height_px:.4f}px"
            )
    return styles


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


def dry_project_operations(
    request: RenderRequest,
    operations: Iterable[object],
    *,
    enforce_mutation_envelope: bool = True,
) -> ProjectionDryRun:
    """Resolve exact target/binding support without rendering or mutating source."""

    index = _index(request)
    return ProjectionDryRun(
        tuple(
            _capability(
                request,
                index,
                operation,
                enforce_mutation_envelope=enforce_mutation_envelope,
            )
            for operation in operations
        )
    )


def dry_project_plan(request: RenderRequest, plan: RepairPlan) -> ProjectionDryRun:
    return dry_project_operations(request, plan.operations)


def projection_schema_context(
    request: RenderRequest,
    *,
    target_semantic_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return the closed, text-free operation vocabulary supplied to the LLM."""

    index = _index(request)
    target_filter = set(target_semantic_ids or ())
    targets: list[dict[str, Any]] = []
    supported_union: set[str] = set()
    for node in sorted(index.nodes_by_layout.values(), key=lambda item: item.layoutId):
        if node.contentSemanticId is None:
            continue
        if target_filter and node.contentSemanticId not in target_filter:
            continue
        supported: list[str] = []
        for op in _ALL_OPERATIONS:
            probe: dict[str, Any] = {"op": op, "target": node.layoutId}
            if op == "setBounds" and node.bounds is not None:
                # Capability context is conservative: prove both axes because the
                # model has not supplied its exact changed-axis payload yet.
                probe["bounds"] = {
                    **node.bounds.model_dump(mode="json"),
                    "x": min(1 - node.bounds.width, node.bounds.x + 1e-6),
                    "y": min(1 - node.bounds.height, node.bounds.y + 1e-6),
                }
            elif op == "translateWithinParent":
                probe["value"] = {"x": 1e-6, "y": 1e-6}
            elif op == "setLineHeightToken":
                probe["value"] = request.spec.tokens[0].name if request.spec.tokens else ""
            elif op == "setTextFitPolicy":
                probe["value"] = "wrap"
            capability = _capability(request, index, probe)
            if capability.supported:
                supported.append(op)
                supported_union.add(op)
        binding = index.bindings_by_layout.get(node.layoutId)
        targets.append(
            {
                "semanticId": node.contentSemanticId,
                "layoutId": node.layoutId,
                "qid": str(binding.qid) if binding is not None else None,
                "supportedOperations": supported,
            }
        )
    return {
        "version": PROJECTION_CAPABILITY_VERSION,
        "sourceKind": "imported-html" if request.source is not None else "native",
        "supportedOperations": sorted(supported_union),
        "targets": targets,
    }


def _changed_operations(request: RenderRequest) -> tuple[dict[str, Any], ...]:
    """Recover imported-source projection operations from canonical/binding deltas."""

    source = request.source
    if source is None:
        return ()
    index = _index(request)
    operations: list[dict[str, Any]] = []
    for binding in source.bindings:
        node = index.nodes_by_layout.get(binding.layoutId)
        if node is None or node.bounds is None:
            operations.append({"op": "setBounds", "target": binding.layoutId})
            continue
        bounds_changed = node.bounds != binding.bounds
        if bounds_changed:
            operations.append(
                {
                    "op": "setBounds",
                    "target": node.layoutId,
                    "bounds": node.bounds.model_dump(mode="json"),
                }
            )
        if node.fontScale != 1:
            operations.append({"op": "setFontScale", "target": node.layoutId})
        if abs(node.spacing - binding.gapPx) > 1e-6:
            operations.append({"op": "setSpacing", "target": node.layoutId})
        _, _, projected_width, projected_height = projected_source_box_px(
            source, node, binding
        )
        desired_padding = (
            node.padding.top * projected_height,
            node.padding.right * projected_width,
            node.padding.bottom * projected_height,
            node.padding.left * projected_width,
        )
        baseline_padding = (
            binding.paddingTopPx,
            binding.paddingRightPx,
            binding.paddingBottomPx,
            binding.paddingLeftPx,
        )
        if any(
            abs(desired - baseline) > 0.01
            for desired, baseline in zip(desired_padding, baseline_padding, strict=True)
        ):
            operations.append({"op": "setPadding", "target": node.layoutId})
        alignment = {
            "normal": "stretch",
            "flex-start": "start",
            "start": "start",
            "flex-end": "end",
            "end": "end",
            "center": "center",
            "stretch": "stretch",
        }.get(binding.alignItems.strip().lower(), "stretch")
        justification = {
            "normal": "start",
            "flex-start": "start",
            "start": "start",
            "flex-end": "end",
            "end": "end",
            "center": "center",
            "space-between": "space-between",
        }.get(binding.justifyContent.strip().lower(), "start")
        if node.alignment != alignment:
            operations.append({"op": "setAlignment", "target": node.layoutId})
        if node.justification != justification:
            operations.append({"op": "setJustification", "target": node.layoutId})
        if node.columnRatio != binding.columnRatio:
            operations.append({"op": "setColumnRatio", "target": node.layoutId})
        elif node.tracks:
            operations.append({"op": "setTrackSize", "target": node.layoutId})
        if node.gridPlacement != binding.gridPlacement:
            operations.append({"op": "setGridPlacement", "target": node.layoutId})
        if node.lineHeightToken is not None:
            operations.append(
                {
                    "op": "setLineHeightToken",
                    "target": node.layoutId,
                    "value": node.lineHeightToken,
                }
            )
        if node.textFitPolicy != binding.textFitPolicy:
            operations.append(
                {
                    "op": "setTextFitPolicy",
                    "target": node.layoutId,
                    "value": node.textFitPolicy,
                }
            )
        if node.imageFit != binding.objectFit:
            operations.append({"op": "setImageFit", "target": node.layoutId})
        if node.cropFocalPoint != binding.cropFocalPoint:
            operations.append({"op": "setCropFocalPoint", "target": node.layoutId})
        if node.connectorRoute is not None:
            operations.append({"op": "rerouteConnector", "target": node.layoutId})
    return tuple(operations)


def dry_project_request(request: RenderRequest) -> ProjectionDryRun:
    """Dry-run the exact deltas the imported renderer is about to project."""

    # A rendered request no longer retains which typed operation produced an
    # equivalent bounds delta (for example resizeWithinBounds vs setBounds).
    # Mutation envelopes were enforced on the RepairPlan; here we only prove the
    # source binding/CSS contract without guessing the lost operation identity.
    return dry_project_operations(
        request,
        _changed_operations(request),
        enforce_mutation_envelope=False,
    )


__all__ = [
    "PROJECTION_CAPABILITY_VERSION",
    "ProjectionCapability",
    "ProjectionDryRun",
    "dry_project_operations",
    "dry_project_plan",
    "dry_project_request",
    "project_positioned_bounds_styles",
    "projected_source_box_px",
    "projection_schema_context",
]
