"""Measured classification membership without requiring a list layout."""
from __future__ import annotations

import itertools
import hashlib
import math
import re
from xml.etree import ElementTree as ET

from services.svg_text_overlap import _ink_rect

GUARD_VERSION = "svg-source-classification-association/v1"


def _normalized(value):
    return re.sub(r"[\s,:：，、·•]+", "", str(value or ""))


def source_classifications(source):
    from services.generation_core.authoring.author_slide_svg import source_classification_coverage
    required = source_classification_coverage(source or "", ET.Element("svg"))["missing_lists"]
    return [{"label": line.split(": ", 1)[0],
             "members": [member.strip() for member in re.split(r"[,，、]", line.split(": ", 1)[1])]}
            for line in required]


def _contains(outer, inner):
    return bool(outer and inner) and (inner["x"] >= outer["x"] and inner["y"] >= outer["y"]
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"])


def _finite_number(value):
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", str(value)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _axis_aligned(node, parents):
    while node is not None:
        if node.get("style"):
            return False
        transform = node.get("transform", "").strip()
        if transform:
            match = re.fullmatch(r"(translate|scale)\s*\(\s*([^()]*)\s*\)", transform)
            if match is None:
                return False
            arguments = match[2].strip()
            values = [_finite_number(value.strip()) for value in
                      (arguments.split(",") if "," in arguments else arguments.split())]
            if len(values) not in (1, 2) or any(value is None for value in values):
                return False
            if match[1] == "scale" and any(value == 0 for value in values):
                return False
        node = parents.get(node)
    return True


def _measured_axes(item):
    quad = item.get("quad")
    if not isinstance(quad, list) or len(quad) != 4:
        return False
    try:
        points = [(float(point["x"]), float(point["y"])) for point in quad]
    except (TypeError, ValueError, KeyError):
        return False
    if not all(math.isfinite(value) for point in points for value in point):
        return False
    box = _ink_rect(item.get("bbox"))
    if box is None or any(abs(actual - expected) > 1e-6 for actual, expected in (
            (min(point[0] for point in points), box["x"]),
            (max(point[0] for point in points), box["x"] + box["width"]),
            (min(point[1] for point in points), box["y"]),
            (max(point[1] for point in points), box["y"] + box["height"]))):
        return False
    horizontal = [abs(first[1] - second[1]) <= 1e-6 for first, second in zip(points, points[1:] + points[:1])]
    vertical = [abs(first[0] - second[0]) <= 1e-6 for first, second in zip(points, points[1:] + points[:1])]
    return all(horizontal[i] and vertical[(i + 1) % 4] for i in (0, 2)) or all(
        vertical[i] and horizontal[(i + 1) % 4] for i in (0, 2))


def _finite_geometry(node, tag):
    fields = {"rect": ("x", "y", "width", "height"), "circle": ("cx", "cy", "r"),
              "ellipse": ("cx", "cy", "rx", "ry")}[tag]
    return all(_finite_number(node.get(field, "0")) is not None for field in fields)


def _paint_contains(paint, text_box):
    """Require the complete measured label box inside the actual filled shape."""
    box, node = paint["box"], paint["node"]
    if not _contains(box, text_box):
        return False
    corners = [(x, y) for x in (text_box["x"], text_box["x"] + text_box["width"])
               for y in (text_box["y"], text_box["y"] + text_box["height"])]
    if paint["item"]["tag"] in {"circle", "ellipse"}:
        rx, ry = box["width"] / 2, box["height"] / 2
        cx, cy = box["x"] + rx, box["y"] + ry
        return rx > 0 and ry > 0 and all(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1
                                       for x, y in corners)
    if node.get("rx") is None and node.get("ry") is None:
        return True
    width, height = _finite_number(node.get("width")), _finite_number(node.get("height"))
    rx = _finite_number(node.get("rx", node.get("ry")))
    ry = _finite_number(node.get("ry", node.get("rx")))
    if any(value is None for value in (width, height, rx, ry)) or width <= 0 or height <= 0 or rx < 0 or ry < 0:
        return False
    rx, ry = min(rx, width / 2) * box["width"] / width, min(ry, height / 2) * box["height"] / height
    if not rx or not ry:
        return True
    left, right = box["x"] + rx, box["x"] + box["width"] - rx
    top, bottom = box["y"] + ry, box["y"] + box["height"] - ry
    return all(left <= x <= right or top <= y <= bottom or
               ((x - (left if x < left else right)) / rx) ** 2
               + ((y - (top if y < top else bottom)) / ry) ** 2 <= 1 for x, y in corners)


def _visible(item):
    occluded_fraction = _finite_number(item.get("occluded_fraction", 0))
    occlusion_unknown_fraction = _finite_number(item.get("occlusion_unknown_fraction", 0))
    if occluded_fraction is None or occlusion_unknown_fraction is None:
        return False
    return (item.get("css_visible") is True and item.get("effective_opacity", 0) >= .9
            and _ink_rect(item.get("bbox")) is not None
            and item.get("viewport_intersection_ratio", 1) >= .99
            and not item.get("clipped") and not item.get("glyph_clipped")
            and occluded_fraction <= .05
            and occlusion_unknown_fraction <= .05)


def _paint(node):
    # Direct solid source paint only. No inference from inherited/CSS paint,
    # palette families, images, strokes or an arbitrary neighboring shape.
    if node.get("style"):
        return None
    value = node.get("fill", "")
    if value in {"black", "white"}:
        value = {"black": "#000000", "white": "#FFFFFF"}[value]
    if not re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value):
        return None
    try:
        if float(node.get("fill-opacity", "1")) < .9:
            return None
    except ValueError:
        return None
    if len(value) == 4:
        value = "#" + "".join(char * 2 for char in value[1:])
    return value.upper()


def _tokens(value, names):
    """Require the entire visible text to consist of exact source member names."""
    value = _normalized(value)
    memo = {}
    def split(rest):
        if not rest:
            return [()]
        if rest in memo:
            return memo[rest]
        found = []
        for name in names:
            if rest.startswith(name):
                found.extend((name, *tail) for tail in split(rest[len(name):]))
                if len(found) > 1:
                    break
        memo[rest] = found[:2]
        return memo[rest]
    result = split(value)
    return set(result[0]) if len(result) == 1 and result[0] else None


def _records(svg, elements):
    nodes = {f"svg-{index}": node for index, node in enumerate(ET.fromstring(svg).iter())}
    source_parents = {child: parent for parent in nodes.values() for child in parent}
    direct_paint_reliable = not any(node.tag.rsplit("}", 1)[-1] == "style" for node in nodes.values())
    by_qid = {item.get("qid"): item for item in elements}
    texts, paints = [], []
    def owns(member, ancestor):
        seen = set()
        while member and member.get("qid") not in seen:
            if member.get("qid") == ancestor:
                return True
            seen.add(member.get("qid"))
            member = by_qid.get(member.get("parent_qid"))
        return False

    def parent(item):
        key = item.get("parent_qid")
        while key in by_qid and by_qid[key].get("tag") in {"text", "tspan"}:
            key = by_qid[key].get("parent_qid")
        return key

    for item in elements:
        node = nodes.get(item.get("qid"))
        if node is None or node.tag.rsplit("}", 1)[-1] != item.get("tag") or not _visible(item):
            continue
        box = _ink_rect(item["bbox"])
        if item.get("tag") == "text":
            members = [member for member in elements if member.get("has_owned_text")
                       and str(member.get("owned_text") or "").strip() and owns(member, item["qid"])]
            if members and all(_visible(member) and any(_ink_rect(fragment)
                    for fragment in member.get("text_ink_fragments", [])) for member in members):
                texts.append({"item": item, "box": box,
                    "text": item.get("rendered_text") or item.get("owned_text"), "node": node,
                    "color": _paint(node) if direct_paint_reliable else None, "parent": parent(item)})
        # HTML/CSS paint ownership does not describe SVG fill. Canonical solid
        # source paint and measured visibility/axes are the SVG evidence.
        if item.get("tag") in {"rect", "circle", "ellipse"}:
            color = (_paint(node) if direct_paint_reliable and _axis_aligned(node, source_parents)
                     and _measured_axes(item) and _finite_geometry(node, item["tag"]) else None)
            if color:
                paints.append({"item": item, "box": box, "color": color, "node": node, "parent": parent(item)})
    return texts, paints, by_qid


def _same_parent(first, second):
    return first["parent"] == second["parent"]


def _near_key(paint, header):
    if not _same_parent(paint, header):
        return False
    box, text = paint["box"], header["box"]
    if _paint_contains(paint, text):
        return True
    height = text["height"]
    if max(box["width"], box["height"]) > height * 2:
        return False
    gap = max(text["x"] - box["x"] - box["width"], box["x"] - text["x"] - text["width"], 0)
    return gap <= height and abs(box["y"] + box["height"] / 2
        - text["y"] - height / 2) <= max(box["height"], height) / 2


def _legend_proof(groups, texts, paints, names):
    """Require explicit, coherent legend keys and uniquely colored member nodes."""
    headers = [[text for text in texts if _normalized(text["text"]) == _normalized(group["label"])]
               for group in groups]
    if not all(headers) or math.prod(map(len, headers)) > 32:
        return None
    members = {name: [] for name in names}
    for text in texts:
        name = _normalized(text["text"])
        if name not in members:
            continue
        enclosures = [paint for paint in paints if _paint_contains(paint, text["box"])
                      and _same_parent(paint, text)]
        if not enclosures:
            return None
        area = min(paint["box"]["width"] * paint["box"]["height"] for paint in enclosures)
        smallest = [paint for paint in enclosures
                    if abs(paint["box"]["width"] * paint["box"]["height"] - area) < .01]
        if len(smallest) != 1:
            return None
        members[name].append(smallest[0])
    if not all(members.values()):
        return None
    palette = {paint["color"] for entries in members.values() for paint in entries}
    proofs = []
    for chosen in itertools.product(*headers):
        if len({header["item"].get("parent_qid") for header in chosen}) != 1:
            continue
        boxes = [header["box"] for header in chosen]
        vertical_span = max(box["y"] + box["height"] for box in boxes) - min(box["y"] for box in boxes)
        horizontal_span = max(box["x"] + box["width"] for box in boxes) - min(box["x"] for box in boxes)
        if vertical_span > sum(box["height"] for box in boxes) * 3 and horizontal_span > sum(box["width"] for box in boxes) * 3:
            continue
        colors, witness = [], []
        for group, header in zip(groups, chosen, strict=True):
            expected = {_normalized(member) for member in group["members"]}
            node_colors = {paint["color"] for name in expected for paint in members[name]}
            if len(node_colors) != 1:
                break
            color = next(iter(node_colors))
            key_colors = {
                paint["color"] for paint in paints if paint["color"] in palette and _near_key(paint, header)}
            # A contrasting caption color does not override its visible painted key.
            if not key_colors and header["color"] in palette:
                key_colors = {header["color"]}
            if key_colors != {color}:
                break
            colors.append(color)
            witness.append({"label": group["label"], "color": color,
                "legend_qid": header["item"]["qid"], "member_qids": [paint["item"]["qid"]
                    for name in expected for paint in members[name]]})
        else:
            if len(set(colors)) == len(groups):
                proofs.append(witness)
    return proofs[0] if len(proofs) == 1 else None


def apply_source_classification_association(quality, svg, source):
    groups = source_classifications(source)
    receipt = {"version": GUARD_VERSION, "status": "not_applicable", "checked_count": len(groups),
               "missing_count": 0, "witnesses": []}
    quality["source_classification_association"] = receipt
    if not groups:
        return
    receipt["status"] = "unproven"
    elements = (quality.get("browser_metrics") or {}).get("elements") or []
    missing = list(groups)
    canonical = hashlib.sha256(svg.encode()).hexdigest()
    if (quality.get("measurement_complete") is True and elements
            and str(quality.get("source_digest", "")).removeprefix("sha256:") == canonical):
        texts, paints, _ = _records(svg, elements)
        # Reuse the existing exact-list predicate, but only on measured visible
        # text. This preserves existing list passes without trusting hidden SVG.
        from services.generation_core.authoring.author_slide_svg import source_classification_coverage
        visible_qids = {text["item"]["qid"] for text in texts}
        measured_root = ET.fromstring(svg)
        for index, node in enumerate(measured_root.iter()):
            if node.tag.rsplit("}", 1)[-1] == "text" and f"svg-{index}" not in visible_qids:
                node.set("opacity", "0")
        list_coverage = source_classification_coverage(source, measured_root,
            allow_associated_blocks=True, allow_split_list_blocks=True)
        if list_coverage["checked_count"] == len(groups) and not list_coverage["missing_count"]:
            receipt.update(status="proven", witnesses=[{"basis": "measured_explicit_lists",
                "text_qids": sorted(visible_qids)}])
            return
        names = {_normalized(member) for group in groups for member in group["members"]}
        labels = {_normalized(group["label"]) for group in groups}
        for group in groups:
            expected = {_normalized(member) for member in group["members"]}
            # Explicit inline lists preserve semantic membership as a set.
            inline = []
            for text in texts:
                match = re.fullmatch(r"\s*([^:：]+)[:：]\s*(.+)", text["text"])
                if match and _normalized(match[1]) == _normalized(group["label"]) and _tokens(match[2], names) == expected:
                    inline.append(text["item"]["qid"])
            if inline:
                missing.remove(group)
                receipt["witnesses"].append({"label": group["label"], "basis": "visible_explicit_list", "qids": inline})
                continue
            candidates = []
            for paint in paints:
                if paint["item"].get("tag") != "rect":
                    continue
                inside = [text for text in texts if _paint_contains(paint, text["box"])]
                headers = [text for text in inside if _normalized(text["text"]) in labels]
                if len(headers) != 1 or _normalized(headers[0]["text"]) != _normalized(group["label"]):
                    continue
                owned = [(text, _tokens(text["text"], names)) for text in inside]
                covered = set().union(*(members for _, members in owned if members))
                if covered == expected:
                    candidates.append({"label": group["label"], "basis": "visible_class_enclosure",
                        "enclosure_qid": paint["item"]["qid"], "header_qid": headers[0]["item"]["qid"],
                        "member_qids": [text["item"]["qid"] for text, members in owned if members]})
            if len(candidates) == 1:
                missing.remove(group)
                receipt["witnesses"].append(candidates[0])
        if missing:
            legend = _legend_proof(groups, texts, paints, names)
            if legend is not None:
                missing = []
                receipt["witnesses"] = [{**witness, "basis": "explicit_color_legend"} for witness in legend]
    receipt["missing_count"] = len(missing)
    if not missing:
        receipt["status"] = "proven"
        return
    receipt["unproven_labels"] = [group["label"] for group in missing]
    quality["passed"] = quality["verified"] = False
    quality.setdefault("failures", []).append({"kind": "source_classification_unproven",
        "labels": receipt["unproven_labels"]})
