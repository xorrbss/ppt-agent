"""The native PPTX style of a trusted ``svg-server-chart/v4`` chart: one spec, two outputs.

Revision four decides every visible property of its drawing (palette, font sizes, value axis
bounds and ticks, gridlines, label placement, legend, plot area). The native chart used to be
rebuilt from the JSON plus whatever ``native_chart.chart_object`` could infer back from the
drawing, so it fell back to converter defaults for most of them. :func:`native_style` replays
the stored declaration and replay style through the same renderer (``_render``) and returns
those decisions as a deterministic spec; publication stores it as
``svgAppearance["serverChartStyle"]`` (validated by
``native_objects.model.validate_server_chart_style``) and the export projection
(``export_projection._server_chart``) builds the converter payload from it alone.

No new stored declaration state: the spec is a pure function of the record
``trusted_server_chart_ids`` already replays. Earlier revisions and author-drawn charts never
carry it, so their sidecars and converter input are unchanged.

What the native chart cannot express (no converter field, the converter stays unpatched):
per-point label placement (line and area labels the drawing moved above or below a point, end
labels of several lines), an area plotted between category centres (no ``crossBetween`` for
non-date area charts), a value axis without its axis line (the converter strokes both axes
with one colour), translucent gridlines (they are blended to an opaque colour), the separator
between a slice name and its value, and per-series label colours in a stack. Pie and donut
values drawn inside their slices keep their place; the names drawn beside them become a legend.
"""
from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

from services.generation_core.native_objects.model import NATIVE_STYLE_VERSION
from services.generation_core.drivers.svg.server_chart import (
    BOX_ATTRIBUTE, RENDER_ATTRIBUTE, SERVER_CHART_REVISION_V4, _contrast, _fmt, _SVG,
)

_GRID_ALPHA, _BASELINE_ALPHA = 0.16, 0.45
#: Revision four draws line points as circles of radius 4.5 and lines 3 px wide; areas 2 px.
_MARKER_DIAMETER_PX = 9.0
_LINE_WIDTH_PX, _AREA_LINE_WIDTH_PX = 3.0, 2.0


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _luminance(color: str) -> float:
    red, green, blue = _rgb(color)
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def _blend(ink: str, alpha: float) -> str:
    """``ink`` at ``alpha`` as an opaque colour: over white for dark ink, over black for light ink.

    The drawing strokes gridlines and the baseline with translucent ink; the native chart
    takes opaque colours only. Light ink means the page is dark, so it blends toward black.
    """
    ground = 255 if _luminance(ink) < 0.5 else 0
    return "#" + "".join(f"{round(part * alpha + ground * (1 - alpha)):02X}" for part in _rgb(ink))


def _relative(box: tuple[float, float, float, float], frame: tuple[float, float, float, float]) -> dict | None:
    """``box`` (x, y, width, height in canvas px) as fractions of ``frame``, clipped to it."""
    fx, fy, fw, fh = frame
    x0, y0 = max(box[0], fx), max(box[1], fy)
    x1, y1 = min(box[0] + box[2], fx + fw), min(box[1] + box[3], fy + fh)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return {"x": round((x0 - fx) / fw, 6), "y": round((y0 - fy) / fh, 6),
            "w": round((x1 - x0) / fw, 6), "h": round((y1 - y0) / fh, 6)}


def _decimals(value: Decimal) -> int:
    from services.generation_core.drivers.svg.server_chart_v4 import AS_WRITTEN

    # Under AS_WRITTEN a label keeps the value's written digits (9.0), so the format must too.
    exponent = (value if AS_WRITTEN.get() else value.normalize()).as_tuple().exponent
    return max(-exponent, 0) if isinstance(exponent, int) else 0


def _literal(unit: str) -> str:
    # The unit grammar (``%`` or 1-4 letters) needs no escaping inside a quoted literal;
    # quoting ``%`` keeps it a literal instead of a x100 percent format.
    return f'"{unit}"' if unit else ""


def _label_format(values: list[list[Decimal]], unit: str) -> str:
    """The number format that writes every value as the drawing's label does.

    Labels group thousands and keep each value's own decimals; one fixed decimal count is
    exact when every value has it (or none), otherwise ``General`` keeps the digits.
    """
    counts = {_decimals(value) for row in values for value in row}
    if counts == {0}:
        return "#,##0" + _literal(unit)
    if len(counts) == 1:
        return "#,##0." + "0" * counts.pop() + _literal(unit)
    return "General" + _literal(unit)


def _tick_format(step: Decimal, suffix: str, percent: bool) -> str:
    if percent:
        # A percent stack's native axis runs from 0 to 1 and formats as percent.
        decimals = max(_decimals(step) - 2, 0)
        return "0" + ("." + "0" * decimals if decimals else "") + "%"
    decimals = _decimals(step)
    return "#,##0" + ("." + "0" * decimals if decimals else "") + _literal(suffix)


def _number(value: Decimal) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def native_style(raw: dict, style: tuple, box: list[float]) -> tuple[dict, dict]:
    """``(series colours by index, spec)`` of a stored v4 declaration, from a replay of its drawing.

    ``raw`` and ``style`` are the record ``trusted_server_chart_ids`` replays (the normalised
    declaration and ``replay_style``), ``box`` its chart box. Raises what the renderer raises.
    """
    from services.generation_core.drivers.svg.server_chart_v4 import _POLAR, _STACKED, _TREND, _render

    probe = ET.Element(_SVG + "g", {RENDER_ATTRIBUTE: SERVER_CHART_REVISION_V4,
                                    BOX_ATTRIBUTE: " ".join(_fmt(value) for value in box)})
    _box, plan, info = _render(None, probe, raw, style)
    frame = (plan.x0, plan.y0, plan.width, plan.height)
    size = info["size"]
    spec: dict = {
        "version": NATIVE_STYLE_VERSION, "fontFamily": plan.font, "textColor": plan.ink.upper(),
        "axisColor": _blend(plan.ink.upper(), _BASELINE_ALPHA), "gridColor": _blend(plan.ink.upper(), _GRID_ALPHA),
        "textPx": size,
    }
    if "title" in info:
        x, y, width, height, title_px = info["title"]
        spec["title"] = {"px": title_px, "layout": _relative((x, y, width, height), frame)}
    unit_values = [[Decimal(str(value)) for value in item["values"]] for item in plan.series]
    labels = {"shown": bool(info["shown"]), "position": info["position"], "px": size, "bold": True,
              "showCategory": False, "numberFormat": _label_format(unit_values, plan.unit)}
    legend = {"visible": False}
    colors = {index: plan.colors[index][0] for index in range(len(plan.series))}
    if plan.chart_type in _POLAR:
        cx, cy, radius = info["pie"]
        spec["plotLayout"] = _relative((cx - radius, cy - radius, 2 * radius, 2 * radius), frame)
        pie = plan.chart_type == "pie"
        slices = plan.colors[0]
        if pie and not info["legendMode"] and info["position"] == "outsideEnd":
            # Names and values beside their slices, as drawn.
            labels.update(position="outsideEnd", color=plan.ink.upper(), showCategory=True, bold=False)
        elif pie and info["legendMode"]:
            labels.update(position="outsideEnd", color=plan.ink.upper())
        else:
            # Values inside their slices in their contrast colours; the slice names drawn beside
            # them become the legend (one label cannot put the name outside and the value inside).
            labels.update(position="center", pointColors=[_contrast(color) for color in slices])
        legend = {"visible": info["legendMode"] or not labels["showCategory"], "position": "right"}
        if info["legendMode"]:
            legend["layout"] = _relative(info["legend"], frame)
        if plan.chart_type == "donut":
            from services.generation_core.drivers.svg.server_chart_v4 import _DONUT_HOLE
            spec["holeRatio"] = _DONUT_HOLE
    else:
        x0, y0, x1, y1 = info["plot"]
        spec["plotLayout"] = _relative((x0, y0, x1 - x0, y1 - y0), frame)
        # A percent stack's native value axis runs from 0 to 1; the drawing's runs to 100.
        scale = Decimal(100) if plan.percent else Decimal(1)
        axis_max, step = info["axisMax"] / scale, info["majorUnit"]
        axis = {"visible": bool(info["axis"]), "gridlines": bool(info["axis"]), "minimum": 0,
                "maximum": _number(axis_max)}
        if step is not None:
            axis["majorUnit"] = _number(step / scale)
        axis["numberFormat"] = _tick_format(step / scale if step is not None else Decimal(1), plan.suffix,
                                            plan.percent)
        spec["valueAxis"] = axis
        if "legend" in info:
            # The native legend key (a line with its marker for a trend) is wider than the drawn
            # 14 px swatch: each entry gets room for it, else the last entries are clipped.
            x, y, width, height = info["legend"]
            room = width + len(plan.series) * size * 1.6
            legend = {"visible": True, "position": "top", "layout": _relative((x, y, room, height), frame)}
        if plan.chart_type in _TREND:
            area = plan.chart_type == "area"
            spec["line"] = {"widthPx": _AREA_LINE_WIDTH_PX if area else _LINE_WIDTH_PX,
                            "markerPx": None if area else _MARKER_DIAMETER_PX}
            if area:
                from services.generation_core.drivers.svg.server_chart_v4 import _AREA_OPACITY
                spec["fillOpacity"] = float(_AREA_OPACITY)
            labels["color"] = plan.ink.upper()
        else:
            band, thickness, slots = info["band"], info["thickness"], info["slots"]
            spec["bars"] = {"gapWidth": max(0, min(500, round((band - thickness * slots) / thickness * 100))),
                            "overlap": 100 if plan.grouping in _STACKED else 0}
            if info["position"] == "outsideEnd":
                labels["color"] = plan.ink.upper()
            elif plan.grouping in _STACKED:
                # One label colour per stack: the converter has no per-series labels there, so
                # the contrast colour most series share (white on ties) stands for all.
                votes = [_contrast(plan.colors[index][0]) for index in range(len(plan.series))]
                labels["color"] = max(sorted(set(votes), key=lambda c: c != "#FFFFFF"), key=votes.count)
            else:
                labels["pointColors"] = [_contrast(color) for color in plan.colors[0]]
    if not labels["shown"]:
        labels = {"shown": False}
    if legend.get("layout", True) is None:
        del legend["layout"]
    spec["dataLabels"] = labels
    spec["legend"] = legend
    if spec.get("title", {}).get("layout", True) is None:
        del spec["title"]["layout"]
    if spec["plotLayout"] is None:
        del spec["plotLayout"]
    return colors, spec


__all__ = ["NATIVE_STYLE_VERSION", "native_style"]
