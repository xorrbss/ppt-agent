"""Small numerical helpers used by repair projections."""

from __future__ import annotations

from collections.abc import Mapping
import math


def off_canvas_fraction(metric: Mapping[str, object], *, canvas_width: float, canvas_height: float) -> float:
    bbox = metric.get("bbox")
    bbox = bbox if isinstance(bbox, Mapping) else {}

    def number(value: object) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        return result if math.isfinite(result) else 0.0

    x, y = number(bbox.get("x")), number(bbox.get("y"))
    width, height = number(bbox.get("width")), number(bbox.get("height"))
    return max(
        max(0.0, -x, x + width - canvas_width) / max(1.0, canvas_width),
        max(0.0, -y, y + height - canvas_height) / max(1.0, canvas_height),
    )
