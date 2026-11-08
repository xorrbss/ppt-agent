"""Non-rendered SVG sizing evidence, deliberately separate from quality validation.

Text boxes are 1.2-em-per-character proxies, not measured glyph ink or advance.
Their envelopes describe layout extent, not painted area or visual whitespace.
Unknown paint/geometry makes the affected evidence unavailable rather than empty.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET


CONTENT_SIZING_POLICY_REVISION = "svg-content-sizing-v1"
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:px)?\Z")
_CARD_ID = re.compile(r"(?:^|[-_])card(?:[-_]|$)", re.I)
_SKIP = {"defs", "title", "desc", "metadata"}
_STYLE = {"fill", "stroke", "stroke-width", "font-size", "font-family", "font-weight",
          "font-style", "text-anchor", "opacity", "fill-opacity", "stroke-opacity",
          "display", "visibility", "stroke-linecap", "stroke-linejoin"}


def _local(node):
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def _number(value):
    value = str(value).strip() if value is not None else ""
    if not _NUMBER.fullmatch(value):
        return None
    number = float(value.removesuffix("px"))
    return number if math.isfinite(number) else None


def _bounds(value):
    try:
        if isinstance(value, dict):
            value = [value[key] for key in ("x", "y", "width", "height")]
        x, y, width, height = map(_number, value)
        if None in (x, y, width, height) or width <= 0 or height <= 0:
            return None
        area = width * height
        if not all(math.isfinite(number) for number in (x + width, y + height, area)) or area <= 0:
            return None
        return (x, y, width, height)
    except (TypeError, ValueError, KeyError):
        return None


def _rect(node):
    return _bounds((node.get("x", "0"), node.get("y", "0"), node.get("width"), node.get("height")))


_PATH_TOKEN = re.compile(r"[A-Za-z]|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_POINTS_SPLIT = re.compile(r"[\s,]+")


def _points_box(coordinates):
    if len(coordinates) < 2 or len(coordinates) % 2:
        return None
    xs, ys = coordinates[0::2], coordinates[1::2]
    # A zero-width or zero-height stroke (a straight rule) still occupies its line.
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    return _bounds((min(xs), min(ys), max(width, 1e-6), max(height, 1e-6)))


def _shape_box(node, tag):
    """A conservative bounding box for simple shapes, or None when it cannot be bounded."""
    try:
        if tag == "line":
            values = [_number(node.get(key, "0")) for key in ("x1", "y1", "x2", "y2")]
            return None if None in values else _points_box(values)
        if tag in {"circle", "ellipse"}:
            cx, cy = _number(node.get("cx", "0")), _number(node.get("cy", "0"))
            rx = _number(node.get("r") if tag == "circle" else node.get("rx"))
            ry = rx if tag == "circle" else _number(node.get("ry"))
            if None in (cx, cy, rx, ry) or rx <= 0 or ry <= 0:
                return None
            return _bounds((cx - rx, cy - ry, 2 * rx, 2 * ry))
        if tag in {"polygon", "polyline"}:
            values = [_number(token) for token in _POINTS_SPLIT.split(node.get("points", "").strip()) if token]
            return None if None in values else _points_box(values)
        if tag == "path":
            # Absolute commands only: every vertex and Bezier control point is an
            # explicit coordinate, and a Bezier curve lies within its control hull.
            # Relative commands and arcs are not bounded here.
            tokens = _PATH_TOKEN.findall(node.get("d", ""))
            if not tokens or any(token.isalpha() and token not in "MLHVCSQTZ" for token in tokens):
                return None
            xs, ys, command, pending = [], [], None, []
            for token in tokens:
                if token.isalpha():
                    if pending:
                        return None
                    command = token
                    continue
                value = _number(token)
                if value is None or command is None:
                    return None
                if command == "H":
                    xs.append(value)
                elif command == "V":
                    ys.append(value)
                else:
                    pending.append(value)
                    if len(pending) == 2:
                        xs.append(pending[0])
                        ys.append(pending[1])
                        pending = []
            if pending or not xs or not ys:
                return None
            return _points_box([coordinate for pair in zip(
                [min(xs), max(xs)], [min(ys), max(ys)], strict=True) for coordinate in pair])
    except (TypeError, ValueError):
        return None
    return None


def _area(box):
    return box[2] * box[3]


def _ratio(numerator, denominator):
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator <= 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _inside(inner, outer):
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[0] + inner[2] <= outer[0] + outer[2]
            and inner[1] + inner[3] <= outer[1] + outer[3])


def _intersects(a, b):
    return (min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0])
            and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1]))


def _envelope(boxes):
    if not boxes:
        return None
    x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return _bounds((x, y, max(b[0] + b[2] for b in boxes) - x,
                    max(b[1] + b[3] for b in boxes) - y))


def _marked(node):
    return node.get("data-role") == "card" or bool(_CARD_ID.search(node.get("id", "")))


def _attributes(node, inherited):
    attrs = dict(inherited)
    attrs.update({key: value for key, value in node.attrib.items() if key in _STYLE})
    for declaration in node.get("style", "").split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            return attrs, "unsupported_style"
        key, value = (part.strip() for part in declaration.split(":", 1))
        if key not in _STYLE or "!important" in value or "var(" in value:
            return attrs, "unsupported_style"
        attrs[key] = value
    if any(key in node.attrib for key in ("transform", "clip-path", "mask", "filter")):
        return attrs, "unsupported_effect_or_transform"
    if any(key in node.attrib for key in ("letter-spacing", "word-spacing", "writing-mode", "direction",
                                          "baseline-shift", "dominant-baseline", "alignment-baseline")):
        return attrs, "unsupported_text_layout"
    if attrs.get("visibility") in {"hidden", "collapse"}:
        return attrs, "visibility_not_resolved"
    if any("url(" in value for value in attrs.values()):
        return attrs, "unsupported_paint_reference"
    for paint in ("fill", "stroke"):
        value = attrs.get(paint, "")
        if "rgba(" in value or "hsla(" in value or "/" in value or (value.startswith("#") and len(value) in {5, 9}):
            return attrs, "unsupported_alpha_color"
    if any(_opacity(attrs[key]) is None for key in ("opacity", "fill-opacity", "stroke-opacity") if key in attrs):
        return attrs, "unsupported_opacity"
    return attrs, None


def _hidden(attrs):
    return attrs.get("display") == "none" or attrs.get("visibility") in {"hidden", "collapse"} or _opacity(attrs.get("opacity", "1")) == 0


def _opacity(value):
    value = str(value).strip()
    if value.endswith("px"):
        return None
    number = _number(value[:-1]) if value.endswith("%") else _number(value)
    return max(0, min(1, number / 100 if value.endswith("%") else number)) if number is not None else None


def _paintless(attrs):
    fill = attrs.get("fill", "black").strip().lower() in {"none", "transparent"} or _opacity(attrs.get("fill-opacity", "1")) == 0
    stroke = (attrs.get("stroke", "none").strip().lower() in {"none", "transparent"} or _opacity(attrs.get("stroke-opacity", "1")) == 0
              or _number(attrs.get("stroke-width", "1")) == 0)
    return fill and stroke


def _text_boxes(node, inherited):
    """Return proxy fragments, rejecting positioning or typography we cannot explain."""
    boxes = []
    cursor = [None, None]
    failure = None

    def visit(part, parent_attrs):
        nonlocal failure
        attrs, reason = _attributes(part, parent_attrs)
        if reason:
            failure = reason
            return
        if _hidden(attrs):
            if part is not node or len(part):
                failure = "structured_text_paint_not_resolved"
            return
        if _paintless(attrs) and (part is not node or len(part)):
            failure = "structured_text_paint_not_resolved"
            return
        if any(key in part.attrib for key in ("rotate", "textLength", "lengthAdjust", "baseline-shift",
                                              "dominant-baseline", "alignment-baseline", "letter-spacing",
                                              "word-spacing", "writing-mode", "direction")):
            failure = "unsupported_text_layout"
            return
        for index, axis in enumerate(("x", "y")):
            if axis in part.attrib:
                cursor[index] = _number(part.get(axis))
            shift = _number(part.get("d" + axis, "0"))
            if cursor[index] is None or shift is None:
                failure = "missing_or_unsupported_text_position"
                return
            cursor[index] += shift
            if not math.isfinite(cursor[index]):
                failure = "invalid_text_proxy_bounds"
                return
        size = _number(attrs.get("font-size", "16"))
        if size is None or size <= 0:
            failure = "unsupported_font_size"
            return
        anchor = attrs.get("text-anchor", "start")
        if anchor not in {"start", "middle", "end"} or (len(part) and anchor != "start"):
            failure = "unsupported_text_anchor"
            return

        def fragment(text):
            if not text or not text.strip() or _paintless(attrs):
                return
            if "\n" in text or "\t" in text:
                failure_nonlocal("unsupported_text_whitespace")
                return
            width = len(text) * size * 1.2
            offset = {"start": 0, "middle": width / 2, "end": width}[anchor]
            box = _bounds((cursor[0] - offset, cursor[1] - size, width, size * 1.3))
            if box is None or not math.isfinite(cursor[0] + width):
                failure_nonlocal("invalid_text_proxy_bounds")
                return
            boxes.append(box)
            cursor[0] += width

        fragment(part.text)
        for child in part:
            if _local(child) != "tspan":
                failure = "unsupported_text_child"
                return
            visit(child, attrs)
            fragment(child.tail)

    def failure_nonlocal(reason):
        nonlocal failure
        failure = reason

    visit(node, inherited)
    return boxes, failure


def svg_content_sizing(svg: str, *, body_bounds=None) -> dict:
    """Return JSON-safe, revisioned card and page/body proxy evidence.

    ``body_bounds`` is optional (x, y, width, height), or a dictionary with those
    keys, in SVG user coordinates. Without it the page is the declared context;
    the body's occupied envelope is never substituted for its available space.
    Explicit card IDs/data-role and groups with one backing rectangle and at
    least two contained text nodes are candidates. Unmarked empty rectangles
    are never assumed to be cards. No scalar is a quality/density score.
    """
    result = {"policy_revision": CONTENT_SIZING_POLICY_REVISION,
              "status": "unavailable", "reasons": [], "cards": [],
              "candidate_count": 0, "available_card_count": 0,
              "card_unused_envelope_share": None,
              "context": {"page_bounds": None, "body_bounds": None,
                          "body_bounds_source": None, "content_envelope_bounds": None,
                          "content_envelope_share": None, "card_footprint_share": None},
              "limitations": ["Text geometry is a 1.2-em character proxy, not glyph ink or font measurement.",
                              "Envelope slack includes gaps between content and is not visual whitespace.",
                              "Card footprint and body/page context must be compared together; no density score is inferred."]}
    try:
        root = ET.fromstring(svg)
    except (ET.ParseError, TypeError):
        result["reasons"] = ["invalid_svg"]
        return result
    if _local(root) != "svg":
        result["reasons"] = ["not_svg"]
        return result
    viewbox = root.get("viewBox", "").replace(",", " ").split()
    page = _bounds(viewbox) if viewbox else _bounds((0, 0, root.get("width"), root.get("height")))
    body = _bounds(body_bounds) if body_bounds is not None else page
    result["context"].update(page_bounds=page, body_bounds=body,
                             body_bounds_source="explicit" if body_bounds is not None else "page_fallback")
    if page is None or body is None or not _inside(body, page):
        result["reasons"] = ["missing_or_invalid_page_or_body_bounds"]
        return result
    if any(_local(n) == "style" for n in root.iter()):
        result["reasons"] = ["stylesheet_not_resolved"]
        return result

    records, parents, errors = {}, {}, []

    def walk(node, inherited, ancestor_error=None):
        tag, node_id = _local(node), node.get("id", "")
        if tag in _SKIP or (node_id.startswith("svg-system-template-") and node_id != "svg-system-template-body"):
            return
        attrs, error = _attributes(node, inherited)
        error = ancestor_error or error
        if tag == "svg" and node is not root:
            error = error or "nested_svg_viewport_unsupported"
        if _hidden(attrs) and not error:
            return
        if tag not in {"svg", "g", "image", "text"} and _paintless(attrs) and not error:
            return
        boxes = []
        if tag == "text":
            boxes, text_error = _text_boxes(node, inherited)
            error = error or text_error
        elif tag in {"rect", "image"}:
            box = _rect(node)
            if box:
                boxes = [box]
            else:
                error = error or "missing_or_invalid_shape_bounds"
        elif tag in {"line", "circle", "ellipse", "polygon", "polyline", "path"}:
            box = _shape_box(node, tag)
            if box:
                boxes = [box]
            else:
                error = error or "unsupported_geometry"
        elif tag not in {"svg", "g"}:
            error = error or "unsupported_geometry"
        records[node] = {"tag": tag, "attrs": attrs, "boxes": boxes, "error": error}
        if error:
            errors.append(error)
        if tag == "text":
            return
        for child in node:
            parents[child] = node
            walk(child, attrs, error)

    walk(root, {})
    candidates = []
    claimed = set()
    for node, record in records.items():
        if record["tag"] != "g":
            continue
        children = [child for child in node if child in records]
        rects = [child for child in children if records[child]["tag"] == "rect"]
        texts = [child for child in node.iter() if child in records and records[child]["tag"] == "text"]
        if len(rects) == 1 and (_marked(node) or (len(texts) >= 2 and children[0] is rects[0])):
            rect = rects[0]
            if not _marked(node) and (_marked(rect) or not records[rect]["boxes"]):
                continue
            box = records[rect]["boxes"][0] if records[rect]["boxes"] else None
            if not _marked(node) and (box[2] < 80 or box[3] < 40 or _area(box) >= _area(body) * 0.85):
                continue
            if not _marked(node) and sum(
                    bool(records[text]["boxes"]) and not records[text]["error"]
                    and all(_inside(text_box, box) for text_box in records[text]["boxes"])
                    for text in texts) < 2:
                continue
            candidates.append((node, rect, "explicit_group" if _marked(node) else "group_backing_rect"))
            claimed.add(rect)
    for node, record in records.items():
        if record["tag"] == "rect" and _marked(node) and node not in claimed:
            candidates.append((parents.get(node, root), node, "explicit_rect"))

    for scope, rect, evidence in candidates:
        record = records[rect]
        box = record["boxes"][0] if record["boxes"] else None
        card = {"id": rect.get("id") or scope.get("id") or None,
                "candidate_evidence": evidence, "bounds": box, "status": "unavailable",
                "reasons": [], "content_count": 0, "content_envelope_bounds": None,
                "unused_envelope_share": None}
        # A card is judged on its own subtree and on geometry that can reach it;
        # an unrelated page-level limitation does not make every card unavailable.
        reasons, content = [], []
        stroke = _number(record["attrs"].get("stroke-width", "1")) if record["attrs"].get("stroke", "none") != "none" else 0
        if box is None or stroke is None or stroke < 0:
            reasons.append("unsupported_card_geometry")
            interior = None
        else:
            interior = _bounds((box[0] + stroke / 2, box[1] + stroke / 2, box[2] - stroke, box[3] - stroke))
            if interior is None or not _inside(box, body):
                reasons.append("card_outside_body_or_invalid_interior")
        card["interior_bounds"] = interior
        if any(_number(rect.get(radius, "0")) != 0 for radius in ("rx", "ry")):
            reasons.append("rounded_card_geometry_unsupported")
        if record["attrs"].get("fill", "black") == "none" and record["attrs"].get("stroke", "none") == "none":
            reasons.append("unpainted_card")
        owned = set(scope.iter())
        if interior and any(member not in owned and any(_intersects(b, interior) for b in item["boxes"])
                            for member, item in records.items()):
            reasons.append("external_geometry_overlaps_card")
        if any(member not in owned and item["error"] and not item["boxes"] and item["tag"] not in {"svg", "g"}
               for member, item in records.items()):
            # Geometry that could not be bounded might cover the card.
            reasons.append("unbounded_external_geometry")
        for member in scope.iter():
            if member not in records:
                continue
            item = records[member]
            if item["error"]:
                reasons.append(item["error"])
            if member is rect or item["tag"] in {"svg", "g"}:
                continue
            if item["tag"] == "rect":
                if not item["boxes"] or (interior and _intersects(item["boxes"][0], interior)):
                    reasons.append("ambiguous_additional_rectangle")
                continue
            for content_box in item["boxes"]:
                if interior and _inside(content_box, interior):
                    content.append(content_box)
                elif interior and _intersects(content_box, interior):
                    reasons.append("content_crosses_card_boundary")
                elif interior and evidence != "explicit_rect":
                    reasons.append("owned_content_outside_card")
        if evidence == "group_backing_rect" and not content:
            reasons.append("no_contained_text_proxy")
        card["content_count"] = len(content)
        card["content_envelope_bounds"] = _envelope(content)
        if content and card["content_envelope_bounds"] is None:
            reasons.append("invalid_content_envelope_bounds")
        card["reasons"] = sorted(set(reasons))
        if not reasons:
            occupied_share = _ratio(_area(card["content_envelope_bounds"]), _area(interior)) if content else 0
            if occupied_share is None:
                card["reasons"] = ["invalid_content_ratio"]
            else:
                card["status"] = "estimated"
                card["unused_envelope_share"] = 1 - occupied_share
        result["cards"].append(card)

    for index, card in enumerate(result["cards"]):
        if card["bounds"] is None:
            continue
        for other in result["cards"][index + 1:]:
            if other["bounds"] and _intersects(card["bounds"], other["bounds"]):
                for ambiguous in (card, other):
                    ambiguous["status"] = "unavailable"
                    ambiguous["unused_envelope_share"] = None
                    ambiguous["reasons"] = sorted(set(ambiguous["reasons"] + ["overlapping_or_nested_cards"]))

    content_boxes = [box for record in records.values() if record["tag"] in {"text", "image"}
                     for box in record["boxes"]]
    if any(not _inside(box, body) for box in content_boxes):
        errors.append("content_outside_body")
    unclassified_rects = [node for node, record in records.items()
                          if record["tag"] == "rect" and not any(node is candidate[1] for candidate in candidates)]
    if unclassified_rects:
        errors.append("unclassified_rectangles_excluded")
    if not errors:
        envelope = _envelope(content_boxes)
        if content_boxes and envelope is None:
            errors.append("invalid_content_envelope_bounds")
        else:
            result["context"]["content_envelope_bounds"] = envelope
            result["context"]["content_envelope_share"] = _ratio(_area(envelope), _area(body)) if envelope else 0
            if result["context"]["content_envelope_share"] is None:
                errors.append("invalid_context_ratio")
    usable = [card for card in result["cards"] if card["status"] == "estimated"]
    result.update(candidate_count=len(candidates), available_card_count=len(usable))
    if usable and len(usable) == len(candidates):
        total_area = sum(_area(card["interior_bounds"]) for card in usable)
        result["card_unused_envelope_share"] = _ratio(sum(card["unused_envelope_share"] * _area(card["interior_bounds"]) for card in usable), total_area)
        if result["card_unused_envelope_share"] is None:
            errors.append("invalid_aggregate_ratio")
    if len(usable) == len(candidates):
        result["context"]["card_footprint_share"] = _ratio(sum(_area(card["bounds"]) for card in usable), _area(body))
        if result["context"]["card_footprint_share"] is None:
            errors.append("invalid_aggregate_ratio")
    result["reasons"] = sorted(set(errors + [reason for card in result["cards"] for reason in card["reasons"]]))
    result["status"] = "partial" if result["reasons"] and (usable or not errors) else "unavailable" if result["reasons"] else "estimated"
    if not candidates:
        result["reasons"] = sorted(set(result["reasons"] + ["no_defensible_card_candidates"]))
        result["status"] = "unavailable"
    return result
