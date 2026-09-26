"""A mode-neutral canvas clause without changing legacy style catalogue entries."""
from __future__ import annotations


def canvas_clause(width: int, height: int, safe_area: object | None = None) -> str:
    if safe_area is None:
        return ""
    x, y = float(safe_area.x), float(safe_area.y)
    w, h = float(safe_area.width), float(safe_area.height)
    orientation = "portrait" if height > width else "landscape"
    return (
        "\nDOCUMENT CANVAS (authoritative over example slide dimensions):\n"
        f"Use exactly {width}x{height} logical pixels in {orientation} orientation. "
        f"The content frame is x={x:g}, y={y:g}, width={w:g}, height={h:g}. "
        "Set the HTML root to this size; backgrounds may bleed to its edges. "
        "Recompose text, comparisons, tables and images in this frame. "
        "Style exemplars describe visual language, not this document's page size. "
        "Do not rotate a landscape layout or clip content to simulate portrait. "
        "Use one uniform scale for fixed source artwork and preserve its full content.\n"
    )
