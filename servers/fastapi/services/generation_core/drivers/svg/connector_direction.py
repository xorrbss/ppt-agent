"""Arrowheads that point backwards along a drawn sequence (opt-in with the connector policy).

Measured 2026-11-04 with the content structure policy's connectors on: the model hand-writes
each arrowhead as a triangle and put the apex on the wrong side on about 1 flow page in 9 (a
five-stage row whose four triangles all pointed left, a four-layer stack whose three triangles
all pointed up while the source said each layer calls the one below). A direction sentence in
the author rules did not help (1 -> 1): it is a coordinate slip, not a wrong reading.

A page fails ``connector_arrowheads_reversed`` only when all of this holds, so a deliberate
return arrow or a flow the source states the other way passes:

* an **arrowhead** is a small (3-48 px) triangle, a ``polygon`` or a ``M L L Z`` path, with one
  side horizontal or vertical (its base) and the opposite corner (its apex) at least 3 px off
  it; the apex gives the direction;
* it sits in the **gap** between two boxes (painted rectangles or polygons of at least 30 x 20)
  that both span the arrowhead's centre line, at most 120 px apart, along its direction;
* the arrowhead points to the box that comes **first in reading order** (left of, or above, the
  other) **and** that box's text comes first in the page's source content too, so the source
  does not state the sequence the other way (a bottom-up stack listed from the bottom passes);
* at least **two** such arrowheads lie on one line (a row or a column), and **every** arrowhead
  on that line between two boxes points backwards. A single backward arrowhead (a loop back to
  an earlier step, a cycle's closing edge) is never flagged, and a line with any forward
  arrowhead is a two-way drawing, not a slip.

Server furniture (``svg-system-*`` other than a template page's body group) is not read. The check adds a failure; it never edits the
page. Kept on all 250 stored pages of the template decks measured on 2026-11-04 it flagged the
known reversed row and stack and one more reversed row (a five-activity chain), nothing else.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

FAILURE_KIND = "connector_arrowheads_reversed"
#: The composed template page's body group (services/svg_templates/frame_composition.BODY_ID).
TEMPLATE_BODY_ID = "svg-system-template-body"
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TRIANGLE_PATH = re.compile(r"\s*M[^MLHVZmlhvz]*L[^MLHVZmlhvz]*L[^MLHVZmlhvz]*(?:Z\s*)?", re.I)
MAX_ARROWHEAD_PX = 48.0
MIN_ARROWHEAD_PX = 3.0
MIN_BOX_WIDTH, MIN_BOX_HEIGHT = 30.0, 20.0
MAX_GAP_PX = 120.0
LINE_TOLERANCE_PX = 8.0


def _numbers(value) -> list[float]:
    return [float(item) for item in _NUMBER.findall(value or "")]


def _tag(node) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _walk(node, dx=0.0, dy=0.0):
    node_id = node.get("id") or ""
    if node_id.startswith("svg-system-") and node_id != TEMPLATE_BODY_ID:
        return
    transform = node.get("transform") or ""
    if transform.startswith("translate"):
        values = _numbers(transform)
        dx += values[0] if values else 0.0
        dy += values[1] if len(values) > 1 else 0.0
    yield node, dx, dy
    for child in node:
        yield from _walk(child, dx, dy)


def _painted(node) -> bool:
    return node.get("fill", "black") != "none" or node.get("stroke", "none") != "none"


def _points(node) -> list[tuple[float, float]] | None:
    tag = _tag(node)
    if tag == "polygon":
        values = _numbers(node.get("points"))
    elif tag == "path" and "m" not in (node.get("d") or "") and "l" not in (node.get("d") or ""):
        values = _numbers(node.get("d"))
    else:
        return None
    return list(zip(values[0::2], values[1::2], strict=False))


def _arrowhead(node, dx: float, dy: float) -> dict | None:
    if _tag(node) == "path" and not _TRIANGLE_PATH.fullmatch(node.get("d") or ""):
        return None
    points = _points(node)
    if not points or not _painted(node):
        return None
    if len(points) == 4 and points[0] == points[-1]:
        points = points[:3]
    if len(points) != 3:
        return None
    points = [(x + dx, y + dy) for x, y in points]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if max(width, height) > MAX_ARROWHEAD_PX or min(width, height) < MIN_ARROWHEAD_PX:
        return None
    for i in range(3):
        a, b, apex = points[i], points[(i + 1) % 3], points[(i + 2) % 3]
        for axis, along in (("horizontal", 0), ("vertical", 1)):
            # A horizontal arrow has a vertical base (its two corners share x).
            if abs(a[along] - b[along]) <= 1 and abs(apex[along] - (a[along] + b[along]) / 2) >= MIN_ARROWHEAD_PX:
                return {"axis": axis, "direction": 1 if apex[along] > a[along] else -1,
                        "box": (min(xs), min(ys), max(xs), max(ys)), "points": node.get("points") or node.get("d"),
                        "id": node.get("id")}
    return None


def _boxes(root) -> list[tuple[float, float, float, float]]:
    boxes = []
    for node, dx, dy in _walk(root):
        tag = _tag(node)
        if not _painted(node):
            continue
        if tag == "rect":
            try:
                x, y, w, h = (float(node.get(key, 0)) for key in ("x", "y", "width", "height"))
            except ValueError:
                continue
            if w >= MIN_BOX_WIDTH and h >= MIN_BOX_HEIGHT:
                boxes.append((x + dx, y + dy, x + dx + w, y + dy + h))
        elif tag in {"polygon", "path"}:
            points = _points(node)
            if points and len(points) >= 4:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                if max(xs) - min(xs) >= MIN_BOX_WIDTH and max(ys) - min(ys) >= MIN_BOX_HEIGHT:
                    boxes.append((min(xs) + dx, min(ys) + dy, max(xs) + dx, max(ys) + dy))
    return boxes


def _texts(root) -> list[tuple[float, float, str]]:
    texts = []
    for node, dx, dy in _walk(root):
        if _tag(node) != "text":
            continue
        value = "".join("".join(node.itertext()).split())
        try:
            x, y = float(node.get("x", "nan")), float(node.get("y", "nan"))
        except ValueError:
            continue
        if value and x == x and y == y:
            texts.append((x + dx, y + dy, value))
    return texts


def _neighbours(head: dict, boxes) -> tuple[tuple, tuple] | None:
    x0, y0, x1, y1 = head["box"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if head["axis"] == "horizontal":
        tolerance = max(4.0, (x1 - x0) / 2)
        band = [b for b in boxes if b[1] <= cy <= b[3] and not b[0] < cx < b[2]]
        before = [b for b in band if b[2] <= x0 + tolerance]
        after = [b for b in band if b[0] >= x1 - tolerance]
        if not before or not after:
            return None
        first, second = max(before, key=lambda b: b[2]), min(after, key=lambda b: b[0])
        return (first, second) if second[0] - first[2] <= MAX_GAP_PX else None
    tolerance = max(4.0, (y1 - y0) / 2)
    band = [b for b in boxes if b[0] <= cx <= b[2] and not b[1] < cy < b[3]]
    before = [b for b in band if b[3] <= y0 + tolerance]
    after = [b for b in band if b[1] >= y1 - tolerance]
    if not before or not after:
        return None
    first, second = max(before, key=lambda b: b[3]), min(after, key=lambda b: b[1])
    return (first, second) if second[1] - first[3] <= MAX_GAP_PX else None


def _box_text(box, texts) -> list[str]:
    return [value for x, y, value in texts if box[0] <= x <= box[2] and box[1] <= y <= box[3]]


def _source_position(values: list[str], source: str) -> int | None:
    found = [source.find(value) for value in values if len(value) >= 2]
    found = [position for position in found if position >= 0]
    return min(found) if found else None


def reversed_arrowhead_chains(svg: str, source: str) -> list[dict]:
    """Every line of arrowheads that all point back along a sequence (see the module docstring)."""
    root = ET.fromstring(svg)
    heads = [head for node, dx, dy in _walk(root) if (head := _arrowhead(node, dx, dy))]
    if len(heads) < 2:
        return []
    boxes, texts = _boxes(root), _texts(root)
    normalized_source = "".join(str(source or "").split())
    placed = []
    for head in heads:
        pair = _neighbours(head, boxes)
        if pair is None:
            continue
        first, second = (_box_text(box, texts) for box in pair)
        positions = (_source_position(first, normalized_source), _source_position(second, normalized_source))
        source_agrees = None not in positions and positions[0] < positions[1]
        x0, y0, x1, y1 = head["box"]
        line = (y0 + y1) / 2 if head["axis"] == "horizontal" else (x0 + x1) / 2
        placed.append({**head, "line": line, "pair": pair, "source_agrees": source_agrees,
                       "from_text": (second if head["direction"] < 0 else first)[:1],
                       "to_text": (first if head["direction"] < 0 else second)[:1]})
    chains: list[list[dict]] = []
    for head in sorted(placed, key=lambda item: (item["axis"], item["line"])):
        if chains and chains[-1][0]["axis"] == head["axis"] and abs(chains[-1][-1]["line"] - head["line"]) <= LINE_TOLERANCE_PX:
            chains[-1].append(head)
        else:
            chains.append([head])
    flagged = []
    for chain in chains:
        if (len({tuple(map(tuple, head["pair"])) for head in chain}) >= 2
                and all(head["direction"] < 0 and head["source_agrees"] for head in chain)):
            flagged.append({
                "axis": chain[0]["axis"], "count": len(chain),
                "arrowheads": [{"id": head["id"], "points": head["points"], "points_to": head["to_text"],
                                "points_from": head["from_text"]} for head in chain][:6],
            })
    return flagged


def apply_connector_direction_quality(quality: dict, svg: str, source: str) -> None:
    """Add a ``connector_arrowheads_reversed`` failure when a whole line of arrowheads points back."""
    try:
        chains = reversed_arrowhead_chains(svg, source)
    except (ET.ParseError, ValueError):
        return
    if not chains:
        return
    quality.setdefault("failures", []).append({
        "kind": FAILURE_KIND, "chains": chains,
        "instruction": ("Each listed arrowhead points from a later item back to the earlier one, against the"
                        " order the source gives. Redraw every arrowhead of that row or column so its apex,"
                        " the corner opposite its flat base, touches the next item: in a left-to-right row the"
                        " apex has the largest x of the three points, in a top-to-bottom column the largest y."
                        " Keep the items, their order and every other element as they are."),
    })
    quality["passed"] = False
    quality["geometry_passed"] = False
