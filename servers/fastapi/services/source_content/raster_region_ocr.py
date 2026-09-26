"""OCR inventory and text region grouping."""

from __future__ import annotations

import csv
import io
import subprocess
from pathlib import Path

from PIL import Image

from services.source_content.raster_region_types import OCRWord, RegionCandidate
from services.source_content.raster_region_panels import _intersection


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
        str(executable),
        "stdin",
        "stdout",
        "--tessdata-dir",
        str(tessdata),
        "-l",
        "kor+eng",
        "--psm",
        "11",
        "-c",
        "tessedit_create_tsv=1",
        "-c",
        "tessedit_create_txt=0",
    ]
    result = subprocess.run(
        command,
        input=data.getvalue(),
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
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
        words.append(
            OCRWord(
                value,
                (x, y, width, height),
                max(0.0, min(1.0, raw_confidence / 100)) if raw_confidence >= 0 else None,
                key,
            )
        )
        if len(words) > 5000:
            raise RuntimeError("local_ocr_token_limit")
    return tuple(words)


def ocr_density_rectangles(
    image: Image.Image,
    words: tuple[OCRWord, ...],
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
            candidates.append(
                RegionCandidate(
                    "screenshot",
                    (x0, y0, width, height),
                    reason="ocr_density_capture_boundary_unverified",
                )
            )
    candidates.sort(key=lambda item: item.bbox[2] * item.bbox[3], reverse=True)
    retained: list[RegionCandidate] = []
    for candidate in candidates:
        if any(
            _intersection(candidate.bbox, other.bbox)
            / min(
                candidate.bbox[2] * candidate.bbox[3],
                other.bbox[2] * other.bbox[3],
            )
            > 0.20
            for other in retained
        ):
            continue
        retained.append(candidate)
        if len(retained) >= 8:
            break
    return tuple(retained)


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
        lines.append(
            RegionCandidate(
                "body",
                (x0, y0, x1 - x0, y1 - y0),
                text=" ".join(words[index].text for index in ordered),
                word_indices=tuple(ordered),
                reason="outside_ocr_line_candidate",
            )
        )
    lines.sort(key=lambda region: (region.bbox[1], region.bbox[0]))
    # OCR line alignment suggests a table, but cell grid and merge status remain unverified.
    table_lines: set[int] = set()
    for index, line in enumerate(lines):
        nearby = [
            other
            for other in range(index + 1, len(lines))
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
        regions.append(
            RegionCandidate(
                "body",
                (x0, y0, x1 - x0, y1 - y0),
                text="\n".join(item.text or "" for item in group),
                word_indices=tuple(index for item in group for index in item.word_indices),
                reason="outside_ocr_paragraph_candidate",
            )
        )
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
        merged.append(
            RegionCandidate(
                "table" if any(item.role == "table" for item in group) else "body",
                (x0, y0, x1 - x0, y1 - y0),
                text="\n".join(item.text or "" for item in ordered),
                word_indices=tuple(index for item in ordered for index in item.word_indices),
                reason="outside_ocr_spatial_group_candidate",
            )
        )
    merged.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
    return tuple(merged), tuple(sorted(issues))
