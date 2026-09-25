"""Request-local Scene logical/CSS-point to physical OOXML conversion.

Scene producers seal CSS points (logical px * .75), including table data.
Do not mutate that sealed graph or its digest when serializing another paper size.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from functools import wraps
import json
import math

from pptx.util import Emu, Inches, Pt

from services.document_canvas import DocumentCanvasMetadata


_canvas: ContextVar[DocumentCanvasMetadata | None] = ContextVar("native_scene_canvas", default=None)


def with_scene_canvas(source: str):
    def decorate(function):
        @wraps(function)
        def execute(*args, **kwargs):
            from services.authored_vnext.ooxml.native_scene import NativeSceneError, _read_custom_properties

            try:
                if source == "scene":
                    value = args[0] if args else kwargs["scene_value"]
                else:
                    content = args[0] if args else kwargs["content"]
                    properties = _read_custom_properties(content)
                    value = json.loads(properties["AuthoredSceneIdentity"])
                canvas = (DocumentCanvasMetadata.model_validate(value["documentCanvas"])
                          if isinstance(value, Mapping) and "documentCanvas" in value else None)
            except (KeyError, TypeError, ValueError) as error:
                raise NativeSceneError("authored_vnext_scene_canvas_invalid") from error
            token = _canvas.set(canvas)
            try:
                return function(*args, **kwargs)
            finally:
                _canvas.reset(token)
        return execute
    return decorate


def canvas_identity() -> dict:
    canvas = _canvas.get()
    return {"documentCanvas": canvas.model_dump(mode="json", by_alias=True)} if canvas else {}


def logical_canvas(scene: Mapping | None = None) -> tuple[int, int] | None:
    canvas = _canvas.get()
    if canvas and scene is not None:
        from services.authored_vnext.ooxml.native_scene import NativeSceneError

        for key in ("canvas", "slideSizePx", "size"):
            if key not in scene:
                continue
            value = scene[key]
            if not isinstance(value, Mapping):
                raise NativeSceneError("authored_vnext_scene_canvas_invalid")
            size = (value.get("width", value.get("widthPx")), value.get("height", value.get("heightPx")))
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in size)
                    or size != (canvas.width_px, canvas.height_px)):
                raise NativeSceneError("document_canvas_geometry_mismatch")
    return (canvas.width_px, canvas.height_px) if canvas else None


def scene_px(value: float):
    canvas = _canvas.get()
    return Emu(round(value * canvas.emu_per_unit)) if canvas else Inches(value / 96)


def scene_points(value: float):
    canvas = _canvas.get()
    return Emu(round(value / .75 * canvas.emu_per_unit)) if canvas else Pt(value)


def scene_font(value: float):
    canvas = _canvas.get()
    # DrawingML font sizes are integral hundredths of physical points.
    return Emu(round(value / .75 * canvas.point_per_unit * 100) * 127) if canvas else Pt(value)


def intended_font_points(value: float) -> float:
    return float(scene_font(value).pt) if _canvas.get() else value


def physical_border_points(value: float) -> float:
    return round(float(scene_points(value).pt), 6)


def shape_bounds_px(shape) -> dict[str, float]:
    canvas = _canvas.get()
    scale = canvas.emu_per_unit if canvas else 9525
    return {key: round(value / scale, 6) for key, value in (
        ("x", shape.left), ("y", shape.top), ("width", shape.width), ("height", shape.height)
    )}


def picture_crop(shape) -> dict[str, float]:
    """Crop fractions are dimensionless and never receive document scale."""
    return {side: round(float(getattr(shape, f"crop_{side}")), 6)
            for side in ("left", "top", "right", "bottom")}


def validate_page_size(presentation) -> None:
    from services.authored_vnext.ooxml.native_scene import NativeSceneError

    canvas = _canvas.get()
    if canvas and (presentation.slide_width, presentation.slide_height) != (
        canvas.physical_size.width_emu, canvas.physical_size.height_emu
    ):
        raise NativeSceneError("authored_vnext_scene_page_size_readback_mismatch")


def master_for_canvas(master: dict) -> dict:
    """Adapt measured fixed furniture without mutating the pinned master."""
    from services.authored_vnext.ooxml.native_scene import NativeSceneError

    canvas = _canvas.get()
    if canvas is None or not master.get("furniture"):
        return master
    size = master.get("slideSizePx", {})
    if not isinstance(size, Mapping) or not isinstance(master["furniture"], list):
        raise NativeSceneError("authored_vnext_master_canvas_unmeasurable")
    width, height = size.get("width"), size.get("height")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
           for v in (width, height)):
        raise NativeSceneError("authored_vnext_master_canvas_unmeasurable")
    if (width, height) == (canvas.width_px, canvas.height_px):
        return master
    safe = canvas.safe_area
    scale = min(safe.width / width, safe.height / height)
    x = safe.x + (safe.width - width * scale) / 2
    y = safe.y + (safe.height - height * scale) / 2
    furniture = []
    for item in master["furniture"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("bounds"), Mapping):
            raise NativeSceneError("authored_vnext_master_furniture_invalid")
        bounds = item.get("bounds", {})
        if any(isinstance(bounds.get(k), bool) or not isinstance(bounds.get(k), (int, float))
               or not math.isfinite(bounds[k]) for k in ("x", "y", "width", "height")):
            raise NativeSceneError("authored_vnext_master_furniture_invalid")
        if (min(bounds["x"], bounds["y"]) < 0 or min(bounds["width"], bounds["height"]) <= 0
                or bounds["x"] + bounds["width"] > width + .01
                or bounds["y"] + bounds["height"] > height + .01):
            raise NativeSceneError("authored_vnext_master_furniture_invalid")
        furniture.append({**item, "bounds": {
            "x": x + bounds["x"] * scale, "y": y + bounds["y"] * scale,
            "width": bounds["width"] * scale, "height": bounds["height"] * scale,
        }})
    return {**master, "furniture": furniture, "_documentCanvasFurnitureScale": scale}
