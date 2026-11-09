"""Server chart renderer revision four (``svg-server-chart/v4``): trusted, with a designed look.

Revision three draws what revision two draws (a plain axis, labels that may collide, a
fixed palette) and refuses what its layout cannot place. Revision four is trusted by
construction as well (``TRUSTED_RENDERERS``), so it is no longer bound by what the
native drawing verifier can read, and it draws a better chart:

* **Value axis.** A zero-based axis with "nice" 1-2-5 tick values and light gridlines
  (horizontal for column, line and area; vertical for bar). Rule: ticks and gridlines are
  drawn for line and area charts, for clustered or stacked charts of two or more series,
  for charts of seven or more categories, and whenever the value labels cannot be shown
  (the author hid them, or they would collide). A single series of at most six categories
  keeps clean bars with value labels and no axis. Tick labels carry ``%`` only for a
  ``(%)`` unit or a percent-stacked chart; other units stay in the title.
* **Value labels never collide.** Every label is placed against the bars, points, line
  segments and labels already drawn; several lines get labels beside their points (above or
  below, whichever is free), else at their line ends, else none (the axis carries them).
* **Palette from the deck.** The page's accent colours (saturated fills and strokes weighted
  by painted area, backgrounds excluded) lead the series and slice colours, followed by
  tints, shades and neutrals of them. The palette is recorded in the replay style
  (``style.palette``), so the drawing replays from the stored record alone.
* **Fallbacks instead of refusals.** The whole chart shrinks from the page body size to the
  14 px floor before it is refused; category names wrap to three lines (at spaces, or between
  Korean/Chinese characters) and are never rotated, abbreviated or changed; a pie or donut
  whose slice names would collide, or with more than six slices, gets a legend (swatch,
  name, value) beside it instead of being refused; zero values draw in clustered bar and
  column charts.
* A title or legend entry wider than the box wraps instead of overflowing.

Refusals that remain are undrawable data and boxes: negative values (the source grounding
cannot read a sign), zero or negative stacked segments and pie slices (no extent), a pie with
more than one series or eight slices, a trend with one category, shape-invalid JSON, a box
below 160x120 px or too small for the chart even at 14 px, category names that cannot wrap
into three lines at the floor, and a legend entry wider than the box.
"""
from __future__ import annotations

import colorsys
import math
import re
from collections import Counter
from contextvars import ContextVar
from decimal import Decimal
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.server_chart import (
    BOX_ATTRIBUTE, RENDER_ATTRIBUTE, ServerChartUnsupported, _box, _clear, _contrast, _element, _fmt,
    _label, _label_unit, _number, _page_style, _text, _text_width, _FAMILIES, _POSITIONS, _STACKED, _SVG,
)

#: Whether value labels keep each value's decimals as written (``9.0`` stays ``9.0``). Off for
#: revisions four to six (their published output); revision seven turns it on while it draws
#: and while its native style is derived (``server_chart.AS_WRITTEN_RENDERERS``).
AS_WRITTEN = ContextVar("server_chart_labels_as_written", default=False)


def value_label(value) -> str:
    """A value label's number: revision one's form, or the declaration's own digits under ``AS_WRITTEN``."""
    if not AS_WRITTEN.get():
        return _label(value)
    number = _number(value)
    text = format(number, "f")
    sign = "-" if text.startswith("-") else ""
    whole, _, fraction = text.lstrip("-").partition(".")
    if len(whole) > 3:
        whole = f"{int(whole):,}"
    return sign + whole + (f".{fraction}" if fraction else "")


_ALL_FAMILIES = _FAMILIES | {"line", "area", "pie", "donut"}
_TREND = ("line", "area")
_POLAR = ("pie", "donut")
_FLOOR = 14.0
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
_DONUT_HOLE = 0.58
_AREA_OPACITY = "0.3"
_BOLD = 1.07
_MAX_PALETTE = 8
_FALLBACK_ACCENT = "#2563EB"
_NEUTRALS = ("#64748B", "#94A3B8")
_TOPUP = ("#38BDF8", "#1E3A8A", "#0F172A", "#0EA5E9", "#CBD5E1")
#: Pie and donut keep names beside their slices up to this many slices; more get a legend.
_DIRECT_SLICES = 6
#: Several lines label every point up to this many points, else only their ends.
_LABELED_POINTS = 12
_CJK = re.compile(r"[ᄀ-ᇿ぀-ヿ㄰-㆏一-鿿가-힯]")


class _Retry(ServerChartUnsupported):
    """This layout does not fit; try the next configuration or size."""


# --- Style: the page's font, ink, body size and palette ------------------------------------

def _hex(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if _HEX.fullmatch(value):
        return value.upper()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", value):
        return "#" + "".join(part * 2 for part in value[1:]).upper()
    return None


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _distance(first: str, second: str) -> float:
    return math.dist(_rgb(first), _rgb(second))


def _shift(color: str, delta: float) -> str:
    """``color`` with its HLS lightness moved by ``delta`` (clamped), same hue and saturation."""
    hue, light, sat = colorsys.rgb_to_hls(*(part / 255 for part in _rgb(color)))
    red, green, blue = colorsys.hls_to_rgb(hue, min(max(light + delta, 0.12), 0.86), sat)
    return "#{:02X}{:02X}{:02X}".format(round(red * 255), round(green * 255), round(blue * 255))


def _colorful(color: str) -> bool:
    _, light, sat = colorsys.rgb_to_hls(*(part / 255 for part in _rgb(color)))
    return sat >= 0.3 and 0.2 <= light <= 0.72


def _canvas_area(root: ET.Element) -> float:
    numbers = [float(part) for part in re.findall(r"[-+]?\d*\.?\d+", root.get("viewBox", ""))]
    try:
        width, height = (numbers[2], numbers[3]) if len(numbers) == 4 else (
            float(root.get("width", 0)), float(root.get("height", 0)))
    except ValueError:
        return 0.0
    return width * height


def _painted_area(node: ET.Element, tag: str) -> float:
    def number(key):
        try:
            return float(node.get(key, "0"))
        except ValueError:
            return 0.0
    if tag == "rect":
        return max(number("width"), 0.0) * max(number("height"), 0.0)
    if tag == "circle":
        return math.pi * number("r") ** 2
    if tag == "ellipse":
        return math.pi * number("rx") * number("ry")
    return 400.0 if tag in ("path", "polygon", "polyline") else 100.0 if tag == "line" else 150.0


def _accents(root: ET.Element, chart: ET.Element) -> list[str]:
    """The page's accent colours, most painted first: saturated, mid-light, not a background.

    Deterministic in document content only (weights are painted areas, ties break by hex).
    """
    inside = set(chart.iter())
    canvas = _canvas_area(root)
    weights: Counter = Counter()
    for node in root.iter():
        if node in inside or not isinstance(node.tag, str):
            continue
        tag = node.tag.removeprefix(_SVG)
        if tag not in ("rect", "circle", "ellipse", "path", "polygon", "polyline", "line", "text"):
            continue
        area = _painted_area(node, tag)
        if not math.isfinite(area) or (canvas and area > canvas * 0.5):
            continue
        for key in ("fill", "stroke"):
            color = _hex(node.get(key))
            if color and _colorful(color) and not (key == "fill" and node.get("fill-opacity") == "0"):
                weights[color] += area if key == "fill" else min(area, 200.0)
    chosen: list[str] = []
    for color, _weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
        if all(_distance(color, other) >= 60 for other in chosen):
            chosen.append(color)
        if len(chosen) == 3:
            break
    return chosen


def _palette(accents: list[str]) -> tuple[str, ...]:
    """Eight distinct colours: accents, a neutral, then shades and tints of the lead accent."""
    lead = accents or [_FALLBACK_ACCENT]
    candidates = [*lead, _NEUTRALS[0], _shift(lead[0], -0.2), _shift(lead[0], 0.2), _NEUTRALS[1]]
    for accent in lead[1:]:
        candidates += [_shift(accent, -0.2), _shift(accent, 0.2)]
    candidates += _TOPUP
    palette: list[str] = []
    for color in candidates:
        if all(_distance(color, other) >= 50 for other in palette):
            palette.append(color)
        if len(palette) == _MAX_PALETTE:
            break
    return tuple(palette)


#: A layout decision the normalised declaration cannot express: which labels a line chart carries.
#: Several lines labelled at their ends record ``valueLabels.shown`` False, like no labels at all,
#: so the drawing records its choice in the replay style and a replay forces it.
_TREND_LABEL_MODES = ("all", "end", "none")


def page_style(root: ET.Element, node: ET.Element) -> tuple[str, str, float, tuple[str, ...], dict]:
    """``(font, ink, body size, palette, decisions)`` of the page ``node`` sits in.

    ``decisions`` is empty on a first drawing; the renderer records what it chose in it.
    """
    font, ink, size = _page_style(root, node)
    return font, ink, size, _palette(_accents(root, node)), {}


def style_record(style) -> dict:
    """The replay style stored beside the declaration (call after the drawing)."""
    font, ink, size, palette, decisions = style
    record = {"font": font, "ink": ink, "size": size, "palette": list(palette)}
    if decisions.get("trendLabels"):
        record["trendLabels"] = decisions["trendLabels"]
    return record


def replay_style(record) -> tuple[str, str, float, tuple[str, ...], dict] | None:
    """The style tuple of a stored record, or ``None`` when it is not a v4 style."""
    base = {"font", "ink", "size", "palette"}
    if (not isinstance(record, dict) or set(record) not in (base, base | {"trendLabels"})
            or not isinstance(record["font"], str) or not isinstance(record["ink"], str)
            or isinstance(record["size"], bool) or not isinstance(record["size"], (int, float))
            or not math.isfinite(record["size"]) or not isinstance(record["palette"], list)
            or not 1 <= len(record["palette"]) <= _MAX_PALETTE
            or not all(isinstance(color, str) and _HEX.fullmatch(color) for color in record["palette"])
            or record.get("trendLabels", "all") not in _TREND_LABEL_MODES):
        return None
    decisions = {"trendLabels": record["trendLabels"]} if "trendLabels" in record else {}
    return record["font"], record["ink"], float(record["size"]), tuple(record["palette"]), decisions


# --- Validation -----------------------------------------------------------------------------

def _validated(raw: dict) -> tuple[str, list[str], list[dict], str]:
    """``(chart type, categories, series, grouping)``; raises for data this revision cannot draw."""
    chart_type = raw.get("chartType")
    if chart_type not in _ALL_FAMILIES:
        raise ServerChartUnsupported("server_chart_type_unsupported")
    grouping = raw.get("grouping")
    if grouping not in (None, "clustered", "stacked", "percentStacked"):
        raise ServerChartUnsupported("server_chart_grouping_unsupported")
    if grouping in _STACKED and chart_type not in _FAMILIES:
        raise ServerChartUnsupported("server_chart_grouping_unsupported")
    categories, series = raw.get("categories"), raw.get("series")
    if (not isinstance(categories, list) or not categories
            or not all(isinstance(item, str) and item.strip() for item in categories)
            or not isinstance(series, list) or not series):
        raise ServerChartUnsupported("server_chart_shape_invalid")
    for item in series:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
                or not isinstance(item.get("values"), list) or len(item["values"]) != len(categories)):
            raise ServerChartUnsupported("server_chart_shape_invalid")
    if grouping in _STACKED and len(series) < 2:
        raise ServerChartUnsupported("server_chart_stack_needs_series")
    values = [_number(value) for item in series for value in item["values"]]
    if any(value < 0 for value in values):
        # The source grounding reads unsigned numbers only, so a signed value is never provable.
        raise ServerChartUnsupported("server_chart_value_negative")
    if chart_type in _POLAR:
        if len(series) != 1:
            raise ServerChartUnsupported("server_chart_polar_needs_one_series")
        if len(categories) > _MAX_PALETTE:
            raise ServerChartUnsupported("server_chart_too_many_slices")
        if any(value <= 0 for value in values):
            raise ServerChartUnsupported("server_chart_value_not_positive")
    elif grouping in _STACKED:
        if any(value <= 0 for value in values):
            # The native stack contract has no zero segment.
            raise ServerChartUnsupported("server_chart_value_not_positive")
    elif not any(value > 0 for value in values):
        raise ServerChartUnsupported("server_chart_value_not_positive")
    if chart_type in _TREND and len(categories) < 2:
        raise ServerChartUnsupported("server_chart_trend_needs_categories")
    return chart_type, categories, series, grouping if grouping in _STACKED else "clustered"


# --- Text fitting ----------------------------------------------------------------------------

def _bold_width(text: str, size: float) -> float:
    return _text_width(text, size) * _BOLD


def _fit_lines(text: str, size: float, width: float, max_lines: int, *, bold: bool = False) -> list[str] | None:
    """``text`` in at most ``max_lines`` lines no wider than ``width``; ``None`` if impossible.

    Lines break at spaces; a word wider than ``width`` breaks between characters only when it
    is all Korean/Chinese/Japanese. The text itself is never changed.
    """
    def measure(part):
        return _bold_width(part, size) if bold else _text_width(part, size)
    if measure(text) <= width:
        return [text]
    pieces: list[tuple[str, str]] = []
    for index, word in enumerate(text.split(" ")):
        separator = " " if index else ""
        if measure(word) <= width:
            pieces.append((word, separator))
        elif all(_CJK.fullmatch(char) for char in word):
            pieces += [(char, separator if position == 0 else "") for position, char in enumerate(word)]
        else:
            return None
    lines: list[str] = []
    current = ""
    for part, separator in pieces:
        trial = current + (separator if current else "") + part
        if current and measure(trial) > width:
            lines.append(current)
            current = part
        else:
            current = trial
    lines.append(current)
    return lines if len(lines) <= max_lines else None


def _sizes(size: float) -> list[float]:
    found, current = [], size
    while current > _FLOOR:
        found.append(current)
        current -= 1
    return [*found, _FLOOR]


# --- Value axis ------------------------------------------------------------------------------

def _nice_step(high: float, max_intervals: int) -> tuple[Decimal, int]:
    """The smallest 1-2-5 step whose axis (a whole number of steps) covers ``high`` in at most
    ``max_intervals`` intervals, and that number of intervals."""
    exponent = math.floor(math.log10(high)) - 2
    while True:
        for mantissa in (1, 2, 5):
            step = Decimal(mantissa).scaleb(exponent)
            intervals = math.ceil(Decimal(repr(high)) / step)
            if intervals <= max_intervals:
                return step, max(intervals, 1)
        exponent += 1


def _ticks(high: float, max_intervals: int, suffix: str) -> tuple[Decimal, list[tuple[float, str]]]:
    step, intervals = _nice_step(high, max(max_intervals, 2))
    return step * intervals, [(float(step * index), _label(str(step * index)) + suffix)
                              for index in range(intervals + 1)]


# --- Collisions ------------------------------------------------------------------------------

Box = tuple[float, float, float, float]


class _Ink:
    """What is already drawn inside the chart box: label placement checks against it."""

    def __init__(self, bounds: Box):
        self.bounds = bounds
        self.boxes: list[Box] = []
        self.segments: list[tuple[float, float, float, float]] = []

    def hits(self, box: Box) -> bool:
        x0, y0, x1, y1 = box
        bx0, by0, bx1, by1 = self.bounds
        if x0 < bx0 or y0 < by0 or x1 > bx1 or y1 > by1:
            return True
        if any(x0 < o[2] + 1 and o[0] - 1 < x1 and y0 < o[3] + 1 and o[1] - 1 < y1 for o in self.boxes):
            return True
        for sx0, sy0, sx1, sy1 in self.segments:
            length = math.hypot(sx1 - sx0, sy1 - sy0)
            steps = max(1, math.ceil(length / 3))
            for step in range(steps + 1):
                x = sx0 + (sx1 - sx0) * step / steps
                y = sy0 + (sy1 - sy0) * step / steps
                if x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
                    return True
        return False

    def add(self, box: Box) -> None:
        self.boxes.append(box)


def _label_box(text: str, size: float, x: float, y: float, anchor: str) -> Box:
    width = _bold_width(text, size) + 2
    left = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
    return left, y - size * 0.8, left + width, y + size * 0.25


# --- Parameters shared by every drawing -----------------------------------------------------

class _Plan:
    """The declaration and style, resolved once: names, colours, labels, title and unit."""

    def __init__(self, node, raw, style):
        self.x0, self.y0, self.width, self.height = _box(node)
        self.chart_type, self.categories, self.series, self.grouping = _validated(raw)
        self.font, self.ink, self.body, self.palette, self.decisions = style
        title = raw.get("title")
        if not (isinstance(title, str) and title.strip()) and len(self.series) == 1:
            title = self.series[0]["name"]
        self.title = title if isinstance(title, str) and title.strip() else None
        self.unit = _label_unit(self.title, self.series)
        self.values = [[float(_number(value)) for value in item["values"]] for item in self.series]
        if not all(math.isfinite(value) for row in self.values for value in row):
            raise ServerChartUnsupported("server_chart_value_invalid")
        self.labels = [[value_label(value) + self.unit for value in item["values"]] for item in self.series]
        declared_labels = raw.get("valueLabels") if isinstance(raw.get("valueLabels"), dict) else {}
        self.wants_labels = declared_labels.get("shown", True) is not False
        self.position_preference = declared_labels.get("position")
        self.colors = self._colors(raw)
        self.percent = self.grouping == "percentStacked"
        self.suffix = "%" if self.unit == "%" or self.percent else ""
        #: Furniture boxes of the current drawing attempt (title, legend), for the native style.
        self.drawn: dict = {}

    def _colors(self, raw) -> list[list[str]]:
        count = len(self.categories)
        if self.chart_type in _POLAR:
            declared = self.series[0].get("pointColors")
            if (isinstance(declared, list) and len(declared) == count and len(set(declared)) == count
                    and all(isinstance(color, str) and _HEX.fullmatch(color) for color in declared)):
                return [list(declared)]
            return [list(self.palette[:count])]
        colors = []
        for index, item in enumerate(self.series):
            declared = item.get("pointColors")
            if self.grouping in _STACKED and isinstance(declared, list) and declared:
                declared = [declared[0]] * count
            if not (isinstance(declared, list) and len(declared) == count
                    and all(isinstance(color, str) and _HEX.fullmatch(color) for color in declared)):
                declared = [self.palette[index % len(self.palette)]] * count
            colors.append(declared)
        return colors

    @property
    def high(self) -> float:
        """The largest extent the value axis must cover (stack total, 100 for percent)."""
        if self.percent:
            return 100.0
        if self.grouping in _STACKED:
            return max(sum(column) for column in zip(*self.values, strict=True))
        return max(max(item) for item in self.values)

    def text(self, g, lines, x, y, size, *, fill=None, anchor="start", weight=None):
        return _text(g, lines, x, y, size=size, font=self.font, fill=fill or self.ink, anchor=anchor,
                     weight=weight)

    def title_block(self, g, size: float) -> float:
        """Draw the title (at most two lines) and return the next free y."""
        self.drawn = {}
        if not self.title:
            return self.y0
        title_size = size + 2
        lines = _fit_lines(self.title, title_size, self.width, 2, bold=True)
        if lines is None:
            raise _Retry("server_chart_title_too_wide")
        _text(g, lines, self.x0, self.y0 + title_size, size=title_size, font=self.font, fill=self.ink,
              weight="bold", line_height=title_size * 1.25)
        bottom = self.y0 + title_size + (len(lines) - 1) * title_size * 1.25 + 14
        self.drawn["title"] = (self.x0, self.y0, max(_bold_width(line, title_size) for line in lines),
                               bottom - 14 + title_size * 0.3 - self.y0, title_size)
        return bottom

    def legend(self, g, size: float, top: float) -> float:
        if len(self.series) < 2:
            return top
        row_height = size + 10
        cursor, row, used = self.x0, 0, 0.0
        for index, item in enumerate(self.series):
            width = 20 + _text_width(item["name"], size)
            if width > self.width:
                raise _Retry("server_chart_legend_too_wide")
            if cursor > self.x0 and cursor + width > self.x0 + self.width:
                cursor, row = self.x0, row + 1
            y = top + row * row_height
            _element(g, "rect", x=_fmt(cursor), y=_fmt(y), width="14", height="14",
                     fill=self.colors[index][0])
            self.text(g, item["name"], cursor + 20, y + 13, size)
            used = max(used, cursor + width - self.x0)
            cursor += width + 24
        self.drawn["legend"] = (self.x0, top, used, (row + 1) * row_height)
        return top + (row + 1) * row_height + 4


def _gridline(g, ink, x1, y1, x2, y2, *, baseline=False):
    line = _element(g, "line", x1=_fmt(x1), y1=_fmt(y1), x2=_fmt(x2), y2=_fmt(y2), stroke=ink,
                    stroke_width="2" if baseline else "1")
    line.set("stroke-opacity", "0.45" if baseline else "0.16")


# --- Bar, column (clustered and stacked) -------------------------------------------------------

def _bar_position(plan: _Plan, single: bool) -> str:
    preference = plan.position_preference
    if plan.grouping in _STACKED:
        return preference if preference in ("center", "insideEnd", "insideBase") else "center"
    return preference if single and preference in _POSITIONS else "outsideEnd"


def _default_axis(plan: _Plan) -> bool:
    return len(plan.series) >= 2 or len(plan.categories) >= 7 or not plan.wants_labels


def _bars(plan: _Plan, size: float, g, *, axis: bool, labels: bool) -> dict:
    """Draw a bar or column chart; ``_Retry`` when it does not fit at ``size`` or its labels collide."""
    horizontal = plan.chart_type == "bar"
    stacked = plan.grouping in _STACKED
    count = len(plan.series)
    ncat = len(plan.categories)
    line_height = size * 1.25
    top = plan.legend(g, size, plan.title_block(g, size))
    x_end, y_end = plan.x0 + plan.width, plan.y0 + plan.height
    longest = max((text for row in plan.labels for text in row), key=lambda text: _bold_width(text, size))
    value_room = _bold_width(longest, size) + 12

    if horizontal:
        limit = plan.width * 0.38 - 14
        wrapped = [_fit_lines(name, size, limit, 3) for name in plan.categories]
        if any(item is None for item in wrapped):
            raise _Retry("server_chart_category_labels_too_wide")
        category_w = max(_text_width(line, size) for item in wrapped for line in item) + 14
        left = plan.x0 + category_w
        right = x_end - (value_room if labels and not stacked else 4)
        bottom = y_end - (size + 12 if axis else 0)
        extent = right - left
        band = (bottom - top) / ncat
        if extent < 80 or band < max(line_height * max(map(len, wrapped)), size + 6):
            raise _Retry("server_chart_box_too_small")
        max_intervals = 5
        for _ in range(4):
            axis_max, ticks = _ticks(plan.high, max_intervals, plan.suffix)
            widest = max(_text_width(text, size) for _, text in ticks)
            fitting = max(2, min(6, int(extent / (widest + 22))))
            if fitting >= max_intervals:
                break
            max_intervals = fitting
        zero = left
    else:
        max_intervals = 5
        axis_w = 0.0
        for _ in range(4):
            axis_max, ticks = _ticks(plan.high, max_intervals, plan.suffix)
            axis_w = max(_text_width(text, size) for _, text in ticks) + 12 if axis else 0.0
            left, right = plan.x0 + axis_w, x_end
            band = (right - left) / ncat
            wrapped = [_fit_lines(name, size, band - 8, 3) for name in plan.categories]
            if any(item is None for item in wrapped):
                raise _Retry("server_chart_category_labels_too_wide")
            bottom = y_end - (line_height * max(map(len, wrapped)) + 10)
            plot_top = top + (size + 10 if labels and not stacked else 6)
            extent = bottom - plot_top
            if extent < 60:
                raise _Retry("server_chart_box_too_small")
            fitting = max(2, min(6, int(extent / (size * 2.3))))
            if not axis or fitting >= max_intervals:
                break
            max_intervals = fitting
        zero = bottom
    if not axis:
        # Without an axis the longest bar fills the plot.
        axis_max = Decimal(repr(plan.high))
    scale = extent / float(axis_max)
    # Gridlines and the axis come first; the bars rest on them.
    if axis:
        for value, text in ticks:
            along = value * scale
            if horizontal:
                x = zero + along
                if value:
                    _gridline(g, plan.ink, x, top, x, bottom)
                anchor = "end" if x + _text_width(text, size) / 2 > x_end else "middle"
                plan.text(g, text, x, bottom + size + 4, size, anchor=anchor)
            else:
                y = zero - along
                if value:
                    _gridline(g, plan.ink, left, y, right, y)
                plan.text(g, text, left - 8, y + size * 0.35, size, anchor="end")
    if horizontal:
        _gridline(g, plan.ink, zero, top, zero, bottom, baseline=True)
    else:
        _gridline(g, plan.ink, left, zero, right, zero, baseline=True)

    slots = 1 if stacked else count
    thickness = min(band * (0.6 if stacked else 0.72) / slots, 64 if horizontal else 72)
    ink = _Ink((plan.x0, plan.y0, x_end, y_end))
    position = _bar_position(plan, count == 1)

    def inside_fits(length, lw):
        if horizontal:
            return length >= lw + 12 and thickness >= size + 4
        return length >= size + 8 and thickness >= lw + 6

    pending = []  # (text, color, a0, a1, across center, slot center)
    for c_index, name_lines in enumerate(wrapped):
        center = (top if horizontal else left) + band * (c_index + 0.5)
        if horizontal:
            first = center + size * 0.35 - (len(name_lines) - 1) * size * 0.625
            _text(g, name_lines, plan.x0, first, size=size, font=plan.font, fill=plan.ink)
        else:
            _text(g, name_lines, center, zero + size + 6, size=size, font=plan.font, fill=plan.ink,
                  anchor="middle")
        offset = 0.0
        total = sum(plan.values[s][c_index] for s in range(count))
        for s_index in range(count):
            value = plan.values[s_index][c_index]
            if stacked:
                span = value * (100.0 / total if plan.percent else 1.0)
                a0, a1 = offset * scale, (offset + span) * scale
                offset += span
                across = center - thickness / 2
            else:
                a0, a1 = 0.0, value * scale
                across = center - thickness * slots / 2 + thickness * s_index
            color = plan.colors[s_index][c_index]
            if a1 - a0 > 0:
                if horizontal:
                    _element(g, "rect", x=_fmt(zero + a0), y=_fmt(across), width=_fmt(a1 - a0),
                             height=_fmt(thickness), fill=color)
                    ink.add((zero + a0, across, zero + a1, across + thickness))
                else:
                    _element(g, "rect", x=_fmt(across), y=_fmt(zero - a1), width=_fmt(thickness),
                             height=_fmt(a1 - a0), fill=color)
                    ink.add((across, zero - a1, across + thickness, zero - a0))
            pending.append((plan.labels[s_index][c_index], color, a0, a1, across + thickness / 2, thickness))

    if labels:
        if position != "outsideEnd" and not all(
                inside_fits(a1 - a0, _bold_width(text, size)) for text, _, a0, a1, _, _ in pending):
            # An inside label wider than its bar would be unreadable: every label moves outside.
            if stacked:
                raise _Retry("server_chart_labels_collide")
            position = "outsideEnd"
        for text, color, a0, a1, across, _ in pending:
            if horizontal:
                y = across + size * 0.35
                x, fill, anchor = {
                    "outsideEnd": (zero + a1 + 8, plan.ink, "start"),
                    "insideEnd": (zero + a1 - 8, _contrast(color), "end"),
                    "insideBase": (zero + a0 + 8, _contrast(color), "start"),
                    "center": (zero + (a0 + a1) / 2, _contrast(color), "middle")}[position]
            else:
                x = across
                y, fill = {
                    "outsideEnd": (zero - a1 - 8, plan.ink),
                    "insideEnd": (zero - a1 + size + 4, _contrast(color)),
                    "insideBase": (zero - a0 - 8, _contrast(color)),
                    "center": (zero - (a0 + a1) / 2 + size * 0.35, _contrast(color))}[position]
                anchor = "middle"
            box = _label_box(text, size, x, y, anchor)
            if position == "outsideEnd" and ink.hits(box):
                raise _Retry("server_chart_labels_collide")
            if position == "outsideEnd":
                ink.add(box)
            plan.text(g, text, x, y, size, fill=fill, anchor=anchor, weight="bold")
    if horizontal:
        plot = (zero, top, zero + extent, bottom)
    else:
        plot = (left, zero - extent, right, zero)
    return {"shown": labels, "position": position, "axis": axis, "axisMax": axis_max,
            "majorUnit": axis_max / (len(ticks) - 1) if axis else None, "plot": plot,
            "band": band, "thickness": thickness, "slots": slots, **plan.drawn}


def _draw_bars(plan: _Plan, node, size: float) -> dict:
    """Configurations in order: the default axis rule with labels, then an axis without labels."""
    configs = [(_default_axis(plan), plan.wants_labels)]
    if plan.wants_labels:
        configs.append((True, False))
    failure: ServerChartUnsupported | None = None
    for axis, labels in configs:
        scratch = ET.Element(_SVG + "g")
        try:
            info = _bars(plan, size, scratch, axis=axis, labels=labels)
        except _Retry as error:
            failure = error
            if str(error) == "server_chart_labels_collide":
                continue
            raise
        for child in list(scratch):
            node.append(child)
        return info
    raise failure or ServerChartUnsupported("server_chart_box_too_small")


# --- Line and area ---------------------------------------------------------------------------

def _trend(plan: _Plan, size: float, g, mode: str) -> dict:
    """A line or area chart with a value axis; ``mode`` is ``all``, ``end`` or ``none`` labels."""
    ncat = len(plan.categories)
    line_height = size * 1.25
    top = plan.legend(g, size, plan.title_block(g, size))
    x_end, y_end = plan.x0 + plan.width, plan.y0 + plan.height
    end_room = 0.0
    if mode == "end":
        end_room = max(_bold_width(item[-1], size) for item in plan.labels) + 16
        if end_room > plan.width * 0.25:
            raise _Retry("server_chart_labels_collide")
    max_intervals = 5
    for _ in range(4):
        axis_max, ticks = _ticks(plan.high, max_intervals, plan.suffix)
        axis_w = max(_text_width(text, size) for _, text in ticks) + 12
        left, right = plan.x0 + axis_w, x_end - end_room
        band = (right - left) / ncat
        wrapped = [_fit_lines(name, size, band - 8, 3) for name in plan.categories]
        if any(item is None for item in wrapped):
            raise _Retry("server_chart_category_labels_too_wide")
        category_h = line_height * max(map(len, wrapped)) + 10
        plot_top = top + (size + 10 if mode == "all" else 8 + size / 2)
        zero = y_end - category_h
        extent = zero - plot_top
        if extent < 60:
            raise _Retry("server_chart_box_too_small")
        fitting = max(2, min(6, int(extent / (size * 2.3))))
        if fitting >= max_intervals:
            break
        max_intervals = fitting
    scale = extent / float(axis_max)
    for value, text in ticks:
        y = zero - value * scale
        if value:
            _gridline(g, plan.ink, left, y, right, y)
        plan.text(g, text, left - 8, y + size * 0.35, size, anchor="end")
    _gridline(g, plan.ink, left, zero, right, zero, baseline=True)
    centers = [left + band * (index + 0.5) for index in range(ncat)]
    for center, name_lines in zip(centers, wrapped, strict=True):
        _text(g, name_lines, center, zero + size + 6, size=size, font=plan.font, fill=plan.ink, anchor="middle")
    points = [[(center, zero - value * scale) for center, value in zip(centers, item, strict=True)]
              for item in plan.values]
    area = plan.chart_type == "area"
    ink = _Ink((plan.x0, plan.y0, x_end, y_end))
    for s_index, series_points in enumerate(points):
        color = plan.colors[s_index][0]
        coordinates = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in series_points)
        if area:
            outline = [*series_points, (centers[-1], zero), (centers[0], zero)]
            _element(g, "polygon", points=" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in outline), fill=color,
                     fill_opacity=_AREA_OPACITY, stroke=color, stroke_width="2", stroke_linejoin="round")
        else:
            _element(g, "polyline", points=coordinates, fill="none", stroke=color, stroke_width="3",
                     stroke_linejoin="round", stroke_linecap="round")
        for first, second in zip(series_points, series_points[1:], strict=False):
            ink.segments.append((*first, *second))
        for x, y in series_points:
            ink.add((x - 5, y - 5, x + 5, y + 5))
            if not area:
                _element(g, "circle", cx=_fmt(x), cy=_fmt(y), r="4.5", fill=color, stroke="#FFFFFF",
                         stroke_width="1.5")
    if mode == "all":
        order = sorted(((c, s) for c in range(ncat) for s in range(len(points))),
                       key=lambda pair: (pair[0], points[pair[1]][pair[0]][1], pair[1]))
        for c_index, s_index in order:
            x, y = points[s_index][c_index]
            text = plan.labels[s_index][c_index]
            width = _bold_width(text, size)
            # Above first, then below, then beside the point on the side its line is not.
            for anchor, shift, baseline in (("middle", 0.0, y - 12), ("middle", 0.0, y + size + 8),
                                            ("end", -8.0, y - 10), ("start", 8.0, y - 10),
                                            ("end", -8.0, y + size + 6), ("start", 8.0, y + size + 6)):
                box = _label_box(text, size, x + shift, baseline, anchor)
                if width <= plan.width and not ink.hits(box):
                    ink.add(box)
                    plan.text(g, text, x + shift, baseline, size, anchor=anchor, weight="bold")
                    break
            else:
                raise _Retry("server_chart_labels_collide")
    elif mode == "end":
        wanted = sorted((points[s][-1][1], s) for s in range(len(points)))
        placed = []
        for y, s_index in wanted:
            y = max(y, plot_top - size * 0.2)
            if placed and y - placed[-1][0] < size + 3:
                y = placed[-1][0] + size + 3
            placed.append((y, s_index))
        if placed[-1][0] > zero:
            raise _Retry("server_chart_labels_collide")
        for y, s_index in placed:
            plan.text(g, plan.labels[s_index][-1], points[s_index][-1][0] + 10, y + size * 0.35, size,
                      weight="bold")
    # The native area plot takes no label position (the converter rejects one); a line labels above.
    return {"shown": mode == "all", "position": "above" if mode == "all" and not area else None,
            "axis": True, "markers": not area, "axisMax": axis_max, "majorUnit": axis_max / (len(ticks) - 1),
            "plot": (left, zero - extent, right, zero), "trendLabels": mode, **plan.drawn}


def _draw_trend(plan: _Plan, node, size: float) -> dict:
    modes = ["none"]
    if plan.decisions.get("trendLabels"):
        # A replay forces what the first drawing chose.
        modes = [plan.decisions["trendLabels"]]
    elif plan.wants_labels:
        if len(plan.series) == 1:
            modes = ["all", "none"]
        elif len(plan.series) * len(plan.categories) <= _LABELED_POINTS:
            modes = ["all", "end", "none"]
        else:
            # Many points: a label at the end of every line, the axis carries the rest.
            modes = ["end", "none"]
    failure: ServerChartUnsupported | None = None
    for mode in modes:
        scratch = ET.Element(_SVG + "g")
        try:
            info = _trend(plan, size, scratch, mode)
        except _Retry as error:
            failure = error
            if str(error) == "server_chart_labels_collide":
                continue
            raise
        for child in list(scratch):
            node.append(child)
        plan.decisions["trendLabels"] = mode
        return info
    raise failure or ServerChartUnsupported("server_chart_box_too_small")


# --- Pie and donut ---------------------------------------------------------------------------

def _slices(plan: _Plan, g, cx: float, cy: float, radius: float, colors: list[str]) -> list[tuple[float, float]]:
    hole = _DONUT_HOLE if plan.chart_type == "donut" else 0.0
    values = plan.values[0]
    total = sum(values)
    angle = -math.pi / 2
    middles = []
    for value, color in zip(values, colors, strict=True):
        sweep = 2 * math.pi * value / total
        steps = max(2, math.ceil(sweep / 0.05))
        arc = [angle + sweep * step / steps for step in range(steps + 1)]
        outline = [(cx + radius * math.cos(item), cy + radius * math.sin(item)) for item in arc]
        outline += ([(cx + radius * hole * math.cos(item), cy + radius * hole * math.sin(item))
                     for item in reversed(arc)] if hole else [(cx, cy)])
        _element(g, "polygon", points=" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in outline), fill=color)
        middles.append((angle + sweep / 2, sweep))
        angle += sweep
    return middles


def _polar_direct(plan: _Plan, size: float, g, inside: bool) -> bool:
    """Slice names (and outside values) beyond their slices; ``False`` when an inside value does
    not fit. ``_Retry`` when names would collide or the pie would be too small."""
    colors = plan.colors[0]
    top = plan.title_block(g, size)
    bottom = plan.y0 + plan.height
    show = plan.wants_labels
    outside = show and not inside
    line_height = size * 1.25
    widths = [max(_text_width(name, size), _bold_width(text, size) if outside else 0)
              for name, text in zip(plan.categories, plan.labels[0], strict=True)]
    block = line_height * (2 if outside else 1)
    gap = 14.0
    radius = min(plan.width / 2 - max(widths) - gap - 4, (bottom - top) / 2 - block - gap)
    if radius < 60:
        raise _Retry("server_chart_box_too_small")
    cx, cy = plan.x0 + plan.width / 2, (top + bottom) / 2
    plan.drawn["pie"] = (cx, cy, radius)
    middles = _slices(plan, g, cx, cy, radius, colors)
    boxes = []
    for (middle, _sweep), name, text, width in zip(middles, plan.categories, plan.labels[0], widths, strict=True):
        cos, sin = math.cos(middle), math.sin(middle)
        x, y = cx + (radius + gap) * cos, cy + (radius + gap) * sin
        anchor = "start" if cos > 0.25 else "end" if cos < -0.25 else "middle"
        center_y = y + sin * block / 2
        baseline = center_y - block / 2 + size * 0.85
        left = x if anchor == "start" else x - width if anchor == "end" else x - width / 2
        boxes.append((left, center_y - block / 2, left + width, center_y + block / 2))
        plan.text(g, name, x, baseline, size, anchor=anchor)
        if outside:
            plan.text(g, text, x, baseline + line_height, size, anchor=anchor, weight="bold")
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            if first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]:
                raise _Retry("server_chart_slice_labels_collide")
    if not inside or not show:
        return True
    hole = _DONUT_HOLE if plan.chart_type == "donut" else 0.0
    fits = True
    reach = radius * ((1 + hole) / 2 if hole else 0.62)
    for (middle, sweep), text, color in zip(middles, plan.labels[0], colors, strict=True):
        chord = 2 * reach * math.sin(min(sweep, math.pi) / 2)
        ring = radius * (1 - hole) if hole else reach
        fits = fits and chord >= _bold_width(text, size) + 8 and ring >= size + 6
        plan.text(g, text, cx + reach * math.cos(middle), cy + reach * math.sin(middle) + size * 0.35, size,
                  fill=_contrast(color), anchor="middle", weight="bold")
    return fits


def _polar_legend(plan: _Plan, size: float, g) -> None:
    """The pie or donut with a legend (swatch, name, value) beside it."""
    colors = plan.colors[0]
    top = plan.title_block(g, size)
    bottom = plan.y0 + plan.height
    region = bottom - top - 4
    show = plan.wants_labels
    line_height = size * 1.25
    value_w = max(_bold_width(text, size) for text in plan.labels[0]) if show else 0.0
    gap = 20.0 if show else 0.0
    name_limit = plan.width * 0.5 - 22 - gap - value_w
    names = [_fit_lines(name, size, name_limit, 2) for name in plan.categories]
    if any(item is None for item in names):
        raise _Retry("server_chart_legend_too_wide")
    legend_w = 22 + max(_text_width(line, size) for item in names for line in item) + gap + value_w
    rows = [max(len(item) * line_height, 14.0) + 10 for item in names]
    total = sum(rows)
    if total > region:
        raise _Retry("server_chart_box_too_small")
    radius = min((plan.width - legend_w - 40) / 2, region / 2) - 2
    if radius < 60:
        raise _Retry("server_chart_box_too_small")
    start = plan.x0 + (plan.width - (2 * radius + 40 + legend_w)) / 2
    cx, cy = start + radius, top + region / 2
    _slices(plan, g, cx, cy, radius, colors)
    legend_x = start + 2 * radius + 40
    y = cy - total / 2
    plan.drawn.update(pie=(cx, cy, radius), legend=(legend_x, y, legend_w, total))
    for item, row, color, text in zip(names, rows, colors, plan.labels[0], strict=True):
        middle = y + row / 2
        _element(g, "rect", x=_fmt(legend_x), y=_fmt(middle - 7), width="14", height="14", fill=color)
        first = middle - (len(item) - 1) * line_height / 2 + size * 0.35
        _text(g, item, legend_x + 22, first, size=size, font=plan.font, fill=plan.ink, line_height=line_height)
        if show:
            plan.text(g, text, legend_x + legend_w, first, size, anchor="end", weight="bold")
        y += row


def _draw_polar(plan: _Plan, node, size: float) -> dict:
    failure: ServerChartUnsupported | None = None
    if len(plan.categories) <= _DIRECT_SLICES:
        requested_outside = plan.position_preference == "outsideEnd"
        for inside in ([False] if requested_outside else [True, False]):
            scratch = ET.Element(_SVG + "g")
            try:
                fits = _polar_direct(plan, size, scratch, inside)
            except _Retry as error:
                failure = error
                break
            if not fits and inside and plan.wants_labels:
                continue
            for child in list(scratch):
                node.append(child)
            return {"shown": plan.wants_labels, "position": "center" if inside else "outsideEnd",
                    "legendMode": False, **plan.drawn}
    scratch = ET.Element(_SVG + "g")
    try:
        _polar_legend(plan, size, scratch)
    except _Retry as error:
        raise failure if failure and str(failure) != "server_chart_slice_labels_collide" else error from None
    for child in list(scratch):
        node.append(child)
    return {"shown": plan.wants_labels, "position": "outsideEnd", "legendMode": True, **plan.drawn}


# --- Entry point -----------------------------------------------------------------------------

def render_server_chart_v4(root: ET.Element | None, node: ET.Element, raw: dict, *,
                           style: tuple[str, str, float, tuple[str, ...], dict] | None = None) -> list[float]:
    """Revision four: replace ``node``'s children with the chart ``raw`` declares, inside its box.

    Same contract as ``render_server_chart_v1``; ``style`` is a replay of the style the first
    drawing recorded (``style_record``), ``None`` reads it from the page.
    """
    return _render(root, node, raw, style)[0]


def _render(root, node, raw, style) -> tuple[list[float], _Plan, dict]:
    """The drawing of :func:`render_server_chart_v4`, plus its plan and decisions (``info``)."""
    _clear(node)
    if style is None:
        style = page_style(root, node)
    plan = _Plan(node, raw, style)
    box = [plan.x0, plan.y0, plan.width, plan.height]
    for key in (RENDER_ATTRIBUTE, BOX_ATTRIBUTE):
        node.attrib.pop(key, None)
    info = None
    failure: ServerChartUnsupported | None = None
    for size in _sizes(min(max(plan.body, _FLOOR), 20.0)):
        try:
            if plan.chart_type in _POLAR:
                info = _draw_polar(plan, node, size)
            elif plan.chart_type in _TREND:
                info = _draw_trend(plan, node, size)
            else:
                info = _draw_bars(plan, node, size)
        except ServerChartUnsupported as error:
            failure = error
            _clear(node)
            continue
        info["size"] = size
        break
    if info is None:
        _clear(node)
        raise failure or ServerChartUnsupported("server_chart_box_too_small")
    raw["valueLabels"] = {"shown": info["shown"], "position": info["position"]}
    raw["categoryOrder"] = ("clockwise" if plan.chart_type in _POLAR
                            else "topToBottom" if plan.chart_type == "bar" else "leftToRight")
    if plan.chart_type in _FAMILIES:
        raw["grouping"] = plan.grouping
    else:
        raw.pop("grouping", None)
    raw["markers"] = bool(info.get("markers", False))
    if plan.title:
        raw["title"] = plan.title
    for item, colors in zip(plan.series, plan.colors, strict=True):
        item["pointColors"] = list(colors if plan.chart_type not in _TREND else [colors[0]] * len(plan.categories))
    if plan.chart_type in _POLAR:
        raw["series"][0]["pointColors"] = list(plan.colors[0])
    return box, plan, info
