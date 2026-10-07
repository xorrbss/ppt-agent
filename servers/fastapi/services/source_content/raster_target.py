"""Freeze output geometry separately from immutable raster source coordinates."""
from types import SimpleNamespace

from services.source_content.raster_contracts_base import RasterTargetCanvas


def raster_target_from_selectors(*, orientation="portrait", aspect_ratio=None, svg_canvas=None):
    if aspect_ratio is None and svg_canvas is None:
        width, height = (840, 1188) if orientation == "portrait" else (1188, 840)
        return RasterTargetCanvas(orientation=orientation, width_units=width, height_units=height)
    from services.svg_canvas import selected_svg_canvas

    canvas = selected_svg_canvas(SimpleNamespace(aspect_ratio=aspect_ratio, svg_canvas=svg_canvas, page_size=None))
    return RasterTargetCanvas(format="SVG", orientation=canvas.orientation,
                              width_units=canvas.width_px, height_units=canvas.height_px,
                              emu_per_unit=canvas.emu_per_unit, document_canvas=canvas,
                              aspect_ratio=aspect_ratio, svg_canvas=svg_canvas)


def raster_target_document_canvas(target):
    from services.document_canvas import document_canvas_for_orientation

    return target.document_canvas or document_canvas_for_orientation(target.orientation)


def raster_target_font_floor(target) -> float:
    from services.document_canvas_generation import source_restyle_font_floor

    if target.format == "A4":
        return 9.5 * 12700 / 9000 + 0.005
    canvas = raster_target_document_canvas(target)
    return max(source_restyle_font_floor(None, canvas), 9.5 / canvas.point_per_unit + 0.005)


def require_raster_physical_readability(svg: str, target) -> None:
    """Fail closed when a new physical target makes composed text illegible."""
    if target.format == "A4":
        return
    from xml.etree import ElementTree

    minimum = raster_target_font_floor(target)
    for node in ElementTree.fromstring(svg).iter():
        if node.tag.rsplit("}", 1)[-1] not in {"text", "tspan"}:
            continue
        font_size = node.attrib.get("font-size")
        if font_size is not None and float(font_size.removesuffix("px")) + 0.01 < minimum:
            raise ValueError("raster_text_requires_below_9_5pt")
