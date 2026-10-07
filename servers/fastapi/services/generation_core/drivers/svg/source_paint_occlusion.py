"""V7 source paint order against measured text ink; no aggregate ink claims."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import json
import math

from services.svg_contract import source_digest
from services.svg_text_overlap import _ink_rect
from services.generation_core.drivers.svg.source_classification_association import (
    _finite_number, _paint_contains, _records,
)

GUARD_VERSION = "svg-source-paint-occlusion/v1"
MIN_INTERSECTION_PX = .25


def source_paint_repair_hint(quality: dict, source: str = "") -> str:
    """Give the author visible labels and positions, not opaque probe IDs alone."""
    receipt = quality["source_paint_occlusion"]
    elements = (quality.get("browser_metrics") or {}).get("elements") or []
    by_qid = {item.get("qid"): item for item in elements}
    witnesses, seen = [], set()
    for witness in receipt.get("collisions") or receipt.get("unproven", []):
        qid = witness.get("text_qid")
        if qid in seen:
            continue
        seen.add(qid)
        text, paint = by_qid.get(qid, {}), by_qid.get(witness.get("paint_qid"), {})
        box = _ink_rect(paint.get("bbox"))
        entry = {"text_qid": qid, "text": str(text.get("owned_text") or "")[:96],
                 "paint_qid": witness.get("paint_qid")}
        if box is not None:
            entry["paint_box"] = {key: round(value, 2) for key, value in box.items()}
        if witness.get("reason"):
            entry["reason"] = witness["reason"]
        witnesses.append(entry)
        if len(witnesses) == 3:
            break
    floor = quality.get("body_font_floor_px", 0)
    floor = floor if isinstance(floor, (int, float)) and math.isfinite(floor) and floor > 0 else 0
    hint = {"kind": "svg_source_paint_occluded" if receipt["status"] == "occluded" else "svg_source_paint_unproven",
        "instruction": "Preserve every original entity, class membership, relationship, label and sentence. Keep all lines of the affected labels clear of later opaque nodes; move or wrap labels and their adjacent nodes together. Do not delete, shorten, hide, clip or shrink text. Resolve other measured failures too.",
        "witnesses": witnesses,
        "unproven_classes": [str(label)[:80] for label in quality.get("source_classification_association", {}).get("unproven_labels", [])[:3]],
        "geometry_failure_kinds": list(dict.fromkeys(str(failure.get("kind"))
            for failure in quality.get("failures", [])))[:5],
        "minimum_body_px": floor, "safe_body_px": math.ceil(floor)}
    if source:
        from services.generation_core.drivers.svg.source_classification_association import source_classifications

        hint["required_classes"] = source_classifications(source)
    encoded = json.dumps(hint, ensure_ascii=False, separators=(",", ":"))
    while len(encoded) > 1500 and len(hint["witnesses"]) > 1:
        hint["witnesses"].pop()
        encoded = json.dumps(hint, ensure_ascii=False, separators=(",", ":"))
    return encoded


def _intersection(first, second):
    x, y = max(first["x"], second["x"]), max(first["y"], second["y"])
    width = min(first["x"] + first["width"], second["x"] + second["width"]) - x
    height = min(first["y"] + first["height"], second["y"] + second["height"]) - y
    return {"x": x, "y": y, "width": width, "height": height} if min(width, height) > MIN_INTERSECTION_PX else None


def _inside_shape(paint, region):
    if _paint_contains(paint, region):
        return "inside"
    # The closest point to an axis-aligned rounded rectangle's centre proves
    # an intersection wholly outside its rounded corner is empty.
    box = paint["box"]
    inset = 1e-7
    x = min(max(box["x"] + box["width"] / 2, region["x"] + inset), region["x"] + region["width"] - inset)
    y = min(max(box["y"] + box["height"] / 2, region["y"] + inset), region["y"] + region["height"] - inset)
    return "partial" if _paint_contains(paint, {"x": x, "y": y, "width": 0, "height": 0}) else "outside"


def _opaque_unclipped(node, parents, item):
    if _finite_number(item.get("effective_opacity", 0)) != 1:
        return False
    while node is not None:
        if any(node.get(key) for key in ("style", "clip-path", "mask", "filter")):
            return False
        if any(_finite_number(node.get(key, "1")) != 1 for key in ("opacity", "fill-opacity")):
            return False
        node = parents.get(node)
    return not item.get("clipped") and not item.get("glyph_clipped")


def _complete_glyphs(item, clusters):
    required, offset = set(), 0
    for character in str(item.get("owned_text") or ""):
        length = len(character.encode("utf-16-le")) // 2
        if not character.isspace():
            required.update(range(offset, offset + length))
        offset += length
    covered = set()
    for cluster in clusters:
        index, length = cluster.get("index"), cluster.get("length")
        if isinstance(index, int) and not isinstance(index, bool) and isinstance(length, int) and 0 < length <= offset:
            covered.update(range(index, index + length))
    return bool(required) and covered == required


def apply_source_paint_occlusion(quality: dict, svg: str) -> None:
    receipt = {"version": GUARD_VERSION, "status": "not_applicable",
               "basis": "later_opaque_source_rect_and_reliable_glyph_ink",
               "collisions": [], "unproven": []}
    quality["source_paint_occlusion"] = receipt
    elements = (quality.get("browser_metrics") or {}).get("elements") or []
    root = ET.fromstring(svg)
    ordered = list(root.iter())
    nodes = {f"svg-{index}": node for index, node in enumerate(ordered)}
    order = {f"svg-{index}": index for index in range(len(ordered))}
    parents = {child: node for node in ordered for child in node}
    _, paints, _ = _records(svg, elements)
    supported = {paint["item"]["qid"]: paint for paint in paints
                 if paint["item"]["tag"] == "rect"
                 and _opaque_unclipped(paint["node"], parents, paint["item"])}
    bound = (quality.get("measurement_complete") is True
             and quality.get("source_digest") == source_digest(svg)
             and isinstance(quality.get("wrapper_digest"), str)
             and quality.get("browser_metrics", {}).get("source_digest") == quality["wrapper_digest"])
    rectangles = [item for item in elements if item.get("tag") == "rect"
                  and item.get("qid") in nodes and item.get("css_visible") is not False
                  and _ink_rect(item.get("bbox")) is not None
                  and _finite_number(item.get("effective_opacity")) != 0
                  and nodes[item["qid"]].get("fill") not in ("none", "transparent")]
    candidates = 0
    for item in elements:
        qid = item.get("qid")
        if qid not in nodes or item.get("tag") not in {"text", "tspan"} or not item.get("has_owned_text"):
            continue
        if item.get("css_visible") is False or _finite_number(item.get("effective_opacity")) == 0:
            continue
        fragments = item.get("text_ink_fragments") or []
        # An owned text bbox only establishes a candidate when ink is absent.
        # It never becomes a glyph or a positive masking witness.
        candidate_fragments = fragments or [item.get("bbox", {})]
        clusters = [cluster for fragment in fragments if isinstance(fragment, dict)
                    for cluster in (fragment.get("glyphInkClusters") or [])
                    if isinstance(cluster, dict)
                    and cluster.get("basis") == "isolated-matching-advance" and _ink_rect(cluster) is not None]
        complete = _complete_glyphs(item, clusters)
        for rectangle in rectangles:
            rid = rectangle["qid"]
            if order[rid] <= order[qid]:
                continue  # A label drawn on its earlier node background is legitimate.
            box, paint = _ink_rect(rectangle["bbox"]), supported.get(rid) if bound else None
            if item.get("css_visible") is not True or _finite_number(item.get("effective_opacity")) is None:
                paint = None
            regions = [_intersection(ink, box) for fragment in candidate_fragments
                       if (ink := _ink_rect(fragment)) is not None]
            regions = [region for region in regions if region is not None
                       and (paint is None or _inside_shape(paint, region) != "outside")]
            if not regions:
                continue
            candidates += 1
            hits = [(cluster, region) for cluster in clusters
                    if (region := _intersection(_ink_rect(cluster), box)) is not None
                    and (paint is None or _inside_shape(paint, region) != "outside")]
            proven = []
            for cluster, region in hits:
                if paint is not None and _paint_contains(paint, _ink_rect(cluster)):
                    proven.append({"text_qid": qid, "paint_qid": rid,
                                   "cluster_index": cluster["index"], "ink": _ink_rect(cluster),
                                   "intersection": region})
            receipt["collisions"].extend(proven)
            if not proven and (not complete or hits):
                receipt["unproven"].append({"text_qid": qid, "paint_qid": rid,
                    "reason": "reliable_glyph_coverage_incomplete" if not complete else "source_rect_paint_or_intersection_unproven"})
    receipt["candidate_pairs"] = candidates
    receipt["status"] = "occluded" if receipt["collisions"] else "unproven" if receipt["unproven"] else "clear" if candidates else "not_applicable"
    if receipt["collisions"] or receipt["unproven"]:
        quality["passed"] = quality["verified"] = False
        if receipt["collisions"]:
            quality["geometry_passed"] = False
        kind = "source_text_paint_occluded" if receipt["collisions"] else "source_text_paint_unproven"
        quality.setdefault("failures", []).append({"kind": kind,
            "pairs": receipt["collisions"] if receipt["collisions"] else receipt["unproven"]})
