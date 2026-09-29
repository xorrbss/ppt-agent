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
            if (kind != "chart" or not isinstance(svgAppearance, dict)
                    or not {"categoryOrder", "valueLabels", "markers"} <= set(svgAppearance)
                    or set(svgAppearance) - {"categoryOrder", "valueLabels", "markers",
                                             "lineWidthPx", "markerDiameterPx"}
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


def slide_native_objects(content: Any) -> NativeObjects | None:
    """The validated sidecar of one slide's ``content``; ``None`` when absent.

    A present but invalid sidecar raises: stored data is never silently
    ignored (the exporter records it as a fallback reason instead).
    """
    if not isinstance(content, dict) or content.get(SLIDE_CONTENT_KEY) is None:
        return None
    return NativeObjects.from_wire(content[SLIDE_CONTENT_KEY])
