"""Conservative, local-only region suggestions for a raster slide.

Every text and rectangle returned here is a draft. OCR confidence is an engine
score, never evidence that the recognized text matches the source.
"""

from __future__ import annotations

import csv
import io
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter


@dataclass(frozen=True)
class OCRWord:
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float | None
    line_key: tuple[int, int, int]


@dataclass(frozen=True)
class RegionCandidate:
    role: str
    bbox: tuple[int, int, int, int]
    text: str | None = None
    word_indices: tuple[int, ...] = ()
    reason: str = ""


def full_page_ocr(
    image: Image.Image,
    *,
    executable: Path,
    tessdata: Path,
    timeout_seconds: int = 60,
) -> tuple[OCRWord, ...]:
    """Inventory all visible OCR words, including text inside screenshots."""

    data = io.BytesIO()
    image.convert("RGB").save(data, format="PNG")
    command = [
        str(executable), "stdin", "stdout", "--tessdata-dir", str(tessdata),
        "-l", "kor+eng", "--psm", "11", "-c", "tessedit_create_tsv=1",
        "-c", "tessedit_create_txt=0",
    ]
    result = subprocess.run(
        command, input=data.getvalue(), capture_output=True,
        timeout=timeout_seconds, check=False,
    )
    if result.returncode:
        raise RuntimeError("local_ocr_failed")
    reader = csv.DictReader(io.StringIO(result.stdout.decode("utf-8-sig", "replace")), delimiter="\t")
    required = {"left", "top", "width", "height", "text", "block_num", "par_num", "line_num"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise RuntimeError("local_ocr_invalid_tsv")
    words: list[OCRWord] = []
    for row in reader:
        value = (row.get("text") or "").strip()
        if not value:
            continue
        try:
            x, y, width, height = (int(row[name]) for name in ("left", "top", "width", "height"))
            key = tuple(int(row[name]) for name in ("block_num", "par_num", "line_num"))
            raw_confidence = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            continue
        if x + width > image.width or y + height > image.height:
            continue
        words.append(OCRWord(
            value, (x, y, width, height),
            max(0.0, min(1.0, raw_confidence / 100)) if raw_confidence >= 0 else None,
            key,
        ))
        if len(words) > 5000:
            raise RuntimeError("local_ocr_token_limit")
    return tuple(words)


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
        for bottom, lower_left, lower_right in lines[i + 1:]:
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
        region.bbox[2] * region.bbox[3] >= image.width * image.height * 0.20
        for region in result
    ):
        envelope = _right_visual_envelope(image)
        if envelope is not None:
            result = [envelope] + [
                region for region in result
                if _intersection(region.bbox, envelope.bbox) < region.bbox[2] * region.bbox[3] * 0.30
            ]
    for icon in _colored_icon_candidates(image):
        if any(
            _intersection(icon.bbox, region.bbox) >= icon.bbox[2] * icon.bbox[3] * 0.60
            for region in result
        ):
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
        "screenshot", (x0, y0, x1 - x0, y1 - y0),
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
        return max(red, green, blue) - min(red, green, blue) >= 65 and max(red, green, blue) >= 135 and min(red, green, blue) <= 135

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
                    (current_x - 1, current_y), (current_x + 1, current_y),
                    (current_x, current_y - 1), (current_x, current_y + 1),
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
                20 <= box_width <= 70 and 20 <= box_height <= 70
                and 0.65 <= box_width / box_height <= 1.40
                and count / (box_width * box_height) > 0.35
            ):
                continue
            x0 = max(0, round(left / scale) - 10)
            y0 = max(0, round(top / scale) - 10)
            x1 = min(image.width, round((right + 1) / scale) + 10)
            y1 = min(image.height, round((bottom + 1) / scale) + 10)
            candidates.append(RegionCandidate(
                "screenshot", (x0, y0, x1 - x0, y1 - y0),
                reason="colored_icon_boundary_unverified",
            ))
            if len(candidates) >= 12:
                return tuple(candidates)
    return tuple(candidates)


def ocr_density_rectangles(
    image: Image.Image, words: tuple[OCRWord, ...],
) -> tuple[RegionCandidate, ...]:
    """Suggest dense text-bearing panels when a UI capture has no visible frame.

    The OCR inventory remains independent. These candidates are deliberately
    conservative and always require a human boundary check.
    """

    if len(words) < 12:
        return ()
    lines: dict[tuple[int, int, int], list[OCRWord]] = {}
    for word in words:
        lines.setdefault(word.line_key, []).append(word)
    boxes: list[tuple[int, int, int, int, int]] = []
    for line_words in lines.values():
        x0 = min(word.bbox[0] for word in line_words)
        y0 = min(word.bbox[1] for word in line_words)
        x1 = max(word.bbox[0] + word.bbox[2] for word in line_words)
        y1 = max(word.bbox[1] + word.bbox[3] for word in line_words)
        boxes.append((x0, y0, x1, y1, len(line_words)))
    boxes.sort(key=lambda box: (box[1], box[0]))
    parent = list(range(len(boxes)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, first in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            second = boxes[j]
            if second[1] - first[3] > 48:
                break
            if second[1] < first[1] - 20:
                continue
            overlap = max(0, min(first[2], second[2]) - max(first[0], second[0]))
            lesser_width = min(first[2] - first[0], second[2] - second[0])
            if overlap >= lesser_width * 0.28 or abs(first[0] - second[0]) <= 24:
                parent[root(j)] = root(i)
    groups: dict[int, list[tuple[int, int, int, int, int]]] = {}
    for index, box in enumerate(boxes):
        groups.setdefault(root(index), []).append(box)
    candidates: list[RegionCandidate] = []
    for group in groups.values():
        if len(group) < 5 or sum(box[4] for box in group) < 18:
            continue
        x0 = max(0, min(box[0] for box in group) - 22)
        y0 = max(0, min(box[1] for box in group) - 22)
        x1 = min(image.width, max(box[2] for box in group) + 22)
        y1 = min(image.height, max(box[3] for box in group) + 22)
        width, height = x1 - x0, y1 - y0
        area = width * height / (image.width * image.height)
        if width >= image.width * 0.20 and height >= image.height * 0.10 and 0.025 <= area <= 0.88:
            candidates.append(RegionCandidate(
                "screenshot", (x0, y0, width, height),
                reason="ocr_density_capture_boundary_unverified",
            ))
    candidates.sort(key=lambda item: item.bbox[2] * item.bbox[3], reverse=True)
    retained: list[RegionCandidate] = []
    for candidate in candidates:
        if any(
            _intersection(candidate.bbox, other.bbox) / min(
                candidate.bbox[2] * candidate.bbox[3], other.bbox[2] * other.bbox[3],
            ) > 0.20
            for other in retained
        ):
            continue
        retained.append(candidate)
        if len(retained) >= 8:
            break
    return tuple(retained)


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
                for neighbor in (cell - 1 if cx else -1, cell + 1 if cx + 1 < width else -1,
                                 cell - width if cy else -1, cell + width if cy + 1 < height else -1):
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
                box_width >= width * 0.18 and box_height >= height * 0.08
                and 0.035 <= area_ratio <= 0.85 and density >= 0.16
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
        match = next((group for group in reversed(groups)
                      if row[0] - group[-1][0] <= 1
                      and abs(row[1] - group[-1][1]) <= 10
                      and abs(row[2] - group[-1][2]) <= 10), None)
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
        return sum(abs(a - b) for a, b in zip(left_pixel, right_pixel))

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


def text_and_table_candidates(
    words: tuple[OCRWord, ...],
    screenshots: tuple[RegionCandidate, ...],
) -> tuple[tuple[RegionCandidate, ...], tuple[str, ...]]:
    """Group only OCR words outside candidates; ambiguous intersections stay unresolved."""

    outside: dict[tuple[int, int, int], list[int]] = {}
    issues: set[str] = set()
    for index, word in enumerate(words):
        area = word.bbox[2] * word.bbox[3]
        overlaps = [_intersection(word.bbox, region.bbox) / area for region in screenshots]
        if sum(ratio > 0 for ratio in overlaps) > 1 or any(0 < ratio < 0.98 for ratio in overlaps):
            issues.add("ocr_screenshot_boundary_ambiguous")
            continue
        if any(ratio >= 0.98 for ratio in overlaps):
            continue
        outside.setdefault(word.line_key, []).append(index)
    lines: list[RegionCandidate] = []
    for indices in outside.values():
        ordered = sorted(indices, key=lambda index: words[index].bbox[0])
        x0 = min(words[index].bbox[0] for index in ordered)
        y0 = min(words[index].bbox[1] for index in ordered)
        x1 = max(words[index].bbox[0] + words[index].bbox[2] for index in ordered)
        y1 = max(words[index].bbox[1] + words[index].bbox[3] for index in ordered)
        lines.append(RegionCandidate(
            "body", (x0, y0, x1 - x0, y1 - y0),
            text=" ".join(words[index].text for index in ordered),
            word_indices=tuple(ordered), reason="outside_ocr_line_candidate",
        ))
    lines.sort(key=lambda region: (region.bbox[1], region.bbox[0]))
    # OCR line alignment suggests a table, but cell grid and merge status remain unverified.
    table_lines: set[int] = set()
    for index, line in enumerate(lines):
        nearby = [
            other for other in range(index + 1, len(lines))
            if 0 < lines[other].bbox[1] - line.bbox[1] < max(24, line.bbox[3] * 5)
            and abs(lines[other].bbox[0] - line.bbox[0]) < max(8, line.bbox[3])
        ]
        aligned = [index, *nearby[:2]]
        if len(nearby) >= 2 and all(len(lines[item].word_indices) >= 2 for item in aligned):
            second_columns = [
                words[sorted(lines[item].word_indices, key=lambda token: words[token].bbox[0])[1]].bbox[0]
                for item in aligned
            ]
            if max(second_columns) - min(second_columns) <= max(12, line.bbox[3]):
                table_lines.update(aligned)
    if table_lines:
        issues.add("table_grid_and_cells_unverified")
    grouped: list[list[RegionCandidate]] = []
    for index, line in enumerate(lines):
        role = "table" if index in table_lines else line.role
        current = RegionCandidate(role, line.bbox, line.text, line.word_indices, line.reason)
        if grouped and role == "body" and grouped[-1][-1].role == "body":
            previous = grouped[-1][-1]
            px, py, pw, ph = previous.bbox
            x, y, width, height = current.bbox
            vertical_gap = y - py - ph
            horizontal_overlap = max(0, min(px + pw, x + width) - max(px, x))
            if (
                -min(ph, height) * 0.25 <= vertical_gap <= max(12, min(ph, height) * 1.6)
                and abs(x - px) <= max(22, min(ph, height) * 2)
                and horizontal_overlap >= min(pw, width) * 0.35
            ):
                grouped[-1].append(current)
                continue
        grouped.append([current])
    regions = []
    for group in grouped:
        if len(group) == 1:
            regions.append(group[0])
            continue
        x0 = min(item.bbox[0] for item in group)
        y0 = min(item.bbox[1] for item in group)
        x1 = max(item.bbox[0] + item.bbox[2] for item in group)
        y1 = max(item.bbox[1] + item.bbox[3] for item in group)
        regions.append(RegionCandidate(
            "body", (x0, y0, x1 - x0, y1 - y0),
            text="\n".join(item.text or "" for item in group),
            word_indices=tuple(index for item in group for index in item.word_indices),
            reason="outside_ocr_paragraph_candidate",
        ))
    if len(regions) <= 1:
        return tuple(regions), tuple(sorted(issues))
    parent = list(range(len(regions)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, first in enumerate(regions):
        fx, fy, fw, fh = first.bbox
        for j in range(i + 1, len(regions)):
            second = regions[j]
            sx, sy, sw, sh = second.bbox
            gap = sy - (fy + fh)
            if gap > 42:
                break
            overlap = max(0, min(fx + fw, sx + sw) - max(fx, sx))
            same_column = abs(fx - sx) <= 34
            similar_width = max(fw, sw) / max(1, min(fw, sw)) <= 3
            if -min(fh, sh) * 0.25 <= gap <= 42 and similar_width and (same_column or overlap >= min(fw, sw) * 0.28):
                parent[root(j)] = root(i)
    components: dict[int, list[RegionCandidate]] = {}
    for index, region in enumerate(regions):
        components.setdefault(root(index), []).append(region)
    merged: list[RegionCandidate] = []
    for group in components.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        x0 = min(item.bbox[0] for item in group)
        y0 = min(item.bbox[1] for item in group)
        x1 = max(item.bbox[0] + item.bbox[2] for item in group)
        y1 = max(item.bbox[1] + item.bbox[3] for item in group)
        ordered = sorted(group, key=lambda item: (item.bbox[1], item.bbox[0]))
        merged.append(RegionCandidate(
            "table" if any(item.role == "table" for item in group) else "body",
            (x0, y0, x1 - x0, y1 - y0),
            text="\n".join(item.text or "" for item in ordered),
            word_indices=tuple(index for item in ordered for index in item.word_indices),
            reason="outside_ocr_spatial_group_candidate",
        ))
    merged.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
    return tuple(merged), tuple(sorted(issues))
