"""Validate one declared native chart against its drawing and derive the V1 payload.

Converted natively, the group's drawn children are replaced by a chart built
from the payload, so the payload may only say what the group already shows:

* The model JSON declares its category order, point colours, value labels and
  markers alongside the authored V1 values; SVG-only options live on the
  sidecar entry, leaving the V1 chart payload unchanged.
* Every category and series name (and the title, when declared) is drawn as a
  text in the group: the converter drops, and the pre-export validator
  rejects, a title or legend entry that is not drawn inside the group. Axis
  titles are not part of the model JSON. A pie has exactly one series. Chart
  texts are compared with their lines joined by a space, whitespace-normalized
  NFC.
* Every drawn text is a category, a series name, the title, or a value label:
  a number (``,`` separators, sign, currency prefix and short unit suffix
  allowed; the suffix is not interpreted) equal to one of the series values.
  Axis tick labels are therefore not accepted: the native chart draws its own.
* Anchor: the bounding box of every primitive (text boxes estimated from their
  font size), canvas px.
* Style: series colours only where unambiguous -- bar/column: exactly one rect
  fill per series drawn once per category (plus an optional legend swatch),
  assigned in drawing order; line: one stroked polyline/path per series. A
  font family shared by every drawn text becomes the chart font.
"""
from __future__ import annotations

import re

from services.generation_core.drivers.svg.native_geometry import (
    GroupPrimitives, NativeRejection, normalize_text, parse_label_number,
)
from services.generation_core.drivers.svg.native_chart_visual import chart_appearance

CHART_ELEMENTS = frozenset({"g", "rect", "line", "polyline", "polygon", "path", "circle", "ellipse",
                            "text", "tspan"})


def _model_chart(raw: dict) -> dict:
    if not set(raw) <= {"chartType", "categories", "series", "title", "categoryOrder", "valueLabels", "markers"}:
        raise NativeRejection("native_chart_json_invalid")
    categories, series = raw.get("categories"), raw.get("series")
    if (not isinstance(categories, list) or not all(isinstance(c, str) for c in categories)
            or not isinstance(series, list) or not series
            or not all(isinstance(s, dict) and set(s) <= {"name", "values", "pointColors"}
                       and isinstance(s.get("name"), str)
                       for s in series)
            or (raw.get("title") is not None and not isinstance(raw["title"], str))):
        raise NativeRejection("native_chart_json_invalid")
    payload = {"chartType": raw.get("chartType"), "categories": list(categories),
               "series": [{"name": s["name"], "values": s.get("values"),
                           **({"pointColors": s["pointColors"]} if "pointColors" in s else {})}
                          for s in series]}
    if raw.get("title"):
        payload["title"] = raw["title"]
    return payload


def _series_colors(payload: dict, prims: GroupPrimitives) -> list[str] | None:
    count, series = len(payload["categories"]), len(payload["series"])
    if payload["chartType"] in ("bar", "column"):
        order: list[str] = []
        tally: dict[str, int] = {}
        for shape in prims.shapes:
            if shape.tag == "rect" and shape.fill:
                tally[shape.fill] = tally.get(shape.fill, 0) + 1
                if shape.fill not in order:
                    order.append(shape.fill)
        colors = [fill for fill in order if tally[fill] in (count, count + 1)]
        return colors if len(colors) == series else None
    if payload["chartType"] == "line":
        strokes = [s.stroke for s in prims.shapes
                   if s.tag in ("polyline", "path") and s.stroke and s.point_count >= count]
        return strokes if len(strokes) == series else None
    return None


def _drawn_series_name(name: str, drawn: set[str]) -> str:
    exact = normalize_text(name)
    if exact in drawn:
        return name
    # Axis ranges sometimes qualify a declared series name while the legend
    # draws only its short name. The native legend must keep the drawn wording.
    short = normalize_text(re.split(r"\s*\(", name, maxsplit=1)[0])
    if short and short in drawn:
        return short
    raise NativeRejection("native_chart_label_not_drawn")


def _chart_furniture(text: str) -> bool:
    return text.startswith(("단위", "좌축", "우축", "축 ", "Axis ")) or "계열별 축" in text


def _axis_ticks(payload: dict, prims: GroupPrimitives, indexes: list[int]) -> bool:
    """Numeric furniture sits together outside the category plot positions."""
    if len(indexes) < 2:
        return False
    categories = {normalize_text(value) for value in payload["categories"]}
    category_texts = [item for item in prims.texts
                      if normalize_text(" ".join(item.lines)) in categories]
    if len(category_texts) != len(categories):
        return False
    horizontal = payload["chartType"] == "bar"
    def coordinate(item):
        box = item.box()
        return (box.y0 + box.y1) / 2 if horizontal else (box.x0 + box.x1) / 2
    positions = [coordinate(prims.texts[index]) for index in indexes]
    return (max(positions) - min(positions) <= 16
            and (min(positions) > max(map(coordinate, category_texts)) + 10 if horizontal
                 else max(positions) < min(map(coordinate, category_texts)) - 10))


def chart_object(raw: dict, prims: GroupPrimitives) -> tuple[dict, dict, dict | None]:
    """``(anchor, payload, SVG appearance)`` for one declared chart."""
    if not prims.tags <= CHART_ELEMENTS:
        raise NativeRejection("native_group_element_unsupported")
    payload = _model_chart(raw)
    if payload["chartType"] == "pie" and len(payload["series"]) != 1:
        raise NativeRejection("native_chart_pie_series_invalid")
    drawn = [normalize_text(" ".join(text.lines)) for text in prims.texts]
    drawn_set = set(drawn)
    labels = {normalize_text(item) for item in payload["categories"]}
    for item in payload["series"]:
        item["name"] = _drawn_series_name(item["name"], drawn_set)
    names = {normalize_text(item["name"]) for item in payload["series"]}
    title = {normalize_text(payload["title"])} if payload.get("title") else set()
    values = [value for item in payload["series"] if isinstance(item["values"], list)
              for value in item["values"] if type(value) in (int, float)]
    if not (labels | names | title) <= drawn_set:
        raise NativeRejection("native_chart_label_not_drawn")
    unknown_numbers = [index for index, text in enumerate(drawn)
                       if text not in labels | names | title and not _chart_furniture(text)
                       and (number := parse_label_number(text)) is not None
                       and not any(abs(number - value) <= 1e-9 * max(1.0, abs(value))
                                   for value in values)]
    if unknown_numbers and not _axis_ticks(payload, prims, unknown_numbers):
        raise NativeRejection("native_chart_text_unaccounted")
    for index, text in enumerate(drawn):
        if text in labels or text in names or text in title:
            continue
        number = parse_label_number(text)
        if _chart_furniture(text) or index in unknown_numbers:
            continue
        if number is None or not any(abs(number - v) <= 1e-9 * max(1.0, abs(v)) for v in values):
            raise NativeRejection("native_chart_text_unaccounted")
    boxes = [shape.box for shape in prims.shapes] + [text.box() for text in prims.texts]
    if not boxes:
        raise NativeRejection("native_chart_empty")
    frame = boxes[0]
    for box in boxes[1:]:
        frame = frame.union(box)
    if frame.width <= 0 or frame.height <= 0:
        raise NativeRejection("native_chart_empty")
    colors = _series_colors(payload, prims) if isinstance(payload["chartType"], str) else None
    if colors:
        for item, color in zip(payload["series"], colors, strict=True):
            item["color"] = color
    families = {text.family.split(",")[0].strip().strip("'\"") for text in prims.texts}
    if len(families) == 1 and "" not in families:
        payload["fontFamily"] = families.pop()
    appearance = chart_appearance(raw, payload, prims)
    return frame.anchor(), payload, appearance
