"""Server-rendered native charts: the drawing is derived from the declaration.

An LLM author draws a chart as SVG primitives and declares the same chart as
JSON; the PPTX export rebuilds the chart from the JSON. Measured on real decks
(docs/session-results/svg-server-chart-design-20261108.md) the JSON matched the
source in every case while the hand-drawn geometry was the dominant cause of
rejected chart pages. With this contract the author declares the chart and its
box only, as an empty placeholder group::

    <g id="native-chart-SLUG" data-native-chart='JSON'
       data-chart-render="server" data-chart-box="X Y WIDTH HEIGHT"/>

and :func:`render_server_chart` draws the group's contents from the JSON,
deterministically. It runs inside ``extract_native_declarations`` so every
replay of a stored raw response (author, partial recovery, page remake) draws
the identical tree. The drawing obeys the existing native chart contract (one
baseline and scale, value labels with their datum, axis beneath the bars), so
the unchanged requirement checks and exporter apply to it.

Version one draws bar and column charts (clustered, one or more series).
Any other form leaves the placeholder empty and records why; nothing is
guessed.
"""
from __future__ import annotations

from collections import Counter
import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

SERVER_CHART_REVISION = "svg-server-chart/v1"
SNAPSHOT_KEY = "server_chart_render"
RENDER_ATTRIBUTE = "data-chart-render"
BOX_ATTRIBUTE = "data-chart-box"
_SVG = "{http://www.w3.org/2000/svg}"
_FAMILIES = {"bar", "column"}
_DEFAULT_COLORS = ("#2563EB", "#94A3B8", "#0F172A", "#38BDF8", "#64748B", "#1E3A8A")
_TITLE_PX = 18
_LABEL_PX = 16
_MIN_BOX = (160.0, 120.0)


class ServerChartUnsupported(ValueError):
    """The declaration cannot be drawn by version one; the reason is the message."""


def server_chart_policy(style_snapshot: dict | None) -> bool:
    """Whether a deck was admitted with server-rendered charts (frozen at admission)."""
    return isinstance(style_snapshot, dict) and style_snapshot.get(SNAPSHOT_KEY) == SERVER_CHART_REVISION


SERVER_CHART_RULE = (
    "Bar and column charts are drawn by the server from your JSON. For a bar or column chart write only "
    "an empty placeholder <g id=\"native-chart-SLUG\" data-native-chart='JSON' data-chart-render=\"server\" "
    "data-chart-box=\"X Y WIDTH HEIGHT\"/> with the same JSON contract; do not draw its bars, axes, labels, "
    "legend or title yourself. The box is where the whole chart (its title, legend, category and value "
    "labels included) will be drawn: at least 360x240 px, clear of every other element; put captions, "
    "units notes and callouts outside it. Use \"grouping\":\"clustered\" (or omit grouping). Line, area, "
    "pie, donut and stacked charts are drawn by you as before.\n"
)


def server_chart_rule(style_snapshot: dict | None) -> str:
    return SERVER_CHART_RULE if server_chart_policy(style_snapshot) else ""


def _text_width(text: str, size: float) -> float:
    from services.generation_core.drivers.svg.fallback import _text_em

    return _text_em(text) * size


def _number(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ServerChartUnsupported("server_chart_value_invalid")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ServerChartUnsupported("server_chart_value_invalid") from error
    if not number.is_finite():
        raise ServerChartUnsupported("server_chart_value_invalid")
    return number


def _label(value) -> str:
    number = _number(value)
    text = format(number.normalize(), "f") if number == number.to_integral() else format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    sign = "-" if text.startswith("-") else ""
    whole, _, fraction = text.lstrip("-").partition(".")
    if len(whole) > 3:
        whole = f"{int(whole):,}"
    return sign + whole + (f".{fraction}" if fraction else "")


def _fmt(value: float) -> str:
    rounded = round(value, 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"


def _page_ink(root: ET.Element) -> tuple[str, str]:
    """The page's dominant text font and colour, so the chart reads as part of the page."""
    fonts, fills = Counter(), Counter()
    for node in root.iter(_SVG + "text"):
        if node.get("font-family"):
            fonts[node.get("font-family")] += 1
        if node.get("fill") and node.get("fill").startswith("#"):
            fills[node.get("fill")] += 1
    font = fonts.most_common(1)[0][0] if fonts else "Noto Sans KR"
    fill = fills.most_common(1)[0][0] if fills else "#0F172A"
    return font, fill


def _contrast(color: str) -> str:
    """The higher-contrast value text colour (white or near-black) on a bar of ``color`` (WCAG)."""
    def luminance(value: str) -> float:
        r, g, b = (int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))
        def channel(part):
            return part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    try:
        bar = luminance(color)
    except (ValueError, IndexError):
        return "#FFFFFF"
    dark = luminance("#0F172A")
    return "#FFFFFF" if 1.05 / (bar + 0.05) >= (bar + 0.05) / (dark + 0.05) else "#0F172A"


def _box(node: ET.Element) -> tuple[float, float, float, float]:
    try:
        x, y, width, height = (float(part) for part in node.get(BOX_ATTRIBUTE, "").replace(",", " ").split())
    except ValueError as error:
        raise ServerChartUnsupported("server_chart_box_invalid") from error
    if width < _MIN_BOX[0] or height < _MIN_BOX[1]:
        raise ServerChartUnsupported("server_chart_box_too_small")
    return x, y, width, height


def _validated(raw: dict) -> tuple[str, list[str], list[dict]]:
    chart_type = raw.get("chartType")
    if chart_type not in _FAMILIES:
        raise ServerChartUnsupported("server_chart_type_unsupported")
    if raw.get("grouping") not in (None, "clustered"):
        raise ServerChartUnsupported("server_chart_grouping_unsupported")
    categories, series = raw.get("categories"), raw.get("series")
    if (not isinstance(categories, list) or not categories or not all(isinstance(item, str) and item.strip()
                                                                       for item in categories)
            or not isinstance(series, list) or not series):
        raise ServerChartUnsupported("server_chart_shape_invalid")
    for item in series:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
                or not isinstance(item.get("values"), list) or len(item["values"]) != len(categories)):
            raise ServerChartUnsupported("server_chart_shape_invalid")
        if any(_number(value) <= 0 for value in item["values"]):
            # The native bar/column contract draws positive values only.
            raise ServerChartUnsupported("server_chart_value_not_positive")
    return chart_type, categories, series


def _element(parent, tag, **attributes):
    node = ET.SubElement(parent, _SVG + tag, {key.replace("_", "-"): value for key, value in attributes.items()})
    return node


def _text(parent, value, x, y, *, size, font, fill, anchor="start", weight=None):
    attributes = {"x": _fmt(x), "y": _fmt(y), "font_family": font, "font_size": _fmt(size), "fill": fill}
    if anchor != "start":
        attributes["text_anchor"] = anchor
    if weight:
        attributes["font_weight"] = weight
    node = _element(parent, "text", **attributes)
    node.text = value
    return node


def render_server_chart(root: ET.Element, node: ET.Element, raw: dict) -> None:
    """Replace ``node``'s children with the chart ``raw`` declares, inside its box.

    Raises :class:`ServerChartUnsupported` (and leaves ``node`` empty) when
    version one cannot draw the declaration.
    """
    for child in list(node):
        node.remove(child)
    x0, y0, width, height = _box(node)
    for key in (RENDER_ATTRIBUTE, BOX_ATTRIBUTE):
        node.attrib.pop(key, None)
    chart_type, categories, series = _validated(raw)
    font, ink = _page_ink(root)
    axis = "#CBD5E1"
    horizontal = chart_type == "bar"
    labels = raw.get("valueLabels") if isinstance(raw.get("valueLabels"), dict) else {}
    show_values = labels.get("shown", True) is not False
    position = labels.get("position") if labels.get("position") in {"outsideEnd", "insideEnd", "center",
                                                                     "insideBase"} else "outsideEnd"
    if len(series) > 1 and position != "outsideEnd":
        position = "outsideEnd"
    raw.setdefault("valueLabels", {})
    if isinstance(raw["valueLabels"], dict):
        raw["valueLabels"].update(shown=show_values, position=position)
    raw["categoryOrder"] = "topToBottom" if horizontal else "leftToRight"
    raw["grouping"] = "clustered"
    raw["markers"] = False

    top = y0
    title = raw.get("title")
    if not (isinstance(title, str) and title.strip()) and len(series) == 1:
        # A single series is named by its drawn title (the native contract's legend rule).
        title = raw["title"] = series[0]["name"]
    if isinstance(title, str) and title.strip():
        _text(node, title, x0, top + _TITLE_PX, size=_TITLE_PX, font=font, fill=ink, weight="bold")
        top += _TITLE_PX + 14
    colors = []
    for index, item in enumerate(series):
        declared = item.get("pointColors")
        if not (isinstance(declared, list) and len(declared) == len(categories)
                and all(isinstance(color, str) and color.startswith("#") for color in declared)):
            declared = [_DEFAULT_COLORS[index % len(_DEFAULT_COLORS)]] * len(categories)
            item["pointColors"] = declared
        colors.append(declared)
    if len(series) > 1:
        cursor = x0
        for index, item in enumerate(series):
            _element(node, "rect", x=_fmt(cursor), y=_fmt(top), width="14", height="14", fill=colors[index][0])
            _text(node, item["name"], cursor + 20, top + 13, size=_LABEL_PX, font=font, fill=ink)
            cursor += 20 + _text_width(item["name"], _LABEL_PX) + 24
        top += 30

    high = max(_number(value) for item in series for value in item["values"])
    span = float(high)
    value_room = _text_width(max((_label(value) for item in series for value in item["values"]), key=len),
                             _LABEL_PX) + 10 if show_values and position == "outsideEnd" else 0
    bottom = y0 + height

    def value_label(text, color, *, end, base, middle, cross, anchors):
        """``end``/``base``/``middle`` along the value axis; ``cross`` across it."""
        inside = _contrast(color)
        along = {"outsideEnd": (end[0], ink, anchors[0]), "insideEnd": (end[1], inside, anchors[1]),
                 "insideBase": (base, inside, anchors[2]), "center": (middle, inside, "middle")}[position]
        x, y = (along[0], cross) if horizontal else (cross, along[0])
        _text(node, text, x, y, size=_LABEL_PX, font=font, fill=along[1],
              anchor=along[2] if horizontal else "middle", weight="bold")

    if horizontal:
        label_width = min(max(_text_width(category, _LABEL_PX) for category in categories) + 14, width * 0.38)
        zero = x0 + label_width
        plot_width = width - label_width - value_room
        if plot_width < 80:
            raise ServerChartUnsupported("server_chart_box_too_small")
        band = (bottom - top) / len(categories)
        # The axis is drawn before the bars that rest on it.
        _element(node, "line", x1=_fmt(zero), y1=_fmt(top), x2=_fmt(zero), y2=_fmt(bottom), stroke=axis,
                 stroke_width="2")
        thickness = min(band * 0.72 / len(series), 64)
        for c_index, category in enumerate(categories):
            center = top + band * (c_index + 0.5)
            first = center - thickness * len(series) / 2
            _text(node, category, x0, center + _LABEL_PX * 0.35, size=_LABEL_PX, font=font, fill=ink)
            for s_index, item in enumerate(series):
                length = plot_width * float(_number(item["values"][c_index])) / span
                bar_y = first + thickness * s_index
                color = colors[s_index][c_index]
                _element(node, "rect", x=_fmt(zero), y=_fmt(bar_y), width=_fmt(length), height=_fmt(thickness),
                         fill=color)
                if show_values:
                    value_label(_label(item["values"][c_index]), color, end=(zero + length + 8, zero + length - 8),
                                base=zero + 8, middle=zero + length / 2,
                                cross=bar_y + thickness / 2 + _LABEL_PX * 0.35, anchors=("start", "end", "start"))
        return

    category_height = _LABEL_PX + 14
    plot_top = top + (_LABEL_PX + 12 if show_values and position == "outsideEnd" else 0)
    zero = bottom - category_height
    if zero - plot_top < 60:
        raise ServerChartUnsupported("server_chart_box_too_small")
    band = width / len(categories)
    if any(_text_width(category, _LABEL_PX) > band - 8 for category in categories):
        raise ServerChartUnsupported("server_chart_category_labels_too_wide")
    _element(node, "line", x1=_fmt(x0), y1=_fmt(zero), x2=_fmt(x0 + width), y2=_fmt(zero), stroke=axis,
             stroke_width="2")
    thickness = min(band * 0.72 / len(series), 72)
    for c_index, category in enumerate(categories):
        center = x0 + band * (c_index + 0.5)
        first = center - thickness * len(series) / 2
        _text(node, category, center, bottom - 8, size=_LABEL_PX, font=font, fill=ink, anchor="middle")
        for s_index, item in enumerate(series):
            length = (zero - plot_top) * float(_number(item["values"][c_index])) / span
            bar_x = first + thickness * s_index
            color = colors[s_index][c_index]
            _element(node, "rect", x=_fmt(bar_x), y=_fmt(zero - length), width=_fmt(thickness), height=_fmt(length),
                     fill=color)
            if show_values:
                value_label(_label(item["values"][c_index]), color,
                            end=(zero - length - 8, zero - length + _LABEL_PX + 4), base=zero - 8,
                            middle=zero - length / 2 + _LABEL_PX * 0.35, cross=bar_x + thickness / 2,
                            anchors=("middle", "middle", "middle"))



_SOURCE_NUMBER = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+(?:\.\d+)?")


def source_numbers(text: str) -> set[Decimal]:
    """Every number the source states, thousands separators removed."""
    return {Decimal(match.replace(",", "")) for match in _SOURCE_NUMBER.findall(text or "")}


def apply_server_chart_grounding(quality: dict, payload: dict, svg: str, source: str) -> None:
    """Fail a server-rendered chart whose values are not numbers the source states.

    The drawing is derived from the declaration, so the declaration's values
    are the only thing left to check: each must occur in the page's source.
    """
    from services.generation_core.drivers.svg.native_declarations import bound_declarations

    record, current = bound_declarations(payload, svg)
    if record is None or not current:
        return
    stated = source_numbers(source if isinstance(source, str) else "")
    failures = []
    for item in record.get("objects") or []:
        if not isinstance(item, dict) or item.get("render") != SERVER_CHART_REVISION:
            continue
        values = [value for series in item["raw"].get("series", []) for value in series.get("values", [])]
        missing = sorted({_label(value) for value in values if _number(value) not in stated})
        if missing:
            failures.append({"kind": "native_requested_chart_missing", "declarationId": item.get("id"),
                             "reason": "server_chart_value_ungrounded", "values": missing})
    if failures:
        quality["passed"] = False
        quality.setdefault("failures", []).extend(failures)


__all__ = ["RENDER_ATTRIBUTE", "BOX_ATTRIBUTE", "SERVER_CHART_REVISION", "SERVER_CHART_RULE", "SNAPSHOT_KEY",
           "ServerChartUnsupported", "apply_server_chart_grounding", "render_server_chart", "server_chart_policy",
           "server_chart_rule", "source_numbers"]
