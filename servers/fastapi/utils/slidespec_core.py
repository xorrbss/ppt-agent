"""Pure SlideSpec 1.x validation, repair, rendering, and adaptation primitives."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html import escape
import json
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from models.slidespec_v1 import (
    AppliedRepair,
    Bounds,
    ContentNode,
    LayoutNode,
    Provenance,
    RenderRequest,
    RenderResult,
    RepairPlan,
    SemanticDomEntry,
    SlideDigests,
    SlideSpec,
    SLIDESPEC_SCHEMA,
    SLIDESPEC_SCHEMA_V1_0,
)
from utils.slidespec_constraints import (
    constraint_structure_error,
    evaluate_repair_transition,
)
from utils.authored_patch_engine import apply_authored_patch

ZERO_DIGEST = "sha256:" + "0" * 64
ADAPTER_VERSION = "adaptive-minimal/1"
class SlideSpecError(ValueError):
    """A stable, machine-readable SlideSpec contract failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)
def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
def canonical_json(value: Any) -> str:
    """Return the RFC-8259 subset used by every SlideSpec digest."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
def digest(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()
def _content_projection(spec: SlideSpec) -> list[dict[str, Any]]:
    projection = []
    for node in spec.content:
        projection.append(
            node.model_dump(
                mode="json",
                exclude={
                    "parentSemanticId",
                    "readingOrder",
                    "importantTokens",
                    "relationships",
                },
            )
        )
    return projection
def _hierarchy_projection(spec: SlideSpec) -> list[dict[str, Any]]:
    return [
        {
            "semanticId": node.semanticId,
            "parentSemanticId": node.parentSemanticId,
            "readingOrder": node.readingOrder,
            "relationships": [rel.model_dump(mode="json") for rel in node.relationships],
        }
        for node in spec.content
    ]
def _important_token_projection(spec: SlideSpec) -> dict[str, Any]:
    tokens = sorted(
        (token.model_dump(mode="json") for token in spec.tokens if token.important),
        key=lambda item: item["name"],
    )
    references = sorted(
        (node.semanticId, name)
        for node in spec.content
        for name in node.importantTokens
    )
    return {"definitions": tokens, "references": references}
def compute_digests(spec: SlideSpec) -> SlideDigests:
    content_hash = digest(_content_projection(spec))
    hierarchy_hash = digest(_hierarchy_projection(spec))
    token_hash = digest(_important_token_projection(spec))
    dumped = spec.model_dump(mode="json", by_alias=True)
    if spec.schemaUri == SLIDESPEC_SCHEMA_V1_0:
        _strip_v1_1_layout_metadata(dumped["layout"])
    else:
        _strip_default_size_constraint_extensions(dumped["layout"])
    # Empty relational metadata was absent from already-persisted SlideSpec 1.1
    # bundles.  Keep those canonical hashes readable while non-empty constraints
    # remain part of the exact spec digest.
    if not spec.constraints:
        dumped.pop("constraints", None)
        dumped.pop("constraintPolicy", None)
    dumped["digests"] = {
        "content": content_hash,
        "hierarchy": hierarchy_hash,
        "importantTokens": token_hash,
    }
    return SlideDigests(
        content=content_hash,
        hierarchy=hierarchy_hash,
        importantTokens=token_hash,
        spec=digest(dumped),
    )

def _strip_v1_1_layout_metadata(node: dict[str, Any]) -> None:
    """Preserve canonical digests for already-persisted SlideSpec 1.0 data."""

    node.pop("intent", None)
    node.pop("constraints", None)
    for child in node.get("children", []):
        _strip_v1_1_layout_metadata(child)

def _strip_default_size_constraint_extensions(node: dict[str, Any]) -> None:
    """Keep hashes from before absolute size envelopes were introduced readable."""

    constraints = node.get("constraints")
    if isinstance(constraints, dict):
        defaults = {
            "minWidth": 1e-6,
            "minHeight": 1e-6,
            "maxWidth": 1,
            "maxHeight": 1,
        }
        for field, default in defaults.items():
            if constraints.get(field) == default:
                constraints.pop(field, None)
    for child in node.get("children", []):
        _strip_default_size_constraint_extensions(child)

def seal_slidespec(spec: SlideSpec) -> SlideSpec:
    """Return a copy carrying digests for its exact current value."""

    return spec.model_copy(update={"digests": compute_digests(spec)})

def _walk_layout(root: LayoutNode) -> Iterable[tuple[LayoutNode, int]]:
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        yield node, depth
        stack.extend((child, depth + 1) for child in reversed(node.children))
def validate_slidespec(
    spec: SlideSpec, *, require_digests: bool = True, max_layout_depth: int = 32
) -> None:
    semantic_ids = [node.semanticId for node in spec.content]
    if len(semantic_ids) != len(set(semantic_ids)):
        raise SlideSpecError("spec.semantic_id.duplicate")
    semantic_set = set(semantic_ids)
    orders = [node.readingOrder for node in spec.content]
    if len(orders) != len(set(orders)):
        raise SlideSpecError("spec.reading_order.duplicate")

    for node in spec.content:
        if node.parentSemanticId is not None and node.parentSemanticId not in semantic_set:
            raise SlideSpecError("spec.parent.missing", node.semanticId)
        if node.parentSemanticId == node.semanticId:
            raise SlideSpecError("spec.parent.cycle", node.semanticId)
        if any(rel.targetSemanticId not in semantic_set for rel in node.relationships):
            raise SlideSpecError("spec.relationship.missing", node.semanticId)

    parents = {node.semanticId: node.parentSemanticId for node in spec.content}
    for start in semantic_ids:
        seen: set[str] = set()
        cursor: str | None = start
        while cursor is not None:
            if cursor in seen:
                raise SlideSpecError("spec.parent.cycle", start)
            seen.add(cursor)
            cursor = parents.get(cursor)

    token_names = [token.name for token in spec.tokens]
    if len(token_names) != len(set(token_names)):
        raise SlideSpecError("spec.token.duplicate")
    important_names = {token.name for token in spec.tokens if token.important}
    for node in spec.content:
        if not set(node.importantTokens).issubset(important_names):
            raise SlideSpecError("spec.token.reference_missing", node.semanticId)

    layout_nodes = list(_walk_layout(spec.layout))
    layout_ids = [node.layoutId for node, _ in layout_nodes]
    if len(layout_ids) != len(set(layout_ids)):
        raise SlideSpecError("spec.layout_id.duplicate")
    if max(depth for _, depth in layout_nodes) > max_layout_depth:
        raise SlideSpecError("spec.layout.depth_exceeded")
    bindings = [
        node.contentSemanticId
        for node, _ in layout_nodes
        if node.contentSemanticId is not None
    ]
    if Counter(bindings) != Counter({semantic_id: 1 for semantic_id in semantic_ids}):
        raise SlideSpecError("spec.layout.binding_not_exact")
    for node, _ in layout_nodes:
        if node.lineHeightToken is not None and node.lineHeightToken not in token_names:
            raise SlideSpecError("spec.layout.token_missing", node.layoutId)

    constraint_error = constraint_structure_error(spec)
    if constraint_error is not None:
        raise SlideSpecError(*constraint_error)

    if require_digests and spec.digests != compute_digests(spec):
        raise SlideSpecError("spec.digest.mismatch")
def _find_layout(
    root: dict[str, Any], layout_id: str
) -> tuple[dict[str, Any] | None, int | None, dict[str, Any]] | None:
    if root["layoutId"] == layout_id:
        return None, None, root
    stack = [root]
    while stack:
        parent = stack.pop()
        for index, child in enumerate(parent.get("children", [])):
            if child["layoutId"] == layout_id:
                return parent, index, child
            stack.append(child)
    return None
def _resolve_layout_id(root: dict[str, Any], target: str) -> str:
    if target.startswith("lay:"):
        return target
    matches: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.get("contentSemanticId") == target:
            matches.append(node["layoutId"])
        stack.extend(node.get("children", []))
    if len(matches) != 1:
        raise SlideSpecError("repair.target.unresolved", target)
    return matches[0]

def _binding_counter(root: dict[str, Any]) -> Counter[str]:
    result: Counter[str] = Counter()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.get("contentSemanticId") is not None:
            result[node["contentSemanticId"]] += 1
        stack.extend(node.get("children", []))
    return result

def _set_value(node: dict[str, Any], field: str, value: Any) -> None:
    if node.get(field) == value:
        raise SlideSpecError("repair.operation.noop", f"{node['layoutId']}:{field}")
    node[field] = value

def _enforce_operation_constraints(node: dict[str, Any], operation: Any) -> None:
    raw = node.get("constraints") or {
        "locked": False,
        "allowedOperations": None,
        "lockPosition": False,
        "lockSize": False,
        "preserveAspectRatio": False,
        "minWidth": 1e-6,
        "minHeight": 1e-6,
        "maxWidth": 1,
        "maxHeight": 1,
        "minFontScale": 0.85,
        "maxFontShrink": 0.15,
        "maxTranslation": 0.08,
        "maxResizeDelta": 0.25,
    }
    op = operation.op
    if raw.get("locked"):
        raise SlideSpecError("repair.constraint.locked", node["layoutId"])
    allowed = raw.get("allowedOperations")
    if allowed is not None and op not in allowed:
        raise SlideSpecError(
            "repair.constraint.operation_not_allowed", f"{node['layoutId']}:{op}"
        )

    bounds = node.get("bounds")
    proposed: dict[str, Any] | None = None
    if op == "setBounds":
        proposed = operation.bounds.model_dump(mode="json")
    elif op == "translateWithinParent" and bounds is not None:
        proposed = dict(
            bounds,
            x=bounds["x"] + operation.value.x,
            y=bounds["y"] + operation.value.y,
        )
    elif op == "resizeWithinBounds" and bounds is not None:
        proposed = dict(bounds, width=operation.width, height=operation.height)

    if proposed is not None and bounds is not None:
        position_delta = max(
            abs(proposed["x"] - bounds["x"]),
            abs(proposed["y"] - bounds["y"]),
        )
        size_delta = max(
            abs(proposed["width"] - bounds["width"]),
            abs(proposed["height"] - bounds["height"]),
        )
        if raw.get("lockPosition") and position_delta > 1e-9:
            raise SlideSpecError("repair.constraint.position_locked", node["layoutId"])
        if raw.get("lockSize") and size_delta > 1e-9:
            raise SlideSpecError("repair.constraint.size_locked", node["layoutId"])
        if position_delta > raw.get("maxTranslation", 0.08) + 1e-9:
            raise SlideSpecError(
                "repair.constraint.translation_exceeded", node["layoutId"]
            )
        if (
            proposed["width"] < raw.get("minWidth", 1e-6) - 1e-9
            or proposed["height"] < raw.get("minHeight", 1e-6) - 1e-9
        ):
            raise SlideSpecError(
                "repair.constraint.size_below_minimum", node["layoutId"]
            )
        if (
            proposed["width"] > raw.get("maxWidth", 1) + 1e-9
            or proposed["height"] > raw.get("maxHeight", 1) + 1e-9
        ):
            raise SlideSpecError(
                "repair.constraint.size_above_maximum", node["layoutId"]
            )
        if size_delta > raw.get("maxResizeDelta", 0.25) + 1e-9:
            raise SlideSpecError("repair.constraint.resize_exceeded", node["layoutId"])
        if raw.get("preserveAspectRatio"):
            before_ratio = bounds["width"] / bounds["height"]
            after_ratio = proposed["width"] / proposed["height"]
            if abs(before_ratio - after_ratio) > 1e-6:
                raise SlideSpecError(
                    "repair.constraint.aspect_ratio_changed", node["layoutId"]
                )

    if op == "setFontScale":
        scale = operation.scale
        current = float(node.get("fontScale", 1.0))
        if scale < raw.get("minFontScale", 0.85) - 1e-9:
            raise SlideSpecError(
                "repair.constraint.font_below_minimum", node["layoutId"]
            )
        if current - scale > raw.get("maxFontShrink", 0.15) + 1e-9:
            raise SlideSpecError(
                "repair.constraint.font_shrink_exceeded", node["layoutId"]
            )

def _binding_metadata(node: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    result: dict[str, tuple[Any, Any]] = {}
    stack = [node]
    while stack:
        current = stack.pop()
        semantic_id = current.get("contentSemanticId")
        if semantic_id is not None:
            result[semantic_id] = (
                current.get("intent"),
                current.get("constraints"),
            )
        stack.extend(current.get("children", []))
    return result

def _apply_operation(
    layout: dict[str, Any], operation: Any
) -> dict[str, Any]:
    layout_id = _resolve_layout_id(layout, operation.target)
    found = _find_layout(layout, layout_id)
    if found is None:
        raise SlideSpecError("repair.target.missing", layout_id)
    parent, index, node = found
    _enforce_operation_constraints(node, operation)
    op = operation.op
    value = getattr(operation, "value", None)
    if op == "setSpacing":
        value = operation.gap
    elif op == "setPadding":
        value = operation.padding
    elif op == "setBounds":
        value = operation.bounds
    elif op == "setFontScale":
        value = operation.scale
    plain_value = _json_value(value)
    fields = {
        "setSpacing": "spacing",
        "setPadding": "padding",
        "setAlignment": "alignment",
        "setJustification": "justification",
        "setTrackSize": "tracks",
        "setColumnRatio": "columnRatio",
        "setGridPlacement": "gridPlacement",
        "setBounds": "bounds",
        "setFontScale": "fontScale",
        "setLineHeightToken": "lineHeightToken",
        "setTextFitPolicy": "textFitPolicy",
        "setImageFit": "imageFit",
        "setCropFocalPoint": "cropFocalPoint",
        "rerouteConnector": "connectorRoute",
    }
    condition = operation.precondition
    if condition is not None:
        if condition.expectedLayoutHash is not None and condition.expectedLayoutHash != digest(node):
            raise SlideSpecError("repair.precondition.layout_hash_mismatch", layout_id)
        if condition.expectedBounds is not None and node.get("bounds") != _json_value(condition.expectedBounds):
            raise SlideSpecError("repair.precondition.bounds_mismatch", layout_id)
        if condition.expectedValue is not None and op in fields and node.get(fields[op]) != condition.expectedValue:
            raise SlideSpecError("repair.precondition.value_mismatch", layout_id)
    if op in fields:
        _set_value(node, fields[op], plain_value)
    elif op == "translateWithinParent":
        bounds = node.get("bounds")
        if bounds is None:
            raise SlideSpecError("repair.bounds.required", node["layoutId"])
        changed = dict(bounds, x=bounds["x"] + value.x, y=bounds["y"] + value.y)
        _set_value(node, "bounds", Bounds.model_validate(changed).model_dump(mode="json"))
    elif op == "resizeWithinBounds":
        bounds = node.get("bounds")
        if bounds is None:
            raise SlideSpecError("repair.bounds.required", node["layoutId"])
        changed = dict(bounds, width=operation.width, height=operation.height)
        _set_value(node, "bounds", Bounds.model_validate(changed).model_dump(mode="json"))
    elif op == "replaceLayoutSubtree":
        replacement = operation.value.root.model_dump(mode="json")
        if replacement["layoutId"] != node["layoutId"]:
            raise SlideSpecError("repair.replace.root_id_changed", node["layoutId"])
        if _binding_counter(replacement) != _binding_counter(node):
            raise SlideSpecError("repair.replace.bindings_changed", node["layoutId"])
        if _binding_metadata(replacement) != _binding_metadata(node):
            raise SlideSpecError(
                "repair.replace.layout_metadata_changed", node["layoutId"]
            )
        if replacement == node:
            raise SlideSpecError("repair.operation.noop", node["layoutId"])
        if parent is None:
            layout = replacement
        else:
            assert index is not None
            parent["children"][index] = replacement
    else:  # pragma: no cover - the discriminated union makes this unreachable
        raise SlideSpecError("repair.operation.unsupported", op)
    return layout

def apply_repair_plan(base: SlideSpec, plan: RepairPlan) -> AppliedRepair:
    """Atomically apply a bounded plan; the input object is never mutated."""

    validate_slidespec(base)
    if plan.slide_id != base.slideId:
        raise SlideSpecError("repair.slide_id.mismatch")
    if plan.base_spec_revision != base.specRevision:
        raise SlideSpecError("repair.base_revision.stale")
    if plan.base_spec_hash != base.digests.spec:
        raise SlideSpecError("repair.base_hash.stale")
    if plan.expected_content_digest != base.digests.content:
        raise SlideSpecError("repair.content_digest.stale")
    if len(plan.operations) > plan.budget.maxOperations:
        raise SlideSpecError("repair.budget.operations_exceeded")
    raw_targets = tuple(dict.fromkeys(op.target for op in plan.operations))
    if plan.scope and not set(raw_targets).issubset(plan.scope):
        raise SlideSpecError("repair.scope.target_outside")
    if plan.strategy != "layout_reflow" and any(
        op.op == "replaceLayoutSubtree" for op in plan.operations
    ):
        raise SlideSpecError("repair.strategy.operation_not_allowed")
    dumped_layout = base.layout.model_dump(mode="json")
    touched = tuple(_resolve_layout_id(dumped_layout, target) for target in raw_targets)
    if len(touched) > plan.budget.maxTouchedLayoutNodes:
        raise SlideSpecError("repair.budget.scope_exceeded")

    candidate = base.model_dump(mode="json", by_alias=True)
    layout = candidate["layout"]
    for operation in plan.operations:
        layout = _apply_operation(layout, operation)
    candidate["layout"] = layout
    candidate["specRevision"] = base.specRevision + 1
    candidate["provenance"] = Provenance(
        source="repair", parentSpecHash=base.digests.spec
    ).model_dump(mode="json")
    candidate["digests"] = SlideDigests(
        content=ZERO_DIGEST,
        hierarchy=ZERO_DIGEST,
        importantTokens=ZERO_DIGEST,
        spec=ZERO_DIGEST,
    ).model_dump(mode="json")
    repaired = seal_slidespec(SlideSpec.model_validate(candidate))
    validate_slidespec(repaired, max_layout_depth=plan.budget.maxLayoutDepth)
    if repaired.digests.content != base.digests.content:
        raise SlideSpecError("repair.invariant.content_changed")
    if repaired.digests.hierarchy != base.digests.hierarchy:
        raise SlideSpecError("repair.invariant.hierarchy_changed")
    if repaired.digests.importantTokens != base.digests.importantTokens:
        raise SlideSpecError("repair.invariant.important_tokens_changed")
    constraint_report = evaluate_repair_transition(base, repaired)
    if constraint_report.hard_failure:
        raise SlideSpecError(
            constraint_report.primary_code or "constraint.transition.failed"
        )
    return AppliedRepair(spec=repaired, touchedLayoutIds=touched)

def _render_node(
    node: LayoutNode,
    content: Mapping[str, ContentNode],
    entries: list[SemanticDomEntry],
) -> str:
    def percent(value: float) -> str:
        return f"{value * 100:.6f}".rstrip("0").rstrip(".") + "%"

    def qid(value: str) -> str:
        # Semantic/layout ids may contain '/', while authored patch qids may not.
        # Keep the browser target opaque and publish the reversible association in
        # ``semanticDom`` instead of weakening either identifier contract.
        return "q-" + sha256(value.encode("utf-8")).hexdigest()[:24]

    styles: dict[str, str] = {"box-sizing": "border-box"}
    if node.bounds is not None:
        styles.update(
            {
                "height": percent(node.bounds.height),
                "left": percent(node.bounds.x),
                "position": "absolute",
                "top": percent(node.bounds.y),
                "width": percent(node.bounds.width),
            }
        )
    else:
        styles.update({"height": "100%", "position": "relative", "width": "100%"})
    if node.kind == "stack":
        styles.update(
            {
                "align-items": node.alignment,
                "display": "flex",
                "flex-direction": "column",
                "gap": f"{node.spacing:g}px",
                "justify-content": node.justification,
            }
        )
    elif node.kind == "grid":
        tracks = node.columnRatio or tuple(
            track.value for track in node.tracks if track.unit == "fraction"
        )
        styles.update(
            {
                "align-items": node.alignment,
                "display": "grid",
                "gap": f"{node.spacing:g}px",
                "grid-template-columns": " ".join(f"{value:g}fr" for value in tracks)
                if tracks
                else "1fr",
                "justify-content": node.justification,
            }
        )
    padding = node.padding
    if any((padding.top, padding.right, padding.bottom, padding.left)):
        styles["padding"] = " ".join(
            percent(value)
            for value in (padding.top, padding.right, padding.bottom, padding.left)
        )
    if node.gridPlacement is not None:
        placement = node.gridPlacement
        styles["grid-column"] = (
            f"{placement.column + 1} / span {placement.columnSpan}"
        )
        styles["grid-row"] = f"{placement.row + 1} / span {placement.rowSpan}"
    if node.fontScale != 1:
        styles["font-size"] = f"{node.fontScale:g}em"
    style = ";".join(f"{name}:{styles[name]}" for name in sorted(styles))
    node_qid = qid(node.contentSemanticId or node.layoutId)
    attrs = (
        f'data-layout-id="{escape(node.layoutId, quote=True)}" '
        f'data-qid="{node_qid}" style="{escape(style, quote=True)}"'
    )
    if node.intent is not None and node.intent.allowIntentionalOverlap:
        attrs += ' data-allow-overlap="true"'
        if node.intent.intentionalOverlapGroup is not None:
            attrs += (
                ' data-intentional-overlap-group="'
                f'{escape(node.intent.intentionalOverlapGroup, quote=True)}"'
            )
    if node.contentSemanticId is not None:
        item = content[node.contentSemanticId]
        entries.append(
            SemanticDomEntry(
                semanticId=item.semanticId,
                layoutId=node.layoutId,
                qid=node_qid,
                bounds=node.bounds,
            )
        )
        attrs += f' data-semantic-id="{escape(item.semanticId, quote=True)}"'
        if item.kind == "connector" and node.connectorRoute is not None:
            # Native SlideSpecs render connector geometry from the canonical
            # route itself.  Keeping the polyline inside the node's normalized
            # viewBox makes reroutes deterministic and lets Chrome measure the
            # exact endpoints used by the acceptance gate.
            points = " ".join(
                f"{point.x * 1000:g},{point.y * 1000:g}"
                for point in node.connectorRoute.points
            )
            return (
                f'<svg {attrs} viewBox="0 0 1000 1000" '
                'preserveAspectRatio="none" aria-hidden="true">'
                f'<polyline data-slidespec-connector="true" points="{points}" '
                'fill="none" stroke="currentColor" stroke-width="4" '
                'vector-effect="non-scaling-stroke"/></svg>'
            )
        body = item.text if item.text is not None else canonical_json(item.payload)
        return f"<div {attrs}>{escape(body)}</div>"
    children = "".join(_render_node(child, content, entries) for child in node.children)
    return f"<div {attrs}>{children}</div>"

def _flow_css(value: str) -> str:
    return {"start": "flex-start", "end": "flex-end"}.get(value, value)


def _text_fit_styles(policy: str) -> dict[str, str]:
    if policy == "none":
        return {
            "white-space": "nowrap",
            "overflow": "visible",
            "overflow-wrap": "normal",
            "text-overflow": "clip",
        }
    if policy == "wrap":
        return {
            "white-space": "normal",
            "overflow": "visible",
            "overflow-wrap": "anywhere",
            "text-overflow": "clip",
        }
    if policy == "truncate":
        return {
            "white-space": "nowrap",
            "overflow": "hidden",
            "overflow-wrap": "normal",
            "text-overflow": "ellipsis",
        }
    raise SlideSpecError("render.source_projection.text_fit_unsupported", policy)


def _track_css(node: LayoutNode) -> str | None:
    if node.columnRatio:
        return " ".join(f"{value:g}fr" for value in node.columnRatio)
    if not node.tracks:
        return None
    suffix = {"fraction": "fr", "normalized": "%", "content": ""}
    return " ".join(
        "auto" if track.unit == "content" else f"{track.value:g}{suffix[track.unit]}"
        for track in node.tracks
    )


def _line_height_css(request: RenderRequest, token_name: str) -> str:
    token = next((item for item in request.spec.tokens if item.name == token_name), None)
    if token is None or isinstance(token.value, bool):
        raise SlideSpecError("render.source_projection.line_height_token_missing", token_name)
    value = token.value
    if isinstance(value, (int, float)):
        if value <= 0:
            raise SlideSpecError("render.source_projection.line_height_invalid", token_name)
        return f"{value:g}"
    parsed = str(value).strip()
    if not re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|em|rem|%)?", parsed):
        raise SlideSpecError("render.source_projection.line_height_invalid", token_name)
    return parsed


def _render_imported_html(request: RenderRequest) -> RenderResult:
    source = request.source
    assert source is not None
    if digest(source.html) != source.htmlHash:
        raise SlideSpecError("render.source_hash.mismatch")

    layout_by_id = {node.layoutId: node for node, _ in _walk_layout(request.spec.layout)}
    content_by_id = {node.semanticId: node for node in request.spec.content}
    content_ids = set(content_by_id)
    binding_by_semantic = {
        binding.semanticId: binding for binding in source.bindings
    }
    if len({binding.qid for binding in source.bindings}) != len(source.bindings):
        raise SlideSpecError("render.source_binding.qid_duplicate")

    html = source.html
    entries: list[SemanticDomEntry] = []
    for binding in source.bindings:
        node = layout_by_id.get(binding.layoutId)
        if (
            node is None
            or node.contentSemanticId != binding.semanticId
            or binding.semanticId not in content_ids
            or node.bounds is None
        ):
            raise SlideSpecError("render.source_binding.invalid", binding.qid)
        entries.append(
            SemanticDomEntry(
                semanticId=binding.semanticId,
                layoutId=binding.layoutId,
                qid=binding.qid,
                bounds=node.bounds,
            )
        )
        actions: list[dict[str, Any]] = []
        if (
            node.bounds.width != binding.bounds.width
            or node.bounds.height != binding.bounds.height
        ):
            resize_action: dict[str, Any] = {
                "action": "resize",
                "qid": binding.qid,
            }
            if node.bounds.width != binding.bounds.width:
                desired_width = binding.sourceBoundsPx.width + (
                    node.bounds.width - binding.bounds.width
                ) * source.viewportWidth
                resize_action["width"] = f"{desired_width:.4f}px"
            if node.bounds.height != binding.bounds.height:
                desired_height = binding.sourceBoundsPx.height + (
                    node.bounds.height - binding.bounds.height
                ) * source.viewportHeight
                resize_action["height"] = f"{desired_height:.4f}px"
            actions.append(resize_action)
            # Authored CSS frequently carries a responsive min/max constraint.
            # ``setBounds`` is an explicit, closed mutation-envelope operation;
            # project the changed axis through that constraint as well so Chrome
            # realizes the requested outer bound instead of silently clamping it.
            bound_styles: dict[str, str] = {}
            if "width" in resize_action:
                changed_width = str(resize_action["width"])
                bound_styles[
                    "max-width"
                    if desired_width >= binding.sourceBoundsPx.width
                    else "min-width"
                ] = changed_width
            if "height" in resize_action:
                changed_height = str(resize_action["height"])
                bound_styles[
                    "max-height"
                    if desired_height >= binding.sourceBoundsPx.height
                    else "min-height"
                ] = changed_height
            if bound_styles:
                actions.append(
                    {
                        "action": "setStyle",
                        "qid": binding.qid,
                        "styles": bound_styles,
                    }
                )
        if node.bounds.x != binding.bounds.x or node.bounds.y != binding.bounds.y:
            if (
                binding.position == "static"
                and binding.transform == "none"
                and binding.staticTranslationSafe
            ):
                # CSS transforms are paint-only and therefore do not reflow
                # static siblings.  The importer must have explicitly captured a
                # composable baseline transform; older/missing telemetry stays
                # fail closed.  Candidate bounds and the post-render Chrome
                # round-trip still prove containment and the exact displacement.
                actions.append(
                    {
                        "action": "setStyle",
                        "qid": binding.qid,
                        "styles": {
                            "transform": (
                                "translate("
                                f"{(node.bounds.x - binding.bounds.x) * source.viewportWidth:.4f}px, "
                                f"{(node.bounds.y - binding.bounds.y) * source.viewportHeight:.4f}px)"
                            )
                        },
                    }
                )
            elif binding.position not in {"relative", "absolute", "fixed", "sticky"}:
                raise SlideSpecError(
                    "render.source_projection.position_unsupported", binding.qid
                )
            else:
                delta_x = (
                    node.bounds.x - binding.bounds.x
                ) * source.viewportWidth
                delta_y = (
                    node.bounds.y - binding.bounds.y
                ) * source.viewportHeight
                anchor_styles: dict[str, str] = {}
                if node.bounds.x != binding.bounds.x:
                    if binding.leftPx is not None:
                        anchor_styles["left"] = f"{binding.leftPx + delta_x:.4f}px"
                    elif binding.rightPx is not None:
                        anchor_styles["right"] = f"{binding.rightPx - delta_x:.4f}px"
                    else:
                        raise SlideSpecError(
                            "render.source_projection.horizontal_anchor_missing",
                            binding.qid,
                        )
                if node.bounds.y != binding.bounds.y:
                    if binding.topPx is not None:
                        anchor_styles["top"] = f"{binding.topPx + delta_y:.4f}px"
                    elif binding.bottomPx is not None:
                        anchor_styles["bottom"] = f"{binding.bottomPx - delta_y:.4f}px"
                    else:
                        raise SlideSpecError(
                            "render.source_projection.vertical_anchor_missing",
                            binding.qid,
                        )
                # A right/bottom anchored element must remain right/bottom
                # anchored.  Replacing an absent top/left with zero changes the
                # containing-block equation and can stretch or pin fixed nodes.
                actions.append(
                    {
                        "action": "setStyle",
                        "qid": binding.qid,
                        "styles": anchor_styles,
                    }
                )
        if node.fontScale != 1:
            if binding.fontSizePx is None:
                raise SlideSpecError(
                    "render.source_projection.font_size_missing", binding.qid
                )
            actions.append(
                {
                    "action": "setStyle",
                    "qid": binding.qid,
                    "styles": {
                        "font-size": f"{binding.fontSizePx * node.fontScale:.4f}px"
                    },
                }
            )
        style_updates: dict[str, str] = {}
        if abs(node.spacing - binding.gapPx) > 1e-6:
            if binding.display not in {"flex", "inline-flex", "grid", "inline-grid"}:
                raise SlideSpecError(
                    "render.source_projection.gap_unsupported", binding.qid
                )
            style_updates["gap"] = f"{node.spacing:g}px"
        # Imported layout bounds are clipped to the normalized viewport, while
        # authored CSS padding is relative to the element's full source box.
        # Reconstruct that projected source box (the same way resize projection
        # does above) so an untouched import remains byte-identical even when an
        # element extends past the canvas.  Using ``node.bounds`` directly here
        # caused baseline imports to round off-canvas footer padding and mutate
        # otherwise untouched slides.
        projected_width_px = binding.sourceBoundsPx.width + (
            node.bounds.width - binding.bounds.width
        ) * source.viewportWidth
        projected_height_px = binding.sourceBoundsPx.height + (
            node.bounds.height - binding.bounds.height
        ) * source.viewportHeight
        desired_padding = (
            node.padding.top * projected_height_px,
            node.padding.right * projected_width_px,
            node.padding.bottom * projected_height_px,
            node.padding.left * projected_width_px,
        )
        baseline_padding = (
            binding.paddingTopPx,
            binding.paddingRightPx,
            binding.paddingBottomPx,
            binding.paddingLeftPx,
        )
        for prop, desired, baseline in zip(
            ("padding-top", "padding-right", "padding-bottom", "padding-left"),
            desired_padding,
            baseline_padding,
            strict=True,
        ):
            if abs(desired - baseline) > 0.01:
                style_updates[prop] = f"{desired:.4f}px"
        baseline_alignment = {
            "flex-start": "start",
            "start": "start",
            "flex-end": "end",
            "end": "end",
            "center": "center",
            "stretch": "stretch",
        }.get(binding.alignItems.strip().lower(), "stretch")
        baseline_justification = {
            "flex-start": "start",
            "start": "start",
            "flex-end": "end",
            "end": "end",
            "center": "center",
            "space-between": "space-between",
        }.get(binding.justifyContent.strip().lower(), "start")
        if node.alignment != baseline_alignment:
            if binding.display not in {"flex", "inline-flex", "grid", "inline-grid"}:
                raise SlideSpecError(
                    "render.source_projection.alignment_unsupported", binding.qid
                )
            style_updates["align-items"] = _flow_css(node.alignment)
        if node.justification != baseline_justification:
            if binding.display not in {"flex", "inline-flex", "grid", "inline-grid"}:
                raise SlideSpecError(
                    "render.source_projection.justification_unsupported", binding.qid
                )
            style_updates["justify-content"] = _flow_css(node.justification)
        if node.columnRatio != binding.columnRatio or node.tracks:
            if binding.display not in {"grid", "inline-grid"}:
                raise SlideSpecError(
                    "render.source_projection.grid_unsupported", binding.qid
                )
            tracks = _track_css(node)
            if tracks is None:
                raise SlideSpecError(
                    "render.source_projection.grid_tracks_missing", binding.qid
                )
            style_updates["grid-template-columns"] = tracks
        if node.gridPlacement != binding.gridPlacement:
            content = content_by_id.get(binding.semanticId)
            parent_binding = (
                binding_by_semantic.get(content.parentSemanticId)
                if content is not None and content.parentSemanticId is not None
                else None
            )
            if (
                parent_binding is None
                or parent_binding.display not in {"grid", "inline-grid"}
                or node.gridPlacement is None
            ):
                raise SlideSpecError(
                    "render.source_projection.grid_placement_unsupported", binding.qid
                )
            placement = node.gridPlacement
            style_updates["grid-column"] = (
                f"{placement.column + 1} / span {placement.columnSpan}"
            )
            style_updates["grid-row"] = f"{placement.row + 1} / span {placement.rowSpan}"
        if node.lineHeightToken is not None:
            style_updates["line-height"] = _line_height_css(
                request, node.lineHeightToken
            )
        if node.textFitPolicy != binding.textFitPolicy:
            style_updates.update(_text_fit_styles(node.textFitPolicy))
        if style_updates:
            actions.append(
                {
                    "action": "setStyle",
                    "qid": binding.qid,
                    "styles": style_updates,
                }
            )
        crop_changed = (
            node.imageFit != binding.objectFit
            or node.cropFocalPoint != binding.cropFocalPoint
        )
        if crop_changed:
            if binding.tag != "img":
                raise SlideSpecError(
                    "render.source_projection.image_unsupported", binding.qid
                )
            actions.append(
                {
                    "action": "crop",
                    "qid": binding.qid,
                    "fit": node.imageFit,
                    "position": (
                        f"{node.cropFocalPoint.x * 100:g}% "
                        f"{node.cropFocalPoint.y * 100:g}%"
                    ),
                }
            )
        if node.connectorRoute is not None:
            raise SlideSpecError(
                "render.source_projection.connector_unsupported", binding.qid
            )
        for action in actions:
            patched = apply_authored_patch(
                html,
                {
                    "strategy": "patch",
                    "actions": [action],
                    "rationale": "SlideSpec imported-source projection",
                },
            )
            if not patched.ok:
                raise SlideSpecError(
                    "render.source_projection.failed",
                    patched.fallback_reason or patched.failure_reason or binding.qid,
                )
            html = patched.html

    input_hash = digest(request)
    dom = tuple(entries)
    html_hash = digest(html)
    dom_hash = digest(dom)
    return RenderResult(
        html=html,
        renderInputHash=input_hash,
        htmlHash=html_hash,
        semanticDom=dom,
        semanticDomHash=dom_hash,
        artifactHashes={"html": html_hash, "semanticDom": dom_hash},
    )


def render_slidespec(request: RenderRequest) -> RenderResult:
    """Deterministically render native or imported semantic-bound HTML."""

    validate_slidespec(request.spec)
    if request.source is not None:
        return _render_imported_html(request)
    input_hash = digest(request)
    entries: list[SemanticDomEntry] = []
    body = _render_node(
        request.spec.layout,
        {node.semanticId: node for node in request.spec.content},
        entries,
    )
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "html,body{height:100%;margin:0;overflow:hidden;width:100%}"
        "</style></head><body>"
        f'<section data-slidespec="{request.spec.schemaUri.rsplit(":", 1)[-1]}" '
        f'data-render-input-hash="{input_hash}" '
        'data-qid="q-slidespec-canvas" style="background:#fff;box-sizing:border-box;'
        'color:#111;font-family:Arial,sans-serif;font-size:32px;height:720px;'
        'overflow:hidden;position:relative;width:1280px">'
        f"{body}</section></body></html>"
    )
    dom = tuple(entries)
    html_hash = digest(html)
    dom_hash = digest(dom)
    return RenderResult(
        html=html,
        renderInputHash=input_hash,
        htmlHash=html_hash,
        semanticDom=dom,
        semanticDomHash=dom_hash,
        artifactHashes={"html": html_hash, "semanticDom": dom_hash},
    )

def _opaque_id(prefix: str) -> str:
    return prefix + uuid4().hex

def _adaptive_blocks(source: Any) -> list[dict[str, Any]]:
    if isinstance(source, BaseModel):
        from models.slide_spec_model import spec_to_blocks

        return [dict(block) for block in spec_to_blocks(source)["blocks"]]
    data = dict(source)
    if "blocks" in data:
        return [
            block.model_dump(mode="json") if isinstance(block, BaseModel) else dict(block)
            for block in data["blocks"]
        ]
    from models.slide_spec_model import SlideSpecUnion, spec_to_blocks

    parsed = TypeAdapter(SlideSpecUnion).validate_python(data)
    return [dict(block) for block in spec_to_blocks(parsed)["blocks"]]

def adapt_adaptive_slide(
    source: Any,
    *,
    slide_id: str,
    identity_map: Mapping[str, str] | None = None,
) -> tuple[SlideSpec, dict[str, str]]:
    """Minimally adapt legacy blocks; persist the returned map for stable identities."""

    blocks = _adaptive_blocks(source)
    if not blocks:
        raise SlideSpecError("adapter.blocks.empty")
    identities = dict(identity_map or {})
    root_key = "layout:$root"
    identities.setdefault(root_key, _opaque_id("lay:"))
    contents: list[ContentNode] = []
    layouts: list[LayoutNode] = []
    kind_map = {
        "title": "title",
        "text": "text",
        "bullets": "list",
        "list": "list",
        "stat": "metric",
        "metric": "metric",
        "table": "table",
        "chart": "chart",
        "image": "image",
        "connector": "connector",
    }
    seen_locators: set[str] = set()
    height = 1 / len(blocks)
    for index, block in enumerate(blocks):
        source_id = str(block.get("id") or block.get("blockId") or f"slot-{index}")
        if source_id in seen_locators:
            raise SlideSpecError("adapter.block_id.duplicate", source_id)
        seen_locators.add(source_id)
        semantic_key = f"semantic:{source_id}"
        layout_key = f"layout:{source_id}"
        identities.setdefault(semantic_key, _opaque_id("sem:"))
        identities.setdefault(layout_key, _opaque_id("lay:"))
        block_type = str(block.get("type") or block.get("kind") or "text")
        text = next(
            (
                str(block[key])
                for key in ("text", "title", "value", "label")
                if block.get(key) is not None
            ),
            None,
        )
        payload = {key: value for key, value in block.items() if key not in {"id", "blockId"}}
        contents.append(
            ContentNode(
                semanticId=identities[semantic_key],
                kind=kind_map.get(block_type, "shape"),
                text=text,
                payload=payload,
                readingOrder=index,
            )
        )
        layouts.append(
            LayoutNode(
                layoutId=identities[layout_key],
                kind="content",
                contentSemanticId=identities[semantic_key],
                bounds=Bounds(x=0, y=index * height, width=1, height=height),
            )
        )
    draft = SlideSpec(
        schemaUri=SLIDESPEC_SCHEMA,
        specRevision=1,
        slideId=slide_id,
        content=tuple(contents),
        layout=LayoutNode(
            layoutId=identities[root_key], kind="canvas", children=tuple(layouts)
        ),
        tokens=(),
        provenance=Provenance(
            source="adaptive-adapter", adapterVersion=ADAPTER_VERSION
        ),
        digests=SlideDigests(
            content=ZERO_DIGEST,
            hierarchy=ZERO_DIGEST,
            importantTokens=ZERO_DIGEST,
            spec=ZERO_DIGEST,
        ),
    )
    sealed = seal_slidespec(draft)
    validate_slidespec(sealed)
    return sealed, identities
