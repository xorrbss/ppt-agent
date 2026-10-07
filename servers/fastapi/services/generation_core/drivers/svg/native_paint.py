"""What a PowerPoint native table must paint to look like the drawn one.

The export projection makes every native cell explicit: a cell's fill is
opaque (``#FFFFFF`` when V1 has none) and an edge without a V1 border has no
line. So the published payload must carry the *effective* appearance:

* **Cell fill** (:func:`effective_fill`): the colour under the cell centre,
  compositing in paint (document) order, over the white canvas, every filled
  element of the whole slide painted before the table's texts -- every element
  before the declared group, plus the group's own rects (a table group holds
  only rect/line/text). Each rect contributes its fill at ``fill-opacity`` x the
  ``opacity`` product of it and its ancestors. A filled non-rect shape or an
  ``<image>`` whose bounding box contains the point makes the background not a
  solid colour, unless a later fully opaque rect covers the point again;
  otherwise the table is rejected (``native_table_background_not_solid``).
* **Cell borders** (:func:`edge_border`): from the group's own strokes only (a
  line, or a rect's stroke edge; the group is replaced natively, everything else
  stays drawn). The topmost stroke collinear with a cell edge and overlapping it
  must cover the whole edge (``native_table_border_partial`` otherwise); its
  colour is composited at ``stroke-opacity`` x ``opacity`` over the cell's
  effective fill (V1 borders are opaque) and its width is ``stroke-width``
  (default 1) x points per px, within V1's (0, 20] pt
  (``native_table_border_invalid`` otherwise). No stroke: no border.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_geometry import (
    Box,
    NativeRejection,
    Shape,
    inherit,
    shape_of,
    tag_of,
    _compose_transform,
    _transform_of,
    transform_shape,
)

_WHITE = (255, 255, 255)
_TOLERANCE = 1.5
_NON_SOLID = frozenset({"path", "polygon", "polyline", "circle", "ellipse", "image"})


@dataclass(frozen=True)
class Stroke:
    horizontal: bool
    at: float  # the y of a horizontal stroke, the x of a vertical one
    start: float
    end: float
    color: str
    alpha: float
    width: float


def background_layers(root: ET.Element, group: ET.Element) -> list[Shape]:
    """Filled shapes painted under the table's texts, in paint order."""
    layers: list[Shape] = []

    def visit(node, offset, attrs, inside) -> bool:
        tag = tag_of(node)
        position = _compose_transform(offset, _transform_of(node))
        attrs = inherit(attrs, node)
        if tag in ("svg", "g"):
            entering = inside or node is group
            for child in node:
                if visit(child, position, attrs, entering):
                    return True
            return node is group  # stop once the table group has been painted
        if tag in ("text", "line") or (inside and tag != "rect"):
            return False
        if position[2] != 1.0 and node.get("vector-effect", "none") != "none":
            raise NativeRejection("native_group_transform_unsupported")
        shape = transform_shape(shape_of(node, tag, 0.0, 0.0, attrs), position)
        if tag == "image" or (shape.fill is not None and shape.fill_alpha > 0):
            layers.append(shape)
        return False

    visit(root, (0.0, 0.0, 1.0), {}, False)
    return layers


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _mix(base: tuple[int, int, int], color: str, alpha: float) -> tuple[int, int, int]:
    alpha = min(max(alpha, 0.0), 1.0)
    # Round half up (not Python's half-to-even) so every reader derives the same colour.
    return tuple(int(b * (1 - alpha) + c * alpha + 0.5) for b, c in zip(base, _rgb(color), strict=True))


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{value:02X}" for value in rgb)


def effective_fill(layers: list[Shape], x: float, y: float) -> str:
    rgb, solid = _WHITE, True
    for layer in layers:
        if not layer.box.contains(x, y, 0.0):
            continue
        if layer.tag in _NON_SOLID:
            solid = False
        elif layer.tag == "rect":
            rgb = _mix(rgb, layer.fill, layer.fill_alpha)
            solid = solid or layer.fill_alpha >= 1.0
    if not solid:
        raise NativeRejection("native_table_background_not_solid")
    return _hex(rgb)


def group_strokes(shapes: tuple[Shape, ...]) -> list[Stroke]:
    """Straight stroke segments of the group, in paint order."""
    strokes: list[Stroke] = []
    for shape in shapes:
        if shape.stroke is None or shape.stroke_width <= 0 or shape.stroke_alpha <= 0:
            continue
        box, style = shape.box, (shape.stroke, shape.stroke_alpha, shape.stroke_width)
        if shape.tag == "rect":
            strokes += [Stroke(True, box.y0, box.x0, box.x1, *style), Stroke(True, box.y1, box.x0, box.x1, *style),
                        Stroke(False, box.x0, box.y0, box.y1, *style), Stroke(False, box.x1, box.y0, box.y1, *style)]
        elif shape.tag == "line" and box.height <= 0.5:
            strokes.append(Stroke(True, box.y0, box.x0, box.x1, *style))
        elif shape.tag == "line" and box.width <= 0.5:
            strokes.append(Stroke(False, box.x0, box.y0, box.y1, *style))
    return strokes


def edge_border(strokes: list[Stroke], *, horizontal: bool, at: float, start: float, end: float,
                under: str, pt_per_px: float) -> dict | None:
    """V1 ``{"color", "widthPt"}`` of one cell edge, or ``None`` when nothing strokes it."""
    top = None
    for stroke in strokes:
        if (stroke.horizontal == horizontal and abs(stroke.at - at) <= _TOLERANCE
                and min(stroke.end, end) - max(stroke.start, start) > _TOLERANCE):
            top = stroke
    if top is None:
        return None
    if top.start > start + _TOLERANCE or top.end < end - _TOLERANCE:
        raise NativeRejection("native_table_border_partial")
    width = round(top.width * pt_per_px, 3)
    if not 0 < width <= 20:
        raise NativeRejection("native_table_border_invalid")
    return {"color": _hex(_mix(_rgb(under), top.color, top.alpha)), "widthPt": width}


def cell_borders(strokes: list[Stroke], cell: Box, *, under: str, pt_per_px: float) -> dict:
    edges = {"top": (True, cell.y0, cell.x0, cell.x1), "right": (False, cell.x1, cell.y0, cell.y1),
             "bottom": (True, cell.y1, cell.x0, cell.x1), "left": (False, cell.x0, cell.y0, cell.y1)}
    borders = {}
    for name, (horizontal, at, start, end) in edges.items():
        border = edge_border(strokes, horizontal=horizontal, at=at, start=start, end=end,
                             under=under, pt_per_px=pt_per_px)
        if border is not None:
            borders[name] = border
    return borders
