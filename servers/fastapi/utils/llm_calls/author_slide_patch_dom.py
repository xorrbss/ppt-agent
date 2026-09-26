"""Bounded DOM normalization and full-graph construction for slide repair."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from utils.llm_calls.author_slide_patch_contracts import (
    _BBOX_KEYS,
    _MAX_DOM_ELEMENTS,
    _MAX_DOM_JSON_CHARS,
    _QID_PATTERN,
    _ROOT_QIDS,
    _STYLE_KEYS,
    _TAG_PATTERN,
    _bounded_number,
    _bounded_string,
)


def _normalize_dom_map(
    compact_dom_map: Sequence[Mapping[str, Any]],
    *,
    full_graph: bool = False,
) -> tuple[list[dict[str, Any]], set[str]]:
    if isinstance(compact_dom_map, (str, bytes, bytearray)):
        raise TypeError("compact_dom_map must be a sequence of mappings")
    if full_graph and len(compact_dom_map) > _MAX_DOM_ELEMENTS:
        raise ValueError("repair_graph_incomplete: DOM element safety limit exceeded")

    normalized: list[dict[str, Any]] = []
    qids: set[str] = set()
    for raw in list(compact_dom_map)[:_MAX_DOM_ELEMENTS]:
        if not isinstance(raw, Mapping):
            if full_graph:
                raise ValueError("repair_graph_incomplete: non-mapping DOM node")
            continue
        qid = str(raw.get("qid") or raw.get("data-qid") or "")
        tag = str(raw.get("tag") or "").lower()
        if not _QID_PATTERN.fullmatch(qid) or not _TAG_PATTERN.fullmatch(tag):
            if full_graph:
                raise ValueError("repair_graph_incomplete: invalid DOM identity")
            continue
        if qid in qids:
            raise ValueError(f"duplicate qid {qid!r} in compact_dom_map")
        qids.add(qid)

        item: dict[str, Any] = {"qid": qid, "tag": tag}
        if "text" in raw:
            item["text"] = str(raw.get("text") or "") if full_graph else _bounded_string(raw.get("text"), 300)
        if "editable" in raw:
            item["editable"] = bool(raw.get("editable"))
        parent_qid = str(raw.get("parentQid") or raw.get("parent_qid") or "")
        if _QID_PATTERN.fullmatch(parent_qid):
            item["parentQid"] = parent_qid
        elif parent_qid and full_graph:
            raise ValueError("repair_graph_incomplete: invalid parent qid")
        if full_graph:
            for key in ("layoutOwnerQid", "clippingAncestorQid", "containingBlockQid", "occludingQid"):
                if raw.get(key):
                    item[key] = str(raw[key])
        source_block_id = _bounded_string(raw.get("sourceBlockId") or raw.get("source_block_id"), 160).strip()
        if source_block_id:
            item["sourceBlockId"] = source_block_id
        semantic_role = _bounded_string(raw.get("semanticRole") or raw.get("semantic_role"), 80).strip()
        if semantic_role:
            item["semanticRole"] = semantic_role

        raw_bbox = raw.get("bbox")
        if isinstance(raw_bbox, Mapping):
            bbox = {key: number for key in _BBOX_KEYS if (number := _bounded_number(raw_bbox.get(key))) is not None}
            if bbox:
                item["bbox"] = bbox

        raw_style = raw.get("computedStyle") or raw.get("computed_style") or raw.get("style")
        if isinstance(raw_style, Mapping):
            style = {
                key: str(raw_style[key]) if full_graph else _bounded_string(raw_style[key], 120)
                for key in _STYLE_KEYS
                if key in raw_style
            }
            if style:
                item["computedStyle"] = style
        normalized.append(item)

    if not normalized:
        raise ValueError("compact_dom_map contains no valid data-qid elements")

    return normalized, qids


def compact_dom_map_for_prompt(
    compact_dom_map: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Bound ordinary patch prompts; structural analysis never uses this result."""
    normalized, qids = _normalize_dom_map(compact_dom_map)

    # Avoid splitting JSON syntax. Drop entries from the end until the encoded map is
    # under budget; a single oversized entry is already bounded field-by-field above.
    while normalized and len(json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))) > _MAX_DOM_JSON_CHARS:
        removed = normalized.pop()
        qids.discard(removed["qid"])
    if not normalized:
        raise ValueError("compact_dom_map exceeds the prompt budget")
    return normalized, qids


@dataclass(frozen=True)
class FullDomGraph:
    nodes: tuple[dict[str, Any], ...]
    qids: frozenset[str]
    system_root_qids: frozenset[str]
    verified_content_qids: frozenset[str]


class _ScopeHtmlRelations(HTMLParser):
    """Read ancestry without changing HTML or inventing missing measured nodes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []
        self.nodes: dict[str, dict[str, Any]] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        qid = str(values.get("data-qid") or "")
        if qid:
            if qid in self.nodes:
                raise ValueError("repair_graph_incomplete: duplicate HTML qid")
            parent = next((value for _, value in reversed(self.stack) if value), "")
            markers = {str(values.get("id") or "").lower(), *str(values.get("class") or "").lower().split()}
            system = (
                tag in {"html", "body"}
                or qid.lower() in _ROOT_QIDS
                or bool(markers & {"root", "slide", "slide-root", "slide-canvas", "capture-frame", "slide-frame"})
                or values.get("data-semantic-role") in {"canvas", "slide", "slide-root", "capture-frame"}
                or "data-slide-canvas" in values
            )
            self.nodes[qid] = {
                "tag": tag,
                "classes": frozenset(str(values.get("class") or "").split()),
                "inlineStyle": str(values.get("style") or ""),
                "headerBandHeight": str(values.get("data-header-band") or "")
                if values.get("data-header-part") == "band"
                else "",
                "parentQid": parent,
                "systemRoot": system,
                "insideBody": any(name == "body" for name, _ in self.stack),
                "wrapperTags": [name for name, value in reversed(self.stack) if not value][:16],
            }
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((tag, qid))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def build_full_dom_graph(
    compact_dom_map: Sequence[Mapping[str, Any]],
    *,
    html: str | None = None,
) -> FullDomGraph:
    """Validate complete QID topology independently of the 48,000 character prompt."""
    nodes, qids = _normalize_dom_map(compact_dom_map, full_graph=True)
    roots = {item["qid"] for item in nodes if item["qid"].lower() in _ROOT_QIDS or item["tag"] in {"html", "body"}}
    verified: set[str] = set()
    if html is not None:
        relations = _ScopeHtmlRelations()
        relations.feed(html)
        relations.close()
        if set(relations.nodes) != qids:
            raise ValueError("repair_graph_incomplete: HTML and measured QID inventories differ")
        for item in nodes:
            relation = relations.nodes[item["qid"]]
            measured_parent = item.get("parentQid", "")
            if measured_parent and measured_parent != relation["parentQid"]:
                raise ValueError("repair_graph_incomplete: HTML and measured ancestry disagree")
            if relation["parentQid"]:
                item["parentQid"] = relation["parentQid"]
            item["wrapperTags"] = relation["wrapperTags"]
            header_height = relation["headerBandHeight"]
            if re.fullmatch(r"\d+(?:\.\d+)?", header_height):
                measured_height = item.get("bbox", {}).get("height")
                item["verifiedHeaderBand"] = (
                    0 < float(header_height) <= 300
                    and measured_height is not None
                    and abs(measured_height - float(header_height)) <= 1
                )
            if relation["systemRoot"]:
                roots.add(item["qid"])
            elif relation["insideBody"]:
                verified.add(item["qid"])
    by_qid = {item["qid"]: item for item in nodes}
    for item in nodes:
        visited = {item["qid"]}
        parent = item.get("parentQid")
        while parent:
            if parent not in qids:
                raise ValueError("repair_graph_incomplete: parent absent from full DOM")
            if parent in visited:
                raise ValueError("repair_graph_incomplete: parent cycle")
            visited.add(parent)
            parent = by_qid[parent].get("parentQid")
    return FullDomGraph(tuple(nodes), frozenset(qids), frozenset(roots), frozenset(verified))
