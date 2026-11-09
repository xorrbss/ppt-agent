"""Per-slide ``NativeObjects`` sidecar: semantic tables/charts next to the markup.

Stored additively in ``slide.content["nativeObjects"]`` (no migration) as::

    {"schemaVersion": "native-objects/v1",
     "objects": [{"id": "<group id in the slide source>",
                  "kind": "table" | "chart",
                  "anchor": {"x": .., "y": .., "width": .., "height": ..},  # canvas px
                  "payload": <authored V1 table/chart payload shape>}]}

The payload shape is authored's (``payload.py``); the anchor is the object's
box in the slide canvas coordinate space. The sidecar is format-neutral: which
markup element ``id`` names and how an exporter turns an entry into a native
PPTX object is a driver concern (``drivers/svg/export_scene.py`` for svg).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from services.generation_core.native_objects.payload import (
    NativePayloadError,
    parse_chart_payload,
    parse_table_payload,
)
from utils.hashing import sha256_prefixed

NATIVE_OBJECTS_SCHEMA = "native-objects/v1"
SLIDE_CONTENT_KEY = "nativeObjects"
NativeKind = Literal["table", "chart"]
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MAX_OBJECTS = 32
_PARSERS = {"table": parse_table_payload, "chart": parse_chart_payload}


class NativeObjectsError(ValueError):
    """An invalid sidecar; ``str(error)`` is the reason code."""


@dataclass(frozen=True)
class NativeAnchor:
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_wire(cls, value: Any) -> "NativeAnchor":
        if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
            raise NativeObjectsError("native_object_anchor_invalid")
        numbers = [value[key] for key in ("x", "y", "width", "height")]
        if not all(type(n) in (int, float) and math.isfinite(n) for n in numbers):
            raise NativeObjectsError("native_object_anchor_invalid")
        anchor = cls(*(float(n) for n in numbers))
        if anchor.width <= 0 or anchor.height <= 0:
            raise NativeObjectsError("native_object_anchor_invalid")
        return anchor

    def to_wire(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def within(self, width: float, height: float, *, tolerance: float = 0.5) -> bool:
        return (self.x >= -tolerance and self.y >= -tolerance
                and self.x + self.width <= width + tolerance
                and self.y + self.height <= height + tolerance)


@dataclass(frozen=True)
class NativeObject:
    id: str
    kind: NativeKind
    anchor: NativeAnchor
    payload: dict
    svg_appearance: dict | None = None

    @classmethod
    def build(cls, *, id: str, kind: str, anchor: NativeAnchor | dict, payload: Any,
              svgAppearance: Any = None) -> "NativeObject":
        """Validate and canonicalize one entry; raises ``NativeObjectsError``."""
        if not isinstance(id, str) or not _ID.match(id):
            raise NativeObjectsError("native_object_id_invalid")
        parser = _PARSERS.get(kind)
        if parser is None:
            raise NativeObjectsError("native_object_kind_invalid")
        if not isinstance(anchor, NativeAnchor):
            anchor = NativeAnchor.from_wire(anchor)
        try:
            canonical = parser(payload)
        except NativePayloadError as error:
            raise NativeObjectsError(f"native_object_payload_invalid:{error}") from error
        if svgAppearance is not None:
            if kind != "chart" or not isinstance(svgAppearance, dict):
                raise NativeObjectsError("native_chart_appearance_invalid")
            if "holeRatio" in svgAppearance and (
                    canonical["chartType"] != "donut"
                    or type(svgAppearance["holeRatio"]) not in (int, float)
                    or not 0 < svgAppearance["holeRatio"] < 1
                    or not math.isfinite(svgAppearance["holeRatio"])):
                raise NativeObjectsError("native_chart_appearance_invalid")
            # V5 may prove the hole of a declaration with no legacy appearance.
            # That singleton carries no inferred label, title, or marker options.
            if set(svgAppearance) != {"holeRatio"} and (
                    not {"categoryOrder", "valueLabels", "markers"} <= set(svgAppearance)
                    or set(svgAppearance) - {"categoryOrder", "valueLabels", "markers",
                                             "lineWidthPx", "markerDiameterPx", "holeRatio",
                                             "serverChartStyle"}
                    or svgAppearance["categoryOrder"] not in ("topToBottom", "leftToRight", "clockwise")
                    or svgAppearance["categoryOrder"] != {
                        "bar": "topToBottom", "column": "leftToRight", "line": "leftToRight",
                        "area": "leftToRight", "pie": "clockwise", "donut": "clockwise",
                    }[canonical["chartType"]]
                    or not isinstance(svgAppearance["valueLabels"], dict)
                    or set(svgAppearance["valueLabels"]) != {"shown", "position"}
                    or type(svgAppearance["valueLabels"]["shown"]) is not bool
                    or svgAppearance["valueLabels"]["position"] not in
                    (None, "center", "insideEnd", "insideBase", "outsideEnd", "above", "bestFit")
                    or type(svgAppearance["markers"]) is not bool
                    or any(type(svgAppearance[key]) not in (int, float) or
                           not math.isfinite(svgAppearance[key]) or svgAppearance[key] <= 0
                           for key in ("lineWidthPx", "markerDiameterPx") if key in svgAppearance)):
                raise NativeObjectsError("native_chart_appearance_invalid")
            if "serverChartStyle" in svgAppearance:
                # The native style of a trusted v4 server chart (absent from every other sidecar).
                try:
                    validate_server_chart_style(svgAppearance["serverChartStyle"], canonical["chartType"],
                                                len(canonical["categories"]), canonical.get("grouping"))
                except ServerChartStyleError as error:
                    raise NativeObjectsError("native_chart_appearance_invalid") from error
        return cls(id=id, kind=kind, anchor=anchor, payload=canonical, svg_appearance=svgAppearance)

    def to_wire(self) -> dict:
        entry = {"id": self.id, "kind": self.kind, "anchor": self.anchor.to_wire(), "payload": self.payload}
        if self.svg_appearance is not None:
            entry["svgAppearance"] = self.svg_appearance
        return entry


@dataclass(frozen=True)
class NativeObjects:
    objects: tuple[NativeObject, ...] = ()

    def __post_init__(self) -> None:
        ids = [item.id for item in self.objects]
        if len(ids) != len(set(ids)):
            raise NativeObjectsError("native_object_id_duplicate")
        if len(ids) > _MAX_OBJECTS:
            raise NativeObjectsError("native_objects_limit")

    def __bool__(self) -> bool:
        return bool(self.objects)

    @classmethod
    def from_wire(cls, value: Any) -> "NativeObjects":
        if (not isinstance(value, dict) or set(value) != {"schemaVersion", "objects"}
                or value["schemaVersion"] != NATIVE_OBJECTS_SCHEMA or not isinstance(value["objects"], list)):
            raise NativeObjectsError("native_objects_invalid")
        entries = []
        for item in value["objects"]:
            if (not isinstance(item, dict) or
                    set(item) not in ({"id", "kind", "anchor", "payload"},
                                      {"id", "kind", "anchor", "payload", "svgAppearance"})):
                raise NativeObjectsError("native_object_invalid")
            entries.append(NativeObject.build(**item))
        return cls(tuple(entries))

    def to_wire(self) -> dict:
        return {"schemaVersion": NATIVE_OBJECTS_SCHEMA, "objects": [item.to_wire() for item in self.objects]}

    def digest(self) -> str:
        encoded = json.dumps(self.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256_prefixed(encoded.encode("utf-8"))

    def by_id(self) -> dict[str, NativeObject]:
        return {item.id: item for item in self.objects}


# --- svgAppearance["serverChartStyle"]: the native style of a trusted v4 server chart ---------
#
# Produced by ``drivers/svg/server_chart_v4_native.native_style`` from a replay of the chart's
# drawing and consumed by ``drivers/svg/export_projection``. Optional: every sidecar written
# before it (and every chart that is not a trusted v4 server chart) has no such key and keeps
# its exact projection. Lengths are canvas px; layouts are fractions of the anchor.

NATIVE_STYLE_VERSION = "svg-server-chart-native-style/v1"
_STYLE_HEX = re.compile(r"^#[0-9A-F]{6}$")
_LABEL_POSITIONS = {
    "bar": ("outsideEnd", "insideEnd", "insideBase", "center"),
    "column": ("outsideEnd", "insideEnd", "insideBase", "center"),
    "line": ("above",), "area": (None,), "pie": ("outsideEnd", "center"), "donut": ("center",),
}
_FORMAT = re.compile(r'^(?:#,##0(?:\.0{1,12})?|General|0(?:\.0{1,12})?%)(?:"(?:%|[A-Za-z가-힣]{1,4})")?$')


class ServerChartStyleError(ValueError):
    """A spec outside the v1 shape."""


def _style_check(condition: bool) -> None:
    if not condition:
        raise ServerChartStyleError("native_chart_server_style_invalid")


def _style_number(value: Any, *, positive: bool = False) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def _style_px(value: Any) -> bool:
    return _style_number(value, positive=True) and value <= 400


def _style_color(value: Any) -> bool:
    return isinstance(value, str) and bool(_STYLE_HEX.match(value))


def _style_layout(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"x", "y", "w", "h"}
            and all(_style_number(value[key]) and value[key] <= 1 for key in value)
            and value["w"] > 0 and value["h"] > 0
            and value["x"] + value["w"] <= 1 + 1e-6 and value["y"] + value["h"] <= 1 + 1e-6)


def validate_server_chart_style(spec: Any, chart_type: str, point_count: int, grouping: str | None) -> None:
    """Raise :class:`ServerChartStyleError` unless ``spec`` is a v1 native style for this chart."""
    polar, trend = chart_type in ("pie", "donut"), chart_type in ("line", "area")
    bars = not polar and not trend
    required = {"version", "fontFamily", "textColor", "axisColor", "gridColor", "textPx", "dataLabels", "legend"}
    optional = {"title", "plotLayout"} | (
        {"holeRatio"} if chart_type == "donut" else set() if polar
        else {"valueAxis", "line", "fillOpacity"} if trend else {"valueAxis", "bars"})
    _style_check(isinstance(spec, dict) and required <= set(spec) <= required | optional)
    _style_check(spec["version"] == NATIVE_STYLE_VERSION)
    _style_check(isinstance(spec["fontFamily"], str) and 0 < len(spec["fontFamily"].strip()) <= 200)
    _style_check(all(_style_color(spec[key]) for key in ("textColor", "axisColor", "gridColor")) and _style_px(spec["textPx"]))
    if "title" in spec:
        title = spec["title"]
        _style_check(isinstance(title, dict) and "px" in title and set(title) <= {"px", "layout"} and _style_px(title["px"])
                     and ("layout" not in title or _style_layout(title["layout"])))
    _style_check("plotLayout" not in spec or _style_layout(spec["plotLayout"]))
    if chart_type == "donut":
        _style_check("holeRatio" in spec and _style_number(spec["holeRatio"], positive=True) and spec["holeRatio"] < 1)
    if not polar:
        axis = spec.get("valueAxis")
        _style_check(isinstance(axis, dict)
                     and {"visible", "gridlines", "minimum", "maximum", "numberFormat"} <= set(axis)
                     <= {"visible", "gridlines", "minimum", "maximum", "majorUnit", "numberFormat"}
                     and type(axis["visible"]) is bool and type(axis["gridlines"]) is bool
                     and axis["minimum"] == 0 and _style_number(axis["maximum"], positive=True)
                     and ("majorUnit" not in axis or (_style_number(axis["majorUnit"], positive=True)
                                                      and 1 <= axis["maximum"] / axis["majorUnit"] <= 32))
                     and isinstance(axis["numberFormat"], str) and bool(_FORMAT.match(axis["numberFormat"])))
    if bars:
        gaps = spec.get("bars")
        _style_check(isinstance(gaps, dict) and set(gaps) == {"gapWidth", "overlap"}
                     and type(gaps["gapWidth"]) is int and 0 <= gaps["gapWidth"] <= 500
                     and gaps["overlap"] == (100 if grouping in ("stacked", "percentStacked") else 0))
    if trend:
        line = spec.get("line")
        _style_check(isinstance(line, dict) and set(line) == {"widthPx", "markerPx"} and _style_px(line["widthPx"])
                     and (line["markerPx"] is None if chart_type == "area" else _style_px(line["markerPx"])))
        _style_check(("fillOpacity" in spec) is (chart_type == "area")
                     and ("fillOpacity" not in spec or (_style_number(spec["fillOpacity"]) and spec["fillOpacity"] <= 1)))
    labels = spec["dataLabels"]
    _style_check(isinstance(labels, dict) and type(labels.get("shown")) is bool)
    if labels["shown"]:
        keys = {"shown", "position", "px", "bold", "showCategory", "numberFormat"}
        _style_check(keys <= set(labels) <= keys | {"color", "pointColors"}
                     and labels["position"] in _LABEL_POSITIONS[chart_type]
                     and not (grouping in ("stacked", "percentStacked") and labels["position"] == "outsideEnd")
                     and _style_px(labels["px"]) and type(labels["bold"]) is bool and type(labels["showCategory"]) is bool
                     and (not labels["showCategory"] or chart_type == "pie")
                     and isinstance(labels["numberFormat"], str) and bool(_FORMAT.match(labels["numberFormat"]))
                     and ("color" not in labels or _style_color(labels["color"]))
                     and ("pointColors" not in labels or (
                         isinstance(labels["pointColors"], list) and len(labels["pointColors"]) == point_count
                         and all(_style_color(item) for item in labels["pointColors"]))))
    else:
        _style_check(set(labels) == {"shown"})
    legend = spec["legend"]
    _style_check(isinstance(legend, dict) and type(legend.get("visible")) is bool
                 and set(legend) <= {"visible", "position", "layout"}
                 and legend.get("position", "top" if not polar else "right") == ("right" if polar else "top")
                 and ("layout" not in legend or _style_layout(legend["layout"])))


def slide_native_objects(content: Any) -> NativeObjects | None:
    """The validated sidecar of one slide's ``content``; ``None`` when absent.

    A present but invalid sidecar raises: stored data is never silently
    ignored (the exporter records it as a fallback reason instead).
    """
    if not isinstance(content, dict) or content.get(SLIDE_CONTENT_KEY) is None:
        return None
    return NativeObjects.from_wire(content[SLIDE_CONTENT_KEY])
