"""Structural (slot-outline) representative render at the pack's own canvas."""

from __future__ import annotations

import html

from .canvases import EMU_PER_PX
from .models import Canvas, DesignFrame


def _render_size(canvas: Canvas) -> tuple[float, float]:
    if canvas.unit == "emu":
        return canvas.width / EMU_PER_PX, canvas.height / EMU_PER_PX
    return canvas.width, canvas.height


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def structural_svg(frame: DesignFrame, canvas: Canvas) -> bytes:
    target_width, target_height = _render_size(canvas)
    sx, sy = target_width / canvas.width, target_height / canvas.height
    boxes: list[str] = []
    for slot in frame.slots:
        bounds = slot.bounds
        boxes.append(
            '<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" '
            'fill="none" stroke="#2563EB" stroke-width="2"/>'.format(
                bounds.x * sx,
                bounds.y * sy,
                bounds.width * sx,
                bounds.height * sy,
            )
        )
        boxes.append(
            '<text x="{:.2f}" y="{:.2f}" font-family="Arial" font-size="12" '
            'fill="#0F172A">{}</text>'.format(
                bounds.x * sx + 4,
                bounds.y * sy + 16,
                html.escape(slot.semantic_role),
            )
        )
    width, height = _number(target_width), _number(target_height)
    document = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect width="{width}" height="{height}" fill="#FFFFFF"/>'
        f'<text x="24" y="32" font-family="Arial" font-size="18" fill="#111827">'
        f'{html.escape(frame.frame_id)} · {html.escape(frame.role)}</text>'
        + "".join(boxes)
        + "</svg>\n"
    )
    return document.encode("utf-8")


__all__ = ["structural_svg"]
