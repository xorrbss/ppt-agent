"""Prompt assembly for bounded authored slide patch proposals."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from llmai.shared import ImageContentPart, SystemMessage, TextContentPart, UserMessage
from utils.authored_patch_engine import (
    EXACT_RECONSTRUCTION, STYLE_REFERENCE, ReferenceMode,
    allowed_patch_style_properties,
)
from utils.llm_calls.author_slide_patch_contracts import (
    StructuralRepairScope, _MAX_CRITIQUE_CHARS, _MAX_DOM_JSON_CHARS,
    _STRUCTURAL_SCOPE_CONTRACT, _SYSTEM, _bounded_string, _image_mime_type,
)
from utils.llm_calls.author_slide_patch_dom import (
    build_full_dom_graph, compact_dom_map_for_prompt,
)

def _critique_payload(critique: Any) -> Any:
    if hasattr(critique, "model_dump"):
        critique = critique.model_dump(mode="json")
    if isinstance(critique, Mapping):
        encoded = json.dumps(dict(critique), ensure_ascii=False, default=str)
    else:
        encoded = json.dumps(
            {"summary": _bounded_string(critique, _MAX_CRITIQUE_CHARS)},
            ensure_ascii=False,
        )
    if len(encoded) > _MAX_CRITIQUE_CHARS:
        return {"summary": encoded[:_MAX_CRITIQUE_CHARS] + "..."}
    return json.loads(encoded)

def _coerce_repair_scope(
    repair_scope: StructuralRepairScope | Mapping[str, Any] | None,
) -> StructuralRepairScope | None:
    if repair_scope is None or isinstance(repair_scope, StructuralRepairScope):
        return repair_scope
    return StructuralRepairScope.model_validate(repair_scope)

def _structural_prompt_dom(
    normalized: Sequence[Mapping[str, Any]], scope: StructuralRepairScope,
) -> list[dict[str, Any]]:
    """Compress values, never identities/relationships or the tail of a group."""
    affected = set(scope.affected_qids)
    editable = set(scope.editable_qids)
    direct_children = {
        item["qid"] for item in normalized
        if item.get("parentQid") == scope.layout_owner_qid
    }
    detailed = editable | direct_children | {scope.layout_owner_qid}
    layout_style_keys = {
        "display", "position", "overflow", "overflowX", "overflowY",
        "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
        "boxSizing", "gridTemplateColumns", "gridTemplateRows",
        "flexDirection", "flexWrap", "gap", "rowGap", "columnGap",
        "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
        "marginTop", "marginRight", "marginBottom", "marginLeft",
        "alignItems", "justifyContent", "whiteSpace",
    }
    text_style_keys = {
        "fontFamily", "fontSize", "fontWeight", "lineHeight", "whiteSpace",
        "overflowWrap", "wordBreak", "textAlign",
    }
    result = []
    for item in normalized:
        node = dict(item)
        node["editable"] = item["qid"] in scope.editable_qids
        if "text" in node:
            node["text"] = _bounded_string(
                node["text"], 160 if item["qid"] in affected else 40
            )
        style = node.get("computedStyle")
        if isinstance(style, Mapping):
            keep = set()
            if item["qid"] in detailed:
                keep |= layout_style_keys
            if item.get("sourceBlockId") or node.get("text"):
                keep |= text_style_keys
            if keep:
                node["computedStyle"] = {
                    key: value for key, value in style.items() if key in keep
                }
            else:
                node.pop("computedStyle", None)
        result.append(node)

    def fits():
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= _MAX_DOM_JSON_CHARS

    if fits():
        return result
    for node in result:
        if "text" in node:
            node["text"] = _bounded_string(node["text"], 80)
        style = node.get("computedStyle")
        if isinstance(style, Mapping):
            node["computedStyle"] = {
                key: value for key, value in style.items()
                if key not in {"backgroundColor", "borderColor", "borderRadius", "color", "fontStyle", "objectPosition"}
            }
    if fits():
        return result
    by_qid = {node["qid"]: node for node in result}
    # Inherited values remain recoverable through the parent reference.
    for node in reversed(result):
        parent = by_qid.get(node.get("parentQid"), {})
        style, parent_style = node.get("computedStyle", {}), parent.get("computedStyle", {})
        inherited = [key for key in ("fontFamily", "fontSize", "fontWeight", "lineHeight", "textAlign", "whiteSpace") if key in style and style[key] == parent_style.get(key)]
        for key in inherited:
            del style[key]
        if inherited:
            node["inheritedStyleKeys"] = inherited
    if fits():
        return result
    raise ValueError("repair_prompt_budget_exceeded: complete structural prompt exceeds 48000 characters")

def build_patch_messages(
    rendered_image: bytes,
    compact_dom_map: Sequence[Mapping[str, Any]],
    critique: Any,
    *,
    reference_mode: ReferenceMode = STYLE_REFERENCE,
    allow_source_reflow_safe_styles: bool = False,
    repair_scope: StructuralRepairScope | Mapping[str, Any] | None = None,
    html: str | None = None,
) -> tuple[list[Any], set[str]]:
    if reference_mode not in {STYLE_REFERENCE, EXACT_RECONSTRUCTION}:
        raise ValueError(f"unsupported reference mode {reference_mode!r}")
    mime_type = _image_mime_type(rendered_image)
    scope = _coerce_repair_scope(repair_scope)
    if scope is None:
        normalized_dom, qids = compact_dom_map_for_prompt(compact_dom_map)
    else:
        graph = build_full_dom_graph(compact_dom_map, html=html)
        normalized_dom, qids = list(graph.nodes), set(graph.qids)
    if scope is not None:
        if not allow_source_reflow_safe_styles:
            raise ValueError(
                "repair_scope requires allow_source_reflow_safe_styles=True"
            )
        scoped_qids = {
            scope.layout_owner_qid,
            *scope.editable_qids,
            *scope.affected_qids,
            *scope.protected_qids,
        }
        unknown_scope_qids = sorted(scoped_qids - qids)
        if unknown_scope_qids:
            raise ValueError(
                "repair_scope contains qid(s) absent from compact DOM map: "
                f"{unknown_scope_qids}"
            )
        if set(scope.affected_qids) | set(scope.protected_qids) != qids:
            raise ValueError(
                "repair_scope must classify every compact DOM qid as affected or protected"
            )
        normalized_dom = _structural_prompt_dom(normalized_dom, scope)
    payload = {
        "referenceMode": reference_mode,
        "critique": _critique_payload(critique),
        "compactDomMap": normalized_dom,
    }
    if scope is not None:
        payload["repairScope"] = scope.model_dump(mode="json")
        payload["allowedStructuralStyleProperties"] = list(
            allowed_patch_style_properties(source_reflow=True)
        )
    prompt = (
        "Propose the smallest safe patch for this rendered slide. Input data:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    return (
        [
            SystemMessage(
                content=(
                    _SYSTEM + _STRUCTURAL_SCOPE_CONTRACT
                    if scope is not None
                    else _SYSTEM
                )
            ),
            UserMessage(
                content=[
                    TextContentPart(text=prompt),
                    ImageContentPart(data=rendered_image, mime_type=mime_type),
                ]
            ),
        ],
        qids,
    )
