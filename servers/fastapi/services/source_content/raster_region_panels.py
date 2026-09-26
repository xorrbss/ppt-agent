"""Panel and border heuristics for raster images."""

from __future__ import annotations

from PIL import Image, ImageFilter


def _intersection(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))


def _line_segments(values: list[int], minimum: int) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    start = -1
    for index, value in enumerate(values + [0]):
        if value and start < 0:
            start = index
        elif not value and start >= 0:
            if index - start >= minimum:
                segments.append((start, index))
            start = -1
    return segments


def _background_panel_candidates(image: Image.Image, scale: float) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Suggest large non-background panels, including borderless UI captures."""

    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    small = image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
    corner = [small.getpixel((x, y)) for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))]
    background = tuple(sorted(pixel[channel] for pixel in corner)[2] for channel in range(3))
    raw = small.load()
    mask = Image.new("L", (width, height))
    marked = mask.load()
    for y in range(height):
        for x in range(width):
            color = raw[x, y]
            if max(abs(color[channel] - background[channel]) for channel in range(3)) > 42:
                marked[x, y] = 255
    # Join panel fills and borders while leaving separate page bands distinct.
    mask = mask.filter(ImageFilter.MaxFilter(5))
    bitmap = mask.load()
    visited = bytearray(width * height)
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for y in range(height):
        for x in range(width):
            start = y * width + x
            if not bitmap[x, y] or visited[start]:
                continue
            visited[start] = 1
            stack = [start]
            count = 0
            x0 = x1 = x
            y0 = y1 = y
            while stack:
                cell = stack.pop()
                cx, cy = cell % width, cell // width
                count += 1
                x0, x1 = min(x0, cx), max(x1, cx)
                y0, y1 = min(y0, cy), max(y1, cy)
                for neighbor in (
                    cell - 1 if cx else -1,
                    cell + 1 if cx + 1 < width else -1,
                    cell - width if cy else -1,
                    cell + width if cy + 1 < height else -1,
                ):
                    if neighbor < 0 or visited[neighbor]:
                        continue
                    nx, ny = neighbor % width, neighbor // width
                    if bitmap[nx, ny]:
                        visited[neighbor] = 1
                        stack.append(neighbor)
            box_width, box_height = x1 - x0 + 1, y1 - y0 + 1
            area_ratio = box_width * box_height / (width * height)
            density = count / (box_width * box_height)
            if (
                box_width >= width * 0.18
                and box_height >= height * 0.08
                and 0.035 <= area_ratio <= 0.85
                and density >= 0.16
            ):
                candidates.append((area_ratio * density, (x0, y0, box_width, box_height)))
    return candidates


def _dark_toolbar_candidates(image: Image.Image, scale: float) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Locate screenshot panels with a dark horizontal application toolbar."""

    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    small = image.convert("L").resize((width, height), Image.Resampling.BILINEAR)
    pixels = small.load()
    rows: list[tuple[int, int, int]] = []
    for y in range(height):
        fragments = _line_segments([1 if pixels[x, y] < 180 else 0 for x in range(width)], 4)
        segments: list[tuple[int, int]] = []
        for x0, x1 in fragments:
            if segments and x0 - segments[-1][1] <= 12:
                segments[-1] = (segments[-1][0], x1)
            else:
                segments.append((x0, x1))
        for x0, x1 in segments:
            if width * 0.14 <= x1 - x0 <= width * 0.82:
                rows.append((y, x0, x1))
    groups: list[list[tuple[int, int, int]]] = []
    for row in rows:
        match = next(
            (
                group
                for group in reversed(groups)
                if row[0] - group[-1][0] <= 1 and abs(row[1] - group[-1][1]) <= 10 and abs(row[2] - group[-1][2]) <= 10
            ),
            None,
        )
        if match is None:
            groups.append([row])
        else:
            match.append(row)
    result: list[tuple[float, tuple[int, int, int, int]]] = []
    for group in groups:
        if len(group) < 5:
            continue
        top = max(0, group[0][0] - 2)
        if top < height * 0.11 or top > height * 0.78:
            continue
        left = min(row[1] for row in group)
        right = max(row[2] for row in group)
        panel_width = right - left
        if panel_width < width * 0.20:
            continue
        minimum_ratio = 1.08 if panel_width < width * 0.40 else 0.67
        minimum_end = min(height, top + round(panel_width * minimum_ratio))
        maximum_end = min(height, top + round(panel_width * 1.30))
        empty = 0
        bottom = maximum_end
        for y in range(group[-1][0] + 1, maximum_end):
            active = sum(1 for x in range(left, right) if pixels[x, y] < 220)
            if y >= minimum_end and active < panel_width * 0.012:
                empty += 1
                if empty >= 8:
                    bottom = min(height, y - empty + 10)
                    break
            else:
                empty = 0
        if bottom <= minimum_end:
            continue
        result.append((1.0 + panel_width * (bottom - top) / (width * height), (left, top, panel_width, bottom - top)))
    # Repeated application panels in one visual row normally share a lower
    # edge. A common edge prevents one white form gap from truncating a crop.
    adjusted = list(result)
    for index, (_, box) in enumerate(result):
        left, top, panel_width, _ = box
        peers = [item for item in result if abs(item[1][1] - top) <= 15 and width * 0.20 <= item[1][2] < width * 0.40]
        if len(peers) < 2 or panel_width >= width * 0.40:
            continue
        widths = sorted(item[1][2] for item in peers)
        common_bottom = min(height, max(item[1][1] for item in peers) + round(widths[len(widths) // 2] * 1.10))
        adjusted[index] = (result[index][0], (left, top, panel_width, common_bottom - top))
    completed: list[tuple[float, tuple[int, int, int, int]]] = []
    for score, (left, top, panel_width, panel_height) in adjusted:
        card_bottom = _border_card_bottom(image, scale, left, top, panel_width)
        if card_bottom is not None:
            panel_height = max(panel_height, card_bottom - top)
        completed.append((score, (left, top, panel_width, min(height - top, panel_height))))
    selected: list[tuple[float, tuple[int, int, int, int]]] = []
    for item in sorted(completed, key=lambda value: (value[1][1], value[1][0])):
        box = item[1]
        if any(
            abs(box[0] - old[1][0]) <= 8
            and abs(box[2] - old[1][2]) <= 8
            and _intersection(box, old[1]) / min(box[2] * box[3], old[1][2] * old[1][3]) > 0.80
            for old in selected
        ):
            continue
        selected.append(item)
    return selected


def _border_card_bottom(image: Image.Image, scale: float, left: int, top: int, width: int) -> int | None:
    """Extend a dark-toolbar crop to a corroborated two-sided card border."""

    source = image.convert("RGB")
    x_left = round(left / scale)
    x_right = round((left + width) / scale)
    y_top = round(top / scale)
    source_width = max(1, x_right - x_left)
    sample_start = y_top + max(38, round(source_width * 0.08))
    sample_end = min(source.height - 1, sample_start + max(45, round(source_width * 0.14)))
    if sample_end <= sample_start:
        return None

    def edge(x: int, y: int) -> int:
        left_pixel = source.getpixel((x - 3, y))
        right_pixel = source.getpixel((x + 3, y))
        return sum(abs(a - b) for a, b in zip(left_pixel, right_pixel, strict=False))

    borders: list[tuple[float, int]] = []
    for expected in (x_left, x_right):
        radius = max(8, round(source_width * 0.035))
        choices = range(max(3, expected - radius), min(source.width - 3, expected + radius + 1))
        samples = range(sample_start, sample_end, 9)
        ranked = [(sum(edge(x, y) for y in samples) / len(samples), x) for x in choices]
        borders.append(max(ranked, default=(0.0, expected)))
    if min(item[0] for item in borders) < 65:
        return None
    left_x, right_x = borders[0][1], borders[1][1]
    unsupported = 0
    last_supported = sample_start
    for y in range(sample_start, source.height - 2):
        if edge(left_x, y) >= 48 and edge(right_x, y) >= 48:
            last_supported = y
            unsupported = 0
        elif y - sample_start > source_width * 0.25:
            unsupported += 1
            if unsupported >= 18:
                break
    if last_supported <= sample_start + source_width * 0.25:
        return None
    return min(round((last_supported + 12) * scale), round(source.height * scale))
