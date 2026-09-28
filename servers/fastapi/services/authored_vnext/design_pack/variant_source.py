"""Turn a base pack plus one deck-level re-layout into an authored variant source.

The model only moves and resizes elements (and may rescale a font size). Every
token, font, colour, paint and slot contract is copied from the base pack, so a
variant is the same design at a different canvas, never a new design.
"""

from __future__ import annotations

import json
from typing import Any

from .canvases import EMU_PER_PX, RegisteredCanvas
from .models import DesignFrame, EditableSlot, NativeElement, Spacing
from .registry import RegisteredDesignPack


AUTHORED_SOURCE_SCHEMA = "authored-design-source/v1"
_EDGE_TOLERANCE_PX = 0.5
_FONT_RANGE = (6.0, 400.0)


class VariantDraftError(ValueError):
    """The re-layout cannot be turned into a faithful variant source."""


def _scale(base: RegisteredDesignPack) -> float:
    return 1.0 / EMU_PER_PX if base.structure.canvas.unit == "emu" else 1.0


def _frame_key(frame: DesignFrame) -> str:
    return frame.frame_id.removeprefix("frame:")


def _elements(frame: DesignFrame) -> list[tuple[str, EditableSlot | NativeElement]]:
    slots = [(f"s{index}", slot) for index, slot in enumerate(frame.slots, start=1)]
    chrome = [
        (f"c{index}", element)
        for index, element in enumerate(frame.chrome, start=1)
        if element.bounds is not None and element.kind != "unsupported"
    ]
    return slots + chrome


def _px_bounds(element: EditableSlot | NativeElement, scale: float) -> dict[str, float]:
    bounds = element.bounds
    assert bounds is not None
    return {
        name: round(getattr(bounds, name) * scale, 2)
        for name in ("x", "y", "width", "height")
    }


def layout_brief(base: RegisteredDesignPack, target: RegisteredCanvas) -> dict[str, Any]:
    scale = _scale(base)
    canvas = base.structure.canvas
    frames = []
    for frame in base.structure.frames:
        elements = []
        for key, element in _elements(frame):
            style = element.text_style
            elements.append(
                {
                    "key": key,
                    "kind": element.kind if isinstance(element, NativeElement) else element.accepted_kinds[0],
                    "role": element.semantic_role if isinstance(element, EditableSlot) else "decoration",
                    "editable": isinstance(element, EditableSlot),
                    "bounds": _px_bounds(element, scale),
                    "fontSize": style.font_size if style is not None else None,
                }
            )
        frames.append({"frameId": _frame_key(frame), "role": frame.role, "elements": elements})
    return {
        "sourceCanvas": {"width": round(canvas.width * scale, 2), "height": round(canvas.height * scale, 2)},
        "targetCanvas": {
            "canvasId": target.canvas_id,
            "width": target.width,
            "height": target.height,
            "safeArea": {
                "x": target.safe_x,
                "y": target.safe_y,
                "width": target.safe_width,
                "height": target.safe_height,
            },
        },
        "frames": frames,
    }


def relayout_schema() -> dict[str, Any]:
    element = {
        "type": "object",
        "required": ["key", "x", "y", "width", "height"],
        "properties": {
            "key": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "width": {"type": "number"},
            "height": {"type": "number"},
            "fontSize": {"type": ["number", "null"]},
        },
    }
    frame = {
        "type": "object",
        "required": ["frameId", "elements"],
        "properties": {
            "frameId": {"type": "string"},
            "elements": {"type": "array", "items": element},
        },
    }
    return {
        "type": "object",
        "required": ["frames"],
        "properties": {"frames": {"type": "array", "items": frame}},
    }


def _placements(relayout: object, target: RegisteredCanvas) -> dict[tuple[str, str], dict[str, float]]:
    frames = relayout.get("frames") if isinstance(relayout, dict) else None
    if not isinstance(frames, list):
        raise VariantDraftError("design_pack_variant_relayout_invalid")
    placements: dict[tuple[str, str], dict[str, float]] = {}
    for frame in frames:
        elements = frame.get("elements") if isinstance(frame, dict) else None
        if not isinstance(elements, list):
            raise VariantDraftError("design_pack_variant_relayout_invalid")
        for item in elements:
            try:
                key = (str(frame["frameId"]), str(item["key"]))
                geometry = {name: float(item[name]) for name in ("x", "y", "width", "height")}
                font = item.get("fontSize")
                font_size = None if font is None else float(font)
            except (KeyError, TypeError, ValueError) as error:
                raise VariantDraftError("design_pack_variant_relayout_invalid") from error
            if key in placements:
                raise VariantDraftError("design_pack_variant_relayout_duplicate")
            if (
                geometry["x"] < 0
                or geometry["y"] < 0
                or geometry["width"] <= 0
                or geometry["height"] <= 0
                or geometry["x"] + geometry["width"] > target.width + _EDGE_TOLERANCE_PX
                or geometry["y"] + geometry["height"] > target.height + _EDGE_TOLERANCE_PX
            ):
                raise VariantDraftError("design_pack_variant_relayout_out_of_canvas")
            if font_size is not None and not _FONT_RANGE[0] <= font_size <= _FONT_RANGE[1]:
                raise VariantDraftError("design_pack_variant_font_size_invalid")
            placements[key] = {**geometry, "fontSize": font_size}
    return placements


def _spacing(spacing: Spacing | None, scale: float) -> dict[str, Any] | None:
    if spacing is None:
        return None
    value = spacing.model_dump(mode="json", by_alias=True, exclude_none=True)
    for name in ("insetLeft", "insetTop", "insetRight", "insetBottom"):
        value[name] = round(value.get(name, 0) * scale, 4)
    return value


def _style(element: EditableSlot | NativeElement, font_size: float | None) -> dict[str, Any] | None:
    if element.text_style is None:
        return None
    value = element.text_style.model_dump(mode="json", by_alias=True, exclude_none=True)
    if font_size is not None:
        value["fontSize"] = font_size
    return value


def _paint(element: EditableSlot | NativeElement, scale: float) -> dict[str, Any] | None:
    if element.paint is None:
        return None
    value = element.paint.model_dump(mode="json", by_alias=True, exclude_none=True)
    if "lineWidth" in value:
        value["lineWidth"] = round(value["lineWidth"] * scale, 4)
    return value


def _source_element(
    key: str, element: EditableSlot | NativeElement, place: dict[str, Any], scale: float
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": key,
        "bounds": {name: place[name] for name in ("x", "y", "width", "height")},
    }
    optional = {
        "textStyle": _style(element, place["fontSize"]),
        "spacing": _spacing(element.spacing, scale),
        "paint": _paint(element, scale),
        "containerId": element.container_id,
    }
    value.update({name: item for name, item in optional.items() if item is not None})
    if isinstance(element, NativeElement):
        value["kind"] = element.kind
        return value
    value.update(
        kind=element.accepted_kinds[0],
        acceptedKinds=list(element.accepted_kinds),
        semanticRole=element.semantic_role,
        slotId=element.slot_id,
        required=element.required,
        editable=True,
        slotApproved=True,
        extractionConfidence=element.extraction_confidence or 1.0,
    )
    return value


def build_variant_source(
    base: RegisteredDesignPack, target: RegisteredCanvas, relayout: object
) -> dict[str, Any]:
    placements = _placements(relayout, target)
    scale = _scale(base)
    expected: set[tuple[str, str]] = set()
    frames = []
    for frame in base.structure.frames:
        frame_key = _frame_key(frame)
        elements = []
        for key, element in _elements(frame):
            expected.add((frame_key, key))
            place = placements.get((frame_key, key))
            if place is None:
                raise VariantDraftError("design_pack_variant_relayout_incomplete")
            elements.append(_source_element(key, element, place, scale))
        frames.append(
            {
                "id": frame_key,
                "type": "html" if frame.frame_type == "html" else "scene",
                "role": frame.role,
                "elements": elements,
            }
        )
    if set(placements) != expected:
        raise VariantDraftError("design_pack_variant_relayout_unknown_element")
    return {
        "schemaVersion": AUTHORED_SOURCE_SCHEMA,
        "canvas": {"width": target.width, "height": target.height},
        "frames": frames,
    }


def source_bytes(source: dict[str, Any]) -> bytes:
    return (json.dumps(source, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


__all__ = [
    "VariantDraftError",
    "build_variant_source",
    "layout_brief",
    "relayout_schema",
    "source_bytes",
]
