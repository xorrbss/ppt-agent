"""Find bar/column charts an author drew without a native chart declaration.

A chart drawn as plain shapes exports as a picture and is never checked
against its data. Measured on real decks
(docs/session-results/svg-server-chart-design-20261108.md), this became the
main way a chart missed native export. The detector is deliberately narrow:
three or more rects that share an edge (one x for horizontal bars, one bottom
for columns), have the same thickness, differ in length, and each carry a
numeric label next to it. Shapes inside a declared native group or server
furniture are ignored, and so is any shape under a transformed group (its
coordinates are not the page's), so a miss is preferred to a false alarm.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_geometry import normalize_text, parse_label_number

_SVG = "{http://www.w3.org/2000/svg}"
_EXCLUDED_PREFIXES = ("native-chart", "native-table", "svg-system-")
_MIN_BARS = 3
_THICKNESS_TOLERANCE = 0.15
_MIN_LENGTH_SPREAD = 1.2


def _float(node, key):
    try:
        return float(node.get(key, "0"))
    except ValueError:
        return None


def _visible_nodes(root: ET.Element):
    """Rects and texts in page coordinates, outside native groups and server furniture."""
    rects, texts = [], []

    def visit(node, hidden):
        tag = node.tag.removeprefix(_SVG)
        identifier = node.get("id") or ""
        if identifier.startswith(_EXCLUDED_PREFIXES) or node.get("transform"):
            hidden = True
        if not hidden and tag == "rect":
            x, y, width, height = (_float(node, key) for key in ("x", "y", "width", "height"))
            if None not in (x, y, width, height) and node.get("fill") not in (None, "none") and width > 4 and height > 4:
                rects.append((x, y, width, height))
        elif not hidden and tag == "text":
            content = normalize_text(" ".join(part for part in node.itertext()))
            x, y = _float(node, "x"), _float(node, "y")
            if content and x is not None and y is not None and parse_label_number(content) is not None:
                texts.append((x, y, content))
        for child in node:
            visit(child, hidden)

    visit(root, False)
    return rects, texts


def _similar(values) -> bool:
    return max(values) <= min(values) * (1 + _THICKNESS_TOLERANCE)


def _labelled(bar, texts, horizontal: bool) -> bool:
    x, y, width, height = bar
    for tx, ty, _content in texts:
        if horizontal and y - 2 <= ty <= y + height + 8 and x - 4 <= tx <= x + width + 220:
            return True
        if not horizontal and x - 24 <= tx <= x + width + 24 and y - 48 <= ty <= y + height:
            return True
    return False


def undeclared_bar_charts(svg: str) -> list[dict]:
    """``[{"orientation", "bars", "box"}]`` for every undeclared bar/column chart found in ``svg``."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return []
    rects, texts = _visible_nodes(root)
    found = []
    for horizontal in (True, False):
        groups: dict[int, list] = {}
        for rect in rects:
            x, y, width, height = rect
            if horizontal and height <= 90 and width >= 8:
                groups.setdefault(round(x), []).append(rect)
            elif not horizontal and width <= 140 and height >= 8:
                groups.setdefault(round(y + height), []).append(rect)
        for bars in groups.values():
            if len(bars) < _MIN_BARS:
                continue
            thickness = [bar[3] if horizontal else bar[2] for bar in bars]
            lengths = [bar[2] if horizontal else bar[3] for bar in bars]
            if not _similar(thickness) or max(lengths) < min(lengths) * _MIN_LENGTH_SPREAD:
                continue
            labelled = [bar for bar in bars if _labelled(bar, texts, horizontal)]
            if len(labelled) < _MIN_BARS:
                continue
            x0 = min(bar[0] for bar in bars)
            y0 = min(bar[1] for bar in bars)
            x1 = max(bar[0] + bar[2] for bar in bars)
            y1 = max(bar[1] + bar[3] for bar in bars)
            found.append({"orientation": "bar" if horizontal else "column", "bars": len(bars),
                          "box": [round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1)]})
    return found


UNDECLARED_KIND = "undeclared_chart_drawn"
REPAIR_INSTRUCTION = (
    "Each listed box shows a bar or column chart drawn as plain shapes: it would export as a picture. "
    "Replace each with an empty server chart placeholder (data-native-chart JSON, data-chart-render and "
    "data-chart-box covering the chart) carrying the same categories, values and title, and remove the drawn "
    "bars and their value labels. Keep every other word and number on the page."
)


def apply_undeclared_chart_quality(quality: dict, svg: str) -> None:
    """Record each undeclared bar/column chart as a layout failure (its data is still visible)."""
    found = undeclared_bar_charts(svg)
    if not found:
        return
    quality["passed"] = False
    quality.setdefault("failures", []).extend(
        {"kind": UNDECLARED_KIND, "orientation": item["orientation"], "bars": item["bars"], "box": item["box"]}
        for item in found)


def undeclared_chart_repair_hint(quality: dict) -> str | None:
    """The correction instruction when a page drew a chart without declaring it."""
    import json

    failures = quality.get("failures") or []
    undeclared = [item for item in failures if item.get("kind") == UNDECLARED_KIND]
    if not undeclared:
        return None
    others = [item for item in failures if item.get("kind") != UNDECLARED_KIND]
    return json.dumps({"kind": UNDECLARED_KIND, "instruction": REPAIR_INSTRUCTION,
                       "boxes": [item["box"] for item in undeclared], "other_failures": others[:8]},
                      ensure_ascii=False)[:3000]


__all__ = ["REPAIR_INSTRUCTION", "UNDECLARED_KIND", "apply_undeclared_chart_quality", "undeclared_bar_charts",
           "undeclared_chart_repair_hint"]
