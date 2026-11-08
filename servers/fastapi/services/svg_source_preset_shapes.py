"""Project the bundled DrawingML presets into the source SVG's linear paths."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
import sys
import threading

from services.source_content.pptx_custom_geometry import flatten_custom_geometry


_REGISTRY_IMPORT_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _preset_registry():
    # Isolated workers run outside the checkout. The editable backend package
    # excludes vendor, so use the converter's canonical pptx_shapes import. The
    # temporary sys.path entry is process-global; the lock keeps concurrent first
    # calls from interleaving their insert/remove.
    vendor_root = str(Path(__file__).resolve().parents[1] / "vendor" / "slide_master")
    with _REGISTRY_IMPORT_LOCK:
        sys.path.insert(0, vendor_root)
        try:
            from pptx_shapes import get_preset_registry

            return get_preset_registry()
        finally:
            sys.path.remove(vendor_root)


def supports_source_preset(preset):
    return preset in _preset_registry()


def _arc_points(current, values, scale_x, scale_y):
    """Flatten an OOXML polar-angle ellipse to at most 0.25 CSS-pixel error."""
    radius_x, radius_y, start, sweep = values
    radius_x, radius_y = abs(radius_x), abs(radius_y)
    rx, ry = radius_x * scale_x, radius_y * scale_y
    if min(rx, ry) <= 1e-12 or abs(sweep) <= 1e-12:
        return []

    def parameter(angle):
        radians = math.radians(angle / 60000)
        return math.atan2(radius_x * math.sin(radians), radius_y * math.cos(radians))

    angle = parameter(start)
    center = current[0] - rx * math.cos(angle), current[1] - ry * math.sin(angle)
    # Split at half turns, matching the bundled SVG adapter's angle handling.
    remaining = sweep
    points = []
    while abs(remaining) > 1e-9:
        step = math.copysign(min(abs(remaining), 10800000), remaining)
        start += step
        end = parameter(start)
        delta = (end - angle) % math.tau if step > 0 else -((angle - end) % math.tau)
        max_step = 2 * math.acos(max(-1, 1 - 0.25 / max(rx, ry)))
        count = max(1, math.ceil(abs(delta) / max(max_step, 1e-9)))
        if len(points) + count > 2048:
            raise ValueError("svg_source_custom_geometry_complexity_limit")
        points.extend([
            center[0] + rx * math.cos(angle + delta * index / count),
            center[1] + ry * math.sin(angle + delta * index / count),
        ] for index in range(1, count + 1))
        angle = end
        remaining -= step
    return points


def _linear_path(path, *, x, y, width, height):
    scale_x = width / path.coordinate_width if path.coordinate_width else 1
    scale_y = height / path.coordinate_height if path.coordinate_height else 1
    commands = []
    current = origin = (x, y)
    arities = {"moveTo": 1, "lnTo": 1, "quadBezTo": 2, "cubicBezTo": 3, "close": 0}
    for command in path.commands:
        values = command.parameters
        if command.name == "arcTo":
            points = _arc_points(current, values, scale_x, scale_y)
            commands.extend(dict(command="lnTo", points=[point]) for point in points)
            if points:
                current = points[-1]
            continue
        if command.name not in arities or len(values) != arities[command.name] * 2:
            raise ValueError("svg_source_custom_geometry_command_unsupported")
        points = [[x + values[i] * scale_x, y + values[i + 1] * scale_y]
                  for i in range(0, len(values), 2)]
        commands.append(dict(command=command.name, points=points))
        if command.name == "moveTo":
            current = origin = points[0]
        elif command.name == "close":
            current = origin
        else:
            current = points[-1]
    return flatten_custom_geometry(dict(version="numeric-custom-path/v1", width=1, height=1,
        fill="norm", stroke=True, commands=commands), x=0, y=0, width=1, height=1)


def _fill(color, mode):
    if mode == "none":
        return "none"
    if mode == "norm" or color == "none":
        return color
    color = {"black": "#000000", "white": "#FFFFFF"}.get(color, color)
    if len(color) == 4 and color.startswith("#"):
        color = "#" + "".join(value * 2 for value in color[1:])
    channels = [int(color[offset:offset + 2], 16) for offset in (1, 3, 5)]
    if mode in {"darken", "darkenLess"}:
        factor = 0.65 if mode == "darken" else 0.82
        channels = [round(value * factor) for value in channels]
    elif mode in {"lighten", "lightenLess"}:
        amount = 0.4 if mode == "lighten" else 0.2
        channels = [round(value + (255 - value) * amount) for value in channels]
    else:
        raise ValueError("svg_source_custom_geometry_fill_unsupported")
    return "#" + "".join(f"{value:02X}" for value in channels)


def source_preset_paths(preset, *, x, y, width, height, background, stroke):
    """Keep every fill/stroke layer, including curves, holes and open outlines."""
    evaluated = _preset_registry().evaluate(preset, width, height)
    return tuple(dict(d=_linear_path(path, x=x, y=y, width=width, height=height),
                      fill=_fill(background, path.fill), stroke=stroke if path.stroke else "none")
                 for path in evaluated.paths if path.commands)
