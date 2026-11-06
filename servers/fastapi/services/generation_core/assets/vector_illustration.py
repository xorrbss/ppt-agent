"""Validate a model-drawn SVG picture and normalise it into contract shapes.

The drawing comes from ``utils/llm_calls/draw_vector_illustrations.py``. A
picture is accepted only when it is a single SVG of the requested size that
uses the allowed shapes, no text, no references and no styling, stays inside
its canvas, and fits the element and point caps. Its paints are moved onto the
deck palette. The accepted picture is re-serialised from its flattened shapes
(linear paths, polygons, circles, ellipses, rects, lines; round caps and joins)
so the stored file is already in the slide contract's subset. A rejected
picture raises ``ValueError`` with a reason code and is dropped by the caller.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Sequence

from services.generation_core.drivers.svg.embed_assets import icon_group
from services.generation_core.drivers.svg.icon_flatten import (
    ASSET_PROJECTION_CURRENT, FlattenedIcon, flatten_icon_svg, validate_asset_projection_revision,
)
from services.svg_contract import SVG_NS

MAX_SVG_BYTES = 400_000
ELEMENT_CAP = 300
POINT_CAP = 6000
#: Chord error of sampled curves, in canvas units (the canvas is drawn at about 1:1).
TOLERANCE = 0.6
#: How far a shape may stray past the canvas edge before the picture counts as cut off.
EDGE_SLACK = 2.0
_SHAPES = {"g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "title", "desc"}
_FORBIDDEN_ATTRIBUTES = {"style", "class", "href", "clip-path", "mask", "filter", "marker-start",
                         "marker-mid", "marker-end", "font-family", "font-size"}


@dataclass(frozen=True)
class ValidatedDrawing:
    svg: str
    width: int
    height: int
    elements: int
    points: int
    snapped_paints: int
    fill_ratio: float


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _bounds(icon: FlattenedIcon) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for primitive in icon.primitives:
        half = (primitive.get("stroke-width", icon.stroke_width) / 2
                if primitive.get("stroke") != "none" else 0.0)
        if "points" in primitive or "subpaths" in primitive:
            rings = primitive.get("subpaths") or [primitive["points"]]
            for ring in rings:
                xs += [x - half for x, _ in ring] + [x + half for x, _ in ring]
                ys += [y - half for _, y in ring] + [y + half for _, y in ring]
        elif primitive["tag"] == "rect":
            xs += [primitive["x"] - half, primitive["x"] + primitive["width"] + half]
            ys += [primitive["y"] - half, primitive["y"] + primitive["height"] + half]
        elif primitive["tag"] in {"circle", "ellipse"}:
            rx = primitive.get("r", primitive.get("rx", 0.0)) + half
            ry = primitive.get("r", primitive.get("ry", 0.0)) + half
            xs += [primitive["cx"] - rx, primitive["cx"] + rx]
            ys += [primitive["cy"] - ry, primitive["cy"] + ry]
        elif primitive["tag"] == "line":
            xs += [primitive["x1"] - half, primitive["x2"] + half, primitive["x1"] + half, primitive["x2"] - half]
            ys += [primitive["y1"] - half, primitive["y2"] + half, primitive["y1"] + half, primitive["y2"] - half]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def validate_drawing(svg: str, *, width: int, height: int, palette: Sequence[str], ink: str,
                     projection_revision: str = ASSET_PROJECTION_CURRENT) -> ValidatedDrawing:
    """The accepted, normalised drawing; raises ``ValueError(reason)`` when it is not acceptable."""
    validate_asset_projection_revision(projection_revision)
    if not isinstance(svg, str) or not svg.strip():
        raise ValueError("vector_illustration_empty")
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError("vector_illustration_too_large")
    # Well-formed comments are inert; anything else declarative is refused.
    svg = re.sub(r"<!--(?:[^-]|-(?!-))*-->", "", svg)
    if re.search(r"<!|<\?", svg):
        raise ValueError("vector_illustration_declaration_forbidden")
    try:
        root = ET.fromstring(svg.strip())
    except ET.ParseError as error:
        raise ValueError("vector_illustration_invalid_xml") from error
    if _local(root.tag) != "svg":
        raise ValueError("vector_illustration_root_not_svg")
    view = [float(value) for value in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if value]
    if len(view) != 4 or view[0] != 0 or view[1] != 0 or abs(view[2] - width) > 0.5 or abs(view[3] - height) > 0.5:
        raise ValueError("vector_illustration_canvas_mismatch")
    elements = 0
    for node in root.iter():
        if node is root:
            continue
        name = _local(node.tag)
        if name not in _SHAPES:
            raise ValueError("vector_illustration_element_forbidden:" + name[:30])
        if (node.text or "").strip() and name not in {"title", "desc"}:
            raise ValueError("vector_illustration_text_forbidden")
        for key in node.attrib:
            if _local(key) in _FORBIDDEN_ATTRIBUTES or "href" in key:
                raise ValueError("vector_illustration_attribute_forbidden:" + _local(key)[:30])
        elements += name not in {"g", "title", "desc"}
    if elements > ELEMENT_CAP:
        raise ValueError("vector_illustration_element_cap")
    try:
        drawing = flatten_icon_svg(svg, color=ink, point_budget=None, tolerance=TOLERANCE, palette=palette,
                                   projection_revision=projection_revision)
    except ValueError as error:
        reason = str(error)
        raise ValueError("vector_illustration_empty" if reason == "icon_empty"
                         else "vector_illustration_unsupported:" + reason[:60]) from error
    if drawing.point_count > POINT_CAP:
        raise ValueError("vector_illustration_point_cap")
    bounds = _bounds(drawing)
    if bounds is None:
        raise ValueError("vector_illustration_empty")
    if (bounds[0] < -EDGE_SLACK or bounds[1] < -EDGE_SLACK
            or bounds[2] > width + EDGE_SLACK or bounds[3] > height + EDGE_SLACK):
        raise ValueError("vector_illustration_overflow")
    for primitive in drawing.primitives:
        if primitive["tag"] in {"rect", "polygon", "path"} and primitive.get("fill", "none") != "none":
            box = _bounds(replace(drawing, primitives=(primitive,)))
            if box and box[2] - box[0] >= width - EDGE_SLACK and box[3] - box[1] >= height - EDGE_SLACK:
                raise ValueError("vector_illustration_background_rectangle")
    drawing = replace(drawing, line_cap="round", line_join="round")
    ET.register_namespace("", SVG_NS)
    group = icon_group(drawing, x=0, y=0, width=float(width), height=float(height))
    normalised = (f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
                  + ET.tostring(group, encoding="unicode") + "</svg>")
    fill_ratio = ((bounds[2] - bounds[0]) * (bounds[3] - bounds[1])) / float(width * height)
    return ValidatedDrawing(svg=normalised, width=width, height=height, elements=len(drawing.primitives),
                            points=drawing.point_count, snapped_paints=drawing.snapped_paints,
                            fill_ratio=round(min(1.0, fill_ratio), 3))


def palette_of(svg: str) -> list[str]:
    """The paint colours a normalised drawing uses (its own palette for re-flattening)."""
    return sorted({color.upper() for color in re.findall(r'(?:fill|stroke)="(#[0-9A-Fa-f]{6})"', svg)})


__all__ = ["ELEMENT_CAP", "POINT_CAP", "ValidatedDrawing", "palette_of", "validate_drawing"]
