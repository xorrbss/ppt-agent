"""Physical geometry at generation/export boundaries; never infer A4 from pixels."""
from __future__ import annotations


def export_size_points(canvas: object | None, physical_canvas: object | None = None) -> tuple[float, float]:
    if physical_canvas is not None:
        from services.document_canvas import canvas_physical_size

        width, height = canvas_physical_size(physical_canvas)
        logical = physical_canvas
        if canvas is not None and (
            getattr(canvas, "width_px", None), getattr(canvas, "height_px", None)
        ) != (getattr(logical, "width_px", None), getattr(logical, "height_px", None)):
            raise ValueError("document_canvas_geometry_mismatch")
        return width / 12700, height / 12700
    return (
        int(getattr(canvas, "width_px", 1280)) * 0.75,
        int(getattr(canvas, "height_px", 720)) * 0.75,
    )


def generation_font_floor(instructions: str | None, physical_canvas: object | None = None) -> float:
    """Preserve ordinary physical floor and explicit px/pt intent across renderers."""
    from utils.authored_font_policy import MIN_AUTHORED_FONT_SIZE_PX, requested_font_floor_px

    scale = float(getattr(physical_canvas, "point_per_unit", 0.75))
    requested = requested_font_floor_px(instructions, point_per_unit=scale)
    return requested if requested is not None else MIN_AUTHORED_FONT_SIZE_PX * 0.75 / scale
