"""The default 1280x720 canvas, named once for every layer.

``models`` may not import ``services``, and ``utils`` may not either, so the
product default lives here as a pure leaf: ``CanvasContract``, the header
metrics, ``services.authored_canvas`` (which re-exports these names) and the
renderer utilities all resolve the default through this module rather than
through a restated literal.
"""

from __future__ import annotations

DEFAULT_CANVAS_WIDTH_PX: int = 1280
DEFAULT_CANVAS_HEIGHT_PX: int = 720
DEFAULT_CANVAS_SIZE_PX: tuple[int, int] = (DEFAULT_CANVAS_WIDTH_PX, DEFAULT_CANVAS_HEIGHT_PX)


def default_canvas_size() -> tuple[int, int]:
    """``(width_px, height_px)`` of the default landscape canvas."""

    return DEFAULT_CANVAS_SIZE_PX


def canvas_or_default(canvas: object | None) -> tuple[int, int]:
    """``(width_px, height_px)`` of a canvas-like object, or the default canvas when absent.

    Any object carrying ``width_px``/``height_px`` (a ``CanvasContract``, a
    deck plan, a metadata model) qualifies; ``None`` and objects without those
    attributes resolve to the default landscape canvas.
    """

    return (
        int(getattr(canvas, "width_px", DEFAULT_CANVAS_WIDTH_PX)),
        int(getattr(canvas, "height_px", DEFAULT_CANVAS_HEIGHT_PX)),
    )


def is_default_landscape(width_px: object, height_px: object) -> bool:
    """True when a pixel size is exactly the default landscape canvas."""

    return (width_px, height_px) == DEFAULT_CANVAS_SIZE_PX


def is_default_landscape_canvas(canvas: object | None) -> bool:
    """True when a canvas-like object (or its absence) means the default canvas."""

    return canvas_or_default(canvas) == DEFAULT_CANVAS_SIZE_PX


def canvas_render_kwargs(width_px: int, height_px: int) -> dict[str, int]:
    """Renderer size keywords: empty on the default canvas so legacy renderers and
    test doubles that take no size keep working, explicit otherwise."""

    if is_default_landscape(width_px, height_px):
        return {}
    return {"width_px": int(width_px), "height_px": int(height_px)}


__all__ = [
    "DEFAULT_CANVAS_HEIGHT_PX",
    "DEFAULT_CANVAS_SIZE_PX",
    "DEFAULT_CANVAS_WIDTH_PX",
    "canvas_or_default",
    "canvas_render_kwargs",
    "default_canvas_size",
    "is_default_landscape",
    "is_default_landscape_canvas",
]
