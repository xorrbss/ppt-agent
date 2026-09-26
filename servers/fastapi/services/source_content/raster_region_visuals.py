"""Screenshot and visual region heuristics."""

from __future__ import annotations

from collections import deque

from PIL import Image, ImageFilter

from services.source_content.raster_region_types import RegionCandidate
from services.source_content.raster_region_panels import (
    _background_panel_candidates,
    _dark_toolbar_candidates,
    _intersection,
    _line_segments,
)


def _horizontal_edges(edges: Image.Image) -> list[tuple[int, int, int]]:
    pixels = edges.load()
    width, height = edges.size
    found: list[tuple[int, int, int]] = []
    for y in range(height):
        values = [1 if pixels[x, y] >= 65 else 0 for x in range(width)]
        for x0, x1 in _line_segments(values, max(12, width // 7)):
            found.append((y, x0, x1))
    return found


def _vertical_support(edges: Image.Image, x: int, y0: int, y1: int) -> float:
    pixels = edges.load()
    width, height = edges.size
    lo, hi = max(0, y0), min(height, y1)
    if hi <= lo:
        return 0.0
    count = 0
    for y in range(lo, hi):
        if max(pixels[max(0, x - 1), y], pixels[x, y], pixels[min(width - 1, x + 1), y]) >= 65:
            count += 1
    return count / (hi - lo)


def screenshot_rectangles(image: Image.Image) -> tuple[RegionCandidate, ...]:
    """Find framed rectangular crops; no candidate implies no screenshot claim."""

    scale = min(1.0, 640 / max(image.size))
    small = image.convert("L").resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.BILINEAR,
    )
    edges = small.filter(ImageFilter.FIND_EDGES)
    # Very dense UI text produces thousands of short edges. Long borders are
    # more useful and keep pair search bounded independently of image detail.
    lines = sorted(_horizontal_edges(edges), key=lambda item: item[2] - item[1], reverse=True)[:240]
    lines.sort(key=lambda item: item[0])
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for i, (top, left, right) in enumerate(lines):
        for bottom, lower_left, lower_right in lines[i + 1 :]:
            height = bottom - top
            if height < small.height * 0.12:
                continue
            if height > small.height * 0.94:
                break
            shared_left, shared_right = max(left, lower_left), min(right, lower_right)
            width = shared_right - shared_left
            if width < small.width * 0.16 or width * height < small.width * small.height * 0.035:
                continue
            if abs(left - lower_left) > small.width * 0.015 or abs(right - lower_right) > small.width * 0.015:
                continue
            support = min(
                _vertical_support(edges, shared_left, top, bottom),
                _vertical_support(edges, shared_right - 1, top, bottom),
            )
            if support < 0.30:
                continue
            # A framed crop is a suggestion only; dense tables can also satisfy it.
            score = support * (width * height / (small.width * small.height))
            candidates.append((score, (shared_left, top, width, height)))
    candidates.extend(_dark_toolbar_candidates(image, scale))
    candidates.extend(_background_panel_candidates(image, scale))
    candidates.sort(key=lambda item: item[0], reverse=True)
    retained: list[tuple[int, int, int, int]] = []
    for _, box in candidates:
        if any(_intersection(box, old) / min(box[2] * box[3], old[2] * old[3]) > 0.30 for old in retained):
            continue
        retained.append(box)
        if len(retained) == 8:
            break
    result = []
    for x, y, width, height in sorted(retained, key=lambda b: (b[1], b[0])):
        x0 = max(0, round(x / scale))
        y0 = max(0, round(y / scale))
        x1 = min(image.width, round((x + width) / scale))
        y1 = min(image.height, round((y + height) / scale))
        result.append(RegionCandidate("screenshot", (x0, y0, x1 - x0, y1 - y0), reason="framed_rectangle_heuristic"))
    if len(result) <= 1 and not any(
        region.bbox[2] * region.bbox[3] >= image.width * image.height * 0.20 for region in result
    ):
        envelope = _right_visual_envelope(image)
        if envelope is not None:
            result = [envelope] + [
                region
                for region in result
                if _intersection(region.bbox, envelope.bbox) < region.bbox[2] * region.bbox[3] * 0.30
            ]
    for icon in _colored_icon_candidates(image):
        if any(_intersection(icon.bbox, region.bbox) >= icon.bbox[2] * icon.bbox[3] * 0.60 for region in result):
            continue
        result.append(icon)
    return tuple(result)


def _right_visual_envelope(image: Image.Image) -> RegionCandidate | None:
    """Suggest a broad right-side visual when conventional frame edges are absent."""

    width, height = image.size
    pixels = image.convert("RGB").load()
    stride = max(2, round(width / 560))
    left, top, right, bottom = width, height, 0, 0
    count = 0
    for y in range(round(height * 0.15), round(height * 0.94), stride):
        for x in range(round(width * 0.45), width, stride):
            red, green, blue = pixels[x, y]
            if max(red, green, blue) - min(red, green, blue) <= 25 or min(red, green, blue) >= 230:
                continue
            left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
            count += 1
    if count < 1000:
        return None
    region_width, region_height = right - left, bottom - top
    area_ratio = region_width * region_height / (width * height)
    if region_width < width * 0.40 or region_height < height * 0.40 or not 0.18 <= area_ratio <= 0.60:
        return None
    x0 = max(0, left - round(width * 0.003))
    y0 = max(0, top - round(height * 0.014))
    x1 = min(width, right + round(width * 0.003))
    y1 = min(height, bottom + round(height * 0.038))
    return RegionCandidate(
        "screenshot",
        (x0, y0, x1 - x0, y1 - y0),
        reason="right_visual_envelope_boundary_unverified",
    )


def _colored_icon_candidates(image: Image.Image) -> tuple[RegionCandidate, ...]:
    """Keep large saturated icons as reviewable pixel crops outside panels."""

    scale = min(1.0, 560 / max(image.size))
    small = image.convert("RGB").resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.BILINEAR,
    )
    width, height = small.size
    pixels = small.load()
    visited = bytearray(width * height)
    candidates: list[RegionCandidate] = []

    def colored(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        return (
            max(red, green, blue) - min(red, green, blue) >= 65
            and max(red, green, blue) >= 135
            and min(red, green, blue) <= 135
        )

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or not colored(x, y):
                continue
            visited[index] = 1
            queue = deque([(x, y)])
            left = right = x
            top = bottom = y
            count = 0
            while queue:
                current_x, current_y = queue.popleft()
                count += 1
                left, right = min(left, current_x), max(right, current_x)
                top, bottom = min(top, current_y), max(bottom, current_y)
                for next_x, next_y in (
                    (current_x - 1, current_y),
                    (current_x + 1, current_y),
                    (current_x, current_y - 1),
                    (current_x, current_y + 1),
                ):
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    next_index = next_y * width + next_x
                    if visited[next_index] or not colored(next_x, next_y):
                        continue
                    visited[next_index] = 1
                    queue.append((next_x, next_y))
            box_width, box_height = right - left + 1, bottom - top + 1
            if not (
                20 <= box_width <= 70
                and 20 <= box_height <= 70
                and 0.65 <= box_width / box_height <= 1.40
                and count / (box_width * box_height) > 0.35
            ):
                continue
            x0 = max(0, round(left / scale) - 10)
            y0 = max(0, round(top / scale) - 10)
            x1 = min(image.width, round((right + 1) / scale) + 10)
            y1 = min(image.height, round((bottom + 1) / scale) + 10)
            candidates.append(
                RegionCandidate(
                    "screenshot",
                    (x0, y0, x1 - x0, y1 - y0),
                    reason="colored_icon_boundary_unverified",
                )
            )
            if len(candidates) >= 12:
                return tuple(candidates)
    return tuple(candidates)
