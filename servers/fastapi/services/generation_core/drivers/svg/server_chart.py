"""Server-rendered native charts: the drawing is derived from the declaration.

An LLM author draws a chart as SVG primitives and declares the same chart as
JSON; the PPTX export rebuilds the chart from the JSON. Measured on real decks
(docs/session-results/svg-server-chart-design-20261108.md) the JSON matched the
source in every case while the hand-drawn geometry was the dominant cause of
rejected chart pages. With this contract the author declares the chart and its
box only, as an empty placeholder group::

    <g id="native-chart-SLUG" data-native-chart='JSON'
       data-chart-render="svg-server-chart/v1" data-chart-box="X Y WIDTH HEIGHT"/>

and the renderer named by ``data-chart-render`` draws the group's contents from
the JSON, deterministically. It runs inside ``extract_native_declarations`` so
every replay of a stored raw response (author, partial recovery, page remake)
draws the identical tree. **A published renderer revision never changes its
output** (``test_svg_server_chart.py`` pins digests); an improvement is a new
revision with its own marker. The drawing obeys the existing native chart
contract (one baseline and scale, value labels with their datum, the axis
beneath the bars), so the unchanged requirement checks and exporter apply to it.

Revision one draws bar and column charts: clustered (one or more series) and
stacked or percent-stacked (two or more series), positive values. Value labels
carry the title's unit. Admission enables it for V4 decks only: the V5 unit
grammar does not read every unit a label may carry (만명, 점). Any other form leaves the placeholder empty and records why;
nothing is guessed.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from statistics import median
from xml.etree import ElementTree as ET

SERVER_CHART_REVISION = "svg-server-chart/v1"
SNAPSHOT_KEY = "server_chart_render"
RENDER_ATTRIBUTE = "data-chart-render"
BOX_ATTRIBUTE = "data-chart-box"
_SVG = "{http://www.w3.org/2000/svg}"
_FAMILIES = {"bar", "column"}
_DEFAULT_COLORS = ("#2563EB", "#94A3B8", "#0F172A", "#38BDF8", "#64748B", "#1E3A8A")
_AXIS = "#CBD5E1"
_DARK = "#0F172A"
_MIN_BOX = (160.0, 120.0)
_LABEL_PX_RANGE = (14.0, 20.0)
_POSITIONS = ("outsideEnd", "insideEnd", "center", "insideBase")
_STACKED = ("stacked", "percentStacked")


class ServerChartUnsupported(ValueError):
    """The declaration cannot be drawn by this revision; the reason is the message."""


def server_chart_policy(style_snapshot: dict | None) -> bool:
    """Whether a deck was admitted with server-rendered charts (frozen at admission)."""
    return isinstance(style_snapshot, dict) and style_snapshot.get(SNAPSHOT_KEY) == SERVER_CHART_REVISION


SERVER_CHART_RULE = (
    "Bar and column charts are drawn by the server from your JSON. For a bar or column chart write only "
    "an empty placeholder <g id=\"native-chart-SLUG\" data-native-chart='JSON' "
    f"data-chart-render=\"{SERVER_CHART_REVISION}\" data-chart-box=\"X Y WIDTH HEIGHT\"/> with the same JSON "
    "contract; do not draw its bars, axes, labels, legend or title yourself. The box is where the whole "
    "chart (its title, legend, category and value labels included) will be drawn: at least 360x240 px, "
    "clear of every other element; put captions, unit notes and callouts outside it. Chart only numbers "
    "the source states, each with the category it belongs to; values must be positive. Put the unit in the "
    "title in parentheses, such as \"(억원)\", \"(%)\" or \"(점)\": value labels show it. Use "
    "\"grouping\":\"clustered\" (or omit it), or \"stacked\"/\"percentStacked\" with two or more series "
    "for parts of a whole. Line, area, pie, donut charts and charts with zero or negative values are drawn by "
    "you as before. When previous_svg shows such a placeholder, keep "
    "it as a placeholder.\n"
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


def _page_style(root: ET.Element, chart: ET.Element) -> tuple[str, str, float]:
    """The page's dominant text font, colour and body size, so the chart reads as part of it."""
    inside = set(chart.iter())
    fonts, fills, sizes = Counter(), Counter(), []
    for node in root.iter(_SVG + "text"):
        if node in inside:
            continue
        if node.get("font-family"):
            fonts[node.get("font-family")] += 1
        if (node.get("fill") or "").startswith("#"):
            fills[node.get("fill")] += 1
        try:
            sizes.append(float(node.get("font-size", "")))
        except ValueError:
            pass
    font = fonts.most_common(1)[0][0] if fonts else "Noto Sans KR"
    fill = fills.most_common(1)[0][0] if fills else _DARK
    size = median(sizes) if sizes else 16.0
    return font, fill, min(max(size, _LABEL_PX_RANGE[0]), _LABEL_PX_RANGE[1])


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
    dark = luminance(_DARK)
    return "#FFFFFF" if 1.05 / (bar + 0.05) >= (bar + 0.05) / (dark + 0.05) else _DARK


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
    if raw.get("grouping") not in (None, "clustered", "stacked", "percentStacked"):
        raise ServerChartUnsupported("server_chart_grouping_unsupported")
    if raw.get("grouping") in _STACKED and (not isinstance(raw.get("series"), list) or len(raw["series"]) < 2):
        # A stack needs two or more series (the native stack contract).
        raise ServerChartUnsupported("server_chart_stack_needs_series")
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


def _wrap(text: str, size: float, width: float) -> list[str] | None:
    """``text`` in at most two lines no wider than ``width``, split at a space; ``None`` if impossible."""
    if _text_width(text, size) <= width:
        return [text]
    words = text.split(" ")
    for cut in range(len(words) - 1, 0, -1):
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
        if all(_text_width(line, size) <= width for line in lines):
            return lines
    return None


def _wrap_all(categories: list[str], size: float, width: float) -> tuple[float, list[list[str]]]:
    """Category lines at the largest size (body size down to the floor) where every name fits."""
    candidate = size
    while candidate >= _LABEL_PX_RANGE[0]:
        lines = [_wrap(category, candidate, width) for category in categories]
        if all(item is not None for item in lines):
            return candidate, lines
        candidate -= 1
    raise ServerChartUnsupported("server_chart_category_labels_too_wide")


def _element(parent, tag, **attributes):
    return ET.SubElement(parent, _SVG + tag, {key.replace("_", "-"): value for key, value in attributes.items()})


def _text(parent, lines, x, y, *, size, font, fill, anchor="start", weight=None, line_height=None):
    """One text; several ``lines`` become tspans with explicit positions, first baseline at ``y``."""
    attributes = {"x": _fmt(x), "y": _fmt(y), "font_family": font, "font_size": _fmt(size), "fill": fill}
    if anchor != "start":
        attributes["text_anchor"] = anchor
    if weight:
        attributes["font_weight"] = weight
    node = _element(parent, "text", **attributes)
    if isinstance(lines, str) or len(lines) == 1:
        node.text = lines if isinstance(lines, str) else lines[0]
        return node
    for index, line in enumerate(lines):
        _element(node, "tspan", x=_fmt(x), y=_fmt(y + index * (line_height or size * 1.25))).text = line
    return node


def _label_unit(title: str | None, series: list[dict]) -> str:
    """The unit value labels carry: the title's ``(unit)``, else one unit shared by every series name.

    A source states "38억" or "22%"; a label without the unit would drop that
    literal from the page. Only a short word the label grammar reads as a
    suffix is used.
    """
    from services.generation_core.drivers.svg.native_requirements import _series_name_unit

    def trailing(text):
        found = re.search(r"\(([^()]+)\)\s*$", text or "")
        return found[1].strip() if found else None

    unit = (_series_name_unit(title) or trailing(title)) if title else None
    if unit is None:
        units = {_series_name_unit(item["name"]) or trailing(item["name"]) for item in series}
        unit = units.pop() if len(units) == 1 else None
    unit = (unit or "").strip()
    return unit if re.fullmatch(r"%|[A-Za-z가-힣]{1,4}", unit) else ""


class _Layout:
    """Everything a drawing needs except the value label position."""

    def __init__(self, root, node, raw):
        self.x0, self.y0, self.width, self.height = _box(node)
        self.chart_type, self.categories, self.series = _validated(raw)
        self.font, self.ink, self.size = _page_style(root, node)
        self.title_size = self.size + 2
        self.horizontal = self.chart_type == "bar"
        title = raw.get("title")
        if not (isinstance(title, str) and title.strip()) and len(self.series) == 1:
            # A single series is named by its drawn title (the native contract's legend rule).
            title = self.series[0]["name"]
        self.title = title if isinstance(title, str) and title.strip() else None
        self.grouping = raw.get("grouping") if raw.get("grouping") in _STACKED else "clustered"
        self.colors = []
        for index, item in enumerate(self.series):
            declared = item.get("pointColors")
            if self.grouping in _STACKED and isinstance(declared, list) and declared:
                # One colour per series in a stack (the native stack contract).
                declared = [declared[0]] * len(self.categories)
            if not (isinstance(declared, list) and len(declared) == len(self.categories)
                    and all(isinstance(color, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", color) for color in declared)):
                declared = [_DEFAULT_COLORS[index % len(_DEFAULT_COLORS)]] * len(self.categories)
            self.colors.append(declared)
        self.high = float(max(_number(value) for item in self.series for value in item["values"]))
        self.unit = _label_unit(self.title, self.series)
        self.labels = [[_label(value) + self.unit for value in item["values"]] for item in self.series]


def _legend(layout: _Layout, node, top: float) -> float:
    """Swatches and names in rows that fit the box; returns the next free y."""
    if len(layout.series) < 2:
        return top
    row_height = layout.size + 10
    cursor, row = layout.x0, 0
    for index, item in enumerate(layout.series):
        width = 20 + _text_width(item["name"], layout.size)
        if width > layout.width:
            raise ServerChartUnsupported("server_chart_legend_too_wide")
        if cursor > layout.x0 and cursor + width > layout.x0 + layout.width:
            cursor, row = layout.x0, row + 1
        y = top + row * row_height
        _element(node, "rect", x=_fmt(cursor), y=_fmt(y), width="14", height="14", fill=layout.colors[index][0])
        _text(node, item["name"], cursor + 20, y + 13, size=layout.size, font=layout.font, fill=layout.ink)
        cursor += width + 24
    return top + (row + 1) * row_height + 4


def _draw(layout: _Layout, node, position: str, show_values: bool) -> bool:
    """Draw into ``node``; ``False`` when an inside value label does not fit its bar."""
    size, font, ink = layout.size, layout.font, layout.ink
    top = layout.y0
    if layout.title:
        _text(node, layout.title, layout.x0, top + layout.title_size, size=layout.title_size, font=font,
              fill=ink, weight="bold")
        top += layout.title_size + 14
    top = _legend(layout, node, top)
    bottom = layout.y0 + layout.height
    longest = max((label for labels in layout.labels for label in labels), key=lambda label: _text_width(label, size))
    value_room = _text_width(longest, size) + 10 if show_values and position == "outsideEnd" else 0
    fits = True
    count = len(layout.series)

    if layout.horizontal:
        category_size, lines = _wrap_all(layout.categories, size, layout.width * 0.38 - 14)
        label_width = max(_text_width(line, category_size) for wrapped in lines for line in wrapped) + 14
        zero = layout.x0 + label_width
        plot = layout.width - label_width - value_room
        band = (bottom - top) / len(layout.categories)
        thickness = min(band * 0.72 / count, 64)
        if plot < 80 or band < max(category_size * 1.25 * max(map(len, lines)), size + 6):
            raise ServerChartUnsupported("server_chart_box_too_small")
        # The axis is drawn before the bars that rest on it.
        _element(node, "line", x1=_fmt(zero), y1=_fmt(top), x2=_fmt(zero), y2=_fmt(bottom), stroke=_AXIS,
                 stroke_width="2")
        for c_index, wrapped in enumerate(lines):
            center = top + band * (c_index + 0.5)
            first_baseline = center + category_size * 0.35 - (len(wrapped) - 1) * category_size * 0.625
            _text(node, wrapped, layout.x0, first_baseline, size=category_size, font=font, fill=ink)
            first = center - thickness * count / 2
            for s_index in range(count):
                length = plot * float(_number(layout.series[s_index]["values"][c_index])) / layout.high
                bar_y = first + thickness * s_index
                color = layout.colors[s_index][c_index]
                _element(node, "rect", x=_fmt(zero), y=_fmt(bar_y), width=_fmt(length), height=_fmt(thickness),
                         fill=color)
                if not show_values:
                    continue
                label = layout.labels[s_index][c_index]
                baseline = bar_y + thickness / 2 + size * 0.35
                if position != "outsideEnd" and (_text_width(label, size) + 16 > length or thickness < size + 4):
                    fits = False
                x, fill, anchor = {"outsideEnd": (zero + length + 8, ink, "start"),
                                   "insideEnd": (zero + length - 8, _contrast(color), "end"),
                                   "insideBase": (zero + 8, _contrast(color), "start"),
                                   "center": (zero + length / 2, _contrast(color), "middle")}[position]
                _text(node, label, x, baseline, size=size, font=font, fill=fill, anchor=anchor, weight="bold")
        return fits

    band = layout.width / len(layout.categories)
    category_size, lines = _wrap_all(layout.categories, size, band - 8)
    category_height = category_size * 1.25 * max(map(len, lines)) + 10
    plot_top = top + (size + 12 if show_values and position == "outsideEnd" else 0)
    zero = bottom - category_height
    thickness = min(band * 0.72 / count, 72)
    if zero - plot_top < 60:
        raise ServerChartUnsupported("server_chart_box_too_small")
    _element(node, "line", x1=_fmt(layout.x0), y1=_fmt(zero), x2=_fmt(layout.x0 + layout.width), y2=_fmt(zero),
             stroke=_AXIS, stroke_width="2")
    for c_index, wrapped in enumerate(lines):
        center = layout.x0 + band * (c_index + 0.5)
        _text(node, wrapped, center, zero + category_size + 6, size=category_size, font=font, fill=ink,
              anchor="middle")
        first = center - thickness * count / 2
        for s_index in range(count):
            length = (zero - plot_top) * float(_number(layout.series[s_index]["values"][c_index])) / layout.high
            bar_x = first + thickness * s_index
            color = layout.colors[s_index][c_index]
            _element(node, "rect", x=_fmt(bar_x), y=_fmt(zero - length), width=_fmt(thickness), height=_fmt(length),
                     fill=color)
            if not show_values:
                continue
            label = layout.labels[s_index][c_index]
            if position != "outsideEnd" and (size + 12 > length or _text_width(label, size) + 6 > thickness):
                fits = False
            y, fill = {"outsideEnd": (zero - length - 8, ink),
                       "insideEnd": (zero - length + size + 4, _contrast(color)),
                       "insideBase": (zero - 8, _contrast(color)),
                       "center": (zero - length / 2 + size * 0.35, _contrast(color))}[position]
            _text(node, label, bar_x + thickness / 2, y, size=size, font=font, fill=fill, anchor="middle",
                  weight="bold")
    return fits


def _draw_stacked(layout: _Layout, node, position: str, show_values: bool) -> bool:
    """A stacked or percent-stacked chart; ``False`` when a segment label does not fit its segment."""
    size, font, ink = layout.size, layout.font, layout.ink
    top = layout.y0
    if layout.title:
        _text(node, layout.title, layout.x0, top + layout.title_size, size=layout.title_size, font=font,
              fill=ink, weight="bold")
        top += layout.title_size + 14
    top = _legend(layout, node, top)
    bottom = layout.y0 + layout.height
    columns = list(zip(*[[float(_number(value)) for value in item["values"]] for item in layout.series], strict=True))
    totals = [sum(column) for column in columns]
    high = max(totals)
    percent = layout.grouping == "percentStacked"
    fits = True

    def segments(c_index, extent):
        """``(series index, start offset, length)`` along the value axis for one category."""
        scale = extent / (totals[c_index] if percent else high)
        offset = 0.0
        for s_index, value in enumerate(columns[c_index]):
            length = value * scale
            yield s_index, offset, length
            offset += length

    def label_fits(label, length, thickness):
        across = _text_width(label, size) + 8
        return (across <= length and size + 4 <= thickness) if layout.horizontal else (
            size + 6 <= length and across <= thickness)

    if layout.horizontal:
        category_size, lines = _wrap_all(layout.categories, size, layout.width * 0.38 - 14)
        label_width = max(_text_width(line, category_size) for wrapped in lines for line in wrapped) + 14
        zero = layout.x0 + label_width
        extent = layout.width - label_width - 4
        band = (bottom - top) / len(layout.categories)
        thickness = min(band * 0.6, 64)
        if extent < 80 or band < max(category_size * 1.25 * max(map(len, lines)), size + 6):
            raise ServerChartUnsupported("server_chart_box_too_small")
        _element(node, "line", x1=_fmt(zero), y1=_fmt(top), x2=_fmt(zero), y2=_fmt(bottom), stroke=_AXIS,
                 stroke_width="2")
        for c_index, wrapped in enumerate(lines):
            center = top + band * (c_index + 0.5)
            first_baseline = center + category_size * 0.35 - (len(wrapped) - 1) * category_size * 0.625
            _text(node, wrapped, layout.x0, first_baseline, size=category_size, font=font, fill=ink)
            bar_y = center - thickness / 2
            for s_index, offset, length in segments(c_index, extent):
                color = layout.colors[s_index][c_index]
                _element(node, "rect", x=_fmt(zero + offset), y=_fmt(bar_y), width=_fmt(length),
                         height=_fmt(thickness), fill=color)
                if not show_values:
                    continue
                label = layout.labels[s_index][c_index]
                fits = fits and label_fits(label, length, thickness)
                x, anchor = {"center": (zero + offset + length / 2, "middle"),
                             "insideEnd": (zero + offset + length - 4, "end"),
                             "insideBase": (zero + offset + 4, "start")}[position]
                _text(node, label, x, center + size * 0.35, size=size, font=font, fill=_contrast(color),
                      anchor=anchor, weight="bold")
        return fits

    band = layout.width / len(layout.categories)
    category_size, lines = _wrap_all(layout.categories, size, band - 8)
    zero = bottom - (category_size * 1.25 * max(map(len, lines)) + 10)
    extent = zero - top - 4
    thickness = min(band * 0.6, 72)
    if extent < 60:
        raise ServerChartUnsupported("server_chart_box_too_small")
    _element(node, "line", x1=_fmt(layout.x0), y1=_fmt(zero), x2=_fmt(layout.x0 + layout.width), y2=_fmt(zero),
             stroke=_AXIS, stroke_width="2")
    for c_index, wrapped in enumerate(lines):
        center = layout.x0 + band * (c_index + 0.5)
        _text(node, wrapped, center, zero + category_size + 6, size=category_size, font=font, fill=ink,
              anchor="middle")
        bar_x = center - thickness / 2
        for s_index, offset, length in segments(c_index, extent):
            color = layout.colors[s_index][c_index]
            _element(node, "rect", x=_fmt(bar_x), y=_fmt(zero - offset - length), width=_fmt(thickness),
                     height=_fmt(length), fill=color)
            if not show_values:
                continue
            label = layout.labels[s_index][c_index]
            fits = fits and label_fits(label, length, thickness)
            y = {"center": zero - offset - length / 2 + size * 0.35,
                 "insideEnd": zero - offset - length + size + 2,
                 "insideBase": zero - offset - 4}[position]
            _text(node, label, center, y, size=size, font=font, fill=_contrast(color), anchor="middle",
                  weight="bold")
    return fits


def render_server_chart_v1(root: ET.Element, node: ET.Element, raw: dict) -> list[float]:
    """Revision one: replace ``node``'s children with the chart ``raw`` declares, inside its box.

    Returns the box. Raises :class:`ServerChartUnsupported` (and leaves
    ``node`` empty) when this revision cannot draw the declaration. ``raw`` is
    normalised to what was drawn, so the export and the preview read one source.
    """
    for child in list(node):
        node.remove(child)
    layout = _Layout(root, node, raw)
    box = [layout.x0, layout.y0, layout.width, layout.height]
    for key in (RENDER_ATTRIBUTE, BOX_ATTRIBUTE):
        node.attrib.pop(key, None)
    labels = raw.get("valueLabels") if isinstance(raw.get("valueLabels"), dict) else {}
    show_values = labels.get("shown", True) is not False
    position = labels.get("position") if labels.get("position") in _POSITIONS else "outsideEnd"
    if layout.grouping in _STACKED:
        # Stack labels sit on their segment (center/insideEnd/insideBase only).
        position = position if position in ("center", "insideEnd", "insideBase") else "center"
        try:
            if not _draw_stacked(layout, node, position, show_values) and show_values:
                # A segment too short for its label: the stack is drawn without value labels.
                for child in list(node):
                    node.remove(child)
                show_values = False
                _draw_stacked(layout, node, position, show_values)
        except ServerChartUnsupported:
            for child in list(node):
                node.remove(child)
            raise
        return _normalised(raw, layout, show_values, position, box)
    if len(layout.series) > 1:
        position = "outsideEnd"
    try:
        fits = _draw(layout, node, position, show_values)
        if not fits:
            # An inside label wider than its bar would be unreadable: every label moves outside.
            for child in list(node):
                node.remove(child)
            position = "outsideEnd"
            _draw(layout, node, position, show_values)
    except ServerChartUnsupported:
        for child in list(node):
            node.remove(child)
        raise
    return _normalised(raw, layout, show_values, position, box)


def _normalised(raw: dict, layout: _Layout, show_values: bool, position: str, box: list[float]) -> list[float]:
    """Write what was drawn back into ``raw``, so the export and the preview read one source."""
    raw["valueLabels"] = {"shown": show_values, "position": position}
    raw["categoryOrder"] = "topToBottom" if layout.horizontal else "leftToRight"
    raw["grouping"] = layout.grouping
    raw["markers"] = False
    if layout.title:
        raw["title"] = layout.title
    for item, colors in zip(layout.series, layout.colors, strict=True):
        item["pointColors"] = list(colors)
    return box


#: Published renderer revisions. A revision's output never changes once published.
RENDERERS = {SERVER_CHART_REVISION: render_server_chart_v1}


def render_server_chart(root: ET.Element, node: ET.Element, raw: dict) -> tuple[str, list[float]]:
    """Draw ``node`` with the renderer its marker names; ``(revision, box)``."""
    revision = node.get(RENDER_ATTRIBUTE)
    renderer = RENDERERS.get(revision)
    if renderer is None:
        node.attrib.pop(RENDER_ATTRIBUTE, None)
        node.attrib.pop(BOX_ATTRIBUTE, None)
        raise ServerChartUnsupported("server_chart_render_invalid")
    return revision, renderer(root, node, raw)


def placeholder_view(svg: str, declarations: dict | None) -> str:
    """``svg`` with each server-drawn chart shown again as its placeholder.

    A correction attempt shows the model its previous SVG. Without this the
    model sees the server's bars and labels, copies them as plain shapes and
    the chart loses its declaration. Only the prompt view changes; the stored
    SVG and every digest stay canonical. ``svg`` is returned unchanged when
    nothing applies or it cannot be parsed.
    """
    objects = [item for item in (declarations or {}).get("objects") or []
               if isinstance(item, dict) and item.get("render") in RENDERERS and isinstance(item.get("box"), list)]
    if not objects or not isinstance(svg, str):
        return svg
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    groups = {node.get("id"): node for node in root.iter(_SVG + "g") if node.get("id")}
    changed = False
    for item in objects:
        node = groups.get(item.get("id"))
        if node is None:
            continue
        for child in list(node):
            node.remove(child)
        node.set("data-native-chart", json.dumps(item["raw"], ensure_ascii=False, separators=(",", ":")))
        node.set(RENDER_ATTRIBUTE, item["render"])
        node.set(BOX_ATTRIBUTE, " ".join(_fmt(value) for value in item["box"]))
        changed = True
    return ET.tostring(root, encoding="unicode") if changed else svg


_SOURCE_NUMBER = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+(?:\.\d+)?")


def source_numbers(text: str) -> set[Decimal]:
    """Every number the source states, thousands separators removed."""
    return {Decimal(match.replace(",", "")) for match in _SOURCE_NUMBER.findall(text or "")}


def _numbers_with_positions(text: str) -> list[tuple[int, int, Decimal]]:
    return [(match.start(), match.end(), Decimal(match.group().replace(",", "")))
            for match in _SOURCE_NUMBER.finditer(text)]


_NEAR_AFTER = 12
_NEAR_BEFORE = 5


def _spans(text: str, category: str, others: list[str]):
    """``(start, end, stop)`` per occurrence of ``category``: its span ends at the next
    occurrence of another category, a line break or ``;``."""
    for match in re.finditer(re.escape(category), text):
        start, end = match.start(), match.end()
        stops = [text.find(other, end) for other in others if other != category and other not in category]
        stops += [text.find(mark, end) for mark in ("\n", ";")]
        yield start, end, min([position for position in stops if position >= 0] or [len(text)])


def _near_after(numbers, anchor: int, stop: int):
    following = [(begin, number) for begin, _, number in numbers if anchor <= begin < stop]
    return following[0][1] if following and following[0][0] - anchor <= _NEAR_AFTER else None


def _near_before(numbers, start: int):
    before = [number for _, finish, number in numbers if finish <= start and start - finish <= _NEAR_BEFORE]
    return before[-1] if before else None


def _value_first(text: str, categories: list[str], numbers) -> bool:
    """Whether ``text`` writes values before their names (``420억원 국내영업``), decided once per text.

    One pair alone is ambiguous: ``국내영업 315, 해외영업 420`` and
    ``315 국내영업, 420 해외영업`` both put 315 between the two names.
    """
    after = before = 0
    for category in categories:
        for start, end, stop in _spans(text, category, categories):
            after += _near_after(numbers, end, stop) is not None
            before += _near_before(numbers, start) is not None
    return before > after


def _associated(text: str, category: str, series: str | None, value: Decimal, others: list[str],
                numbers, value_first: bool) -> bool:
    """Whether ``text`` states ``value`` for ``category`` (and ``series``, when several).

    Within the category's span the value is the first number at most a few
    characters after the name (after the series name, with several series):
    ``국내영업 420``, ``국내영업이 420억원``. In a text that writes values first,
    it is the number just before the name instead.
    """
    for start, end, stop in _spans(text, category, others):
        if series is not None:
            anchors = [end + found.end() for found in re.finditer(re.escape(series), text[end:stop])]
            if any(_near_after(numbers, anchor, stop) == value for anchor in anchors):
                return True
        elif value_first:
            if _near_before(numbers, start) == value:
                return True
        elif _near_after(numbers, end, stop) == value:
            return True
    return False


def apply_server_chart_grounding(quality: dict, payload: dict, svg: str, sources: list[str] | str) -> None:
    """Fail a server-rendered chart whose values the source does not state for their category.

    The drawing is derived from the declaration, so the declaration's data is
    the only thing left to check: each value must be stated next to its
    category (and its series, with several series) in one of ``sources``.
    Derived numbers (shares, totals) are not charted by this revision.
    """
    from services.generation_core.drivers.svg.native_declarations import bound_declarations

    record, current = bound_declarations(payload, svg)
    if record is None or not current:
        return
    texts = [sources] if isinstance(sources, str) else [text for text in sources if isinstance(text, str)]
    failures = []
    for item in record.get("objects") or []:
        if not isinstance(item, dict) or item.get("render") not in RENDERERS:
            continue
        categories = item["raw"].get("categories", [])
        series = item["raw"].get("series", [])
        missing = []
        views = [(text, _numbers_with_positions(text)) for text in texts]
        views = [(text, numbers, _value_first(text, categories, numbers)) for text, numbers in views]
        names = [item_series.get("name") for item_series in series]
        for item_series in series:
            name = item_series.get("name") if len(series) > 1 else None
            for category, value in zip(categories, item_series.get("values", []), strict=False):
                number = _number(value)
                # With several series the source may name either first: "A사 2025년 61" or
                # "2025년 A사 61" (category and series swap roles).
                if not any(_associated(text, category, name, number, categories, numbers, first)
                           or (name is not None
                               and _associated(text, name, category, number, names, numbers, False))
                           for text, numbers, first in views):
                    missing.append(f"{category}={_label(value)}" if name is None else f"{name}/{category}={_label(value)}")
        if missing:
            failures.append({"kind": "native_requested_chart_missing", "declarationId": item.get("id"),
                             "reason": "server_chart_value_ungrounded", "values": missing})
    if failures:
        quality["passed"] = False
        quality.setdefault("failures", []).extend(failures)


__all__ = ["BOX_ATTRIBUTE", "RENDERERS", "RENDER_ATTRIBUTE", "SERVER_CHART_REVISION", "SERVER_CHART_RULE",
           "SNAPSHOT_KEY", "ServerChartUnsupported", "apply_server_chart_grounding", "placeholder_view",
           "render_server_chart", "render_server_chart_v1", "server_chart_policy", "server_chart_rule",
           "source_numbers"]
