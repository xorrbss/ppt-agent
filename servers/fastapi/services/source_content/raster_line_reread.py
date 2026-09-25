"""Second, line-normalised reading of every slide-scale text line.

Page-level segmentation misses syllables at line ends and garbles very large
headline glyphs. Each line is re-read alone (psm 7) after extending it to the
ink that continues on its row and scaling its glyphs to a fixed height. The
better-scoring reading is kept; the similarity of the two independent
readings becomes the line's agreement score.
"""

from __future__ import annotations

import csv
import difflib
import io
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord, _space_flags, line_text

REREAD_ENGINE = "tesseract-kor+eng-psm7-line"
TARGET_GLYPH_PX = 32
_NUMBER = re.compile(r"\d[\d,.]*%?")
_LETTER = re.compile(r"[0-9A-Za-z가-힣]")


@dataclass(frozen=True)
class _Job:
    line_indices: tuple[int, ...]
    box: tuple[int, int, int, int]
    crop_origin: tuple[int, int]
    scale: float


def _compact(value: str) -> str:
    return "".join(value.split())


def _ink_extent(gray: np.ndarray, box: tuple[int, int, int, int], height: float) -> tuple[int, int]:
    """Extend a line horizontally over ink that continues on the same row."""

    x, y, width, box_height = box
    top = y + int(0.15 * box_height)
    bottom = max(top + 1, y + int(0.85 * box_height))
    reach = int(6 * height)
    lo, hi = max(0, x - reach), min(gray.shape[1], x + width + reach)
    band = gray[top:bottom, lo:hi]
    if band.size == 0:
        return x, x + width
    ink = (np.abs(band - np.median(band)) > 40).any(axis=0)
    limit = max(6, int(0.6 * height))
    left, empty = x, 0
    for column in range(x - 1, lo - 1, -1):
        if ink[column - lo]:
            left, empty = column, 0
        else:
            empty += 1
            if empty > limit:
                break
    right, empty = x + width, 0
    for column in range(x + width, hi):
        if ink[column - lo]:
            right, empty = column + 1, 0
        else:
            empty += 1
            if empty > limit:
                break
    return left, right


def _plan_jobs(image: Image.Image, words, lines, min_height: float) -> list[_Job]:
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    consumed: set[int] = set()
    jobs: list[_Job] = []
    for index in sorted(range(len(lines)), key=lambda i: -lines[i].height):
        line = lines[index]
        if index in consumed or line.height <= min_height:
            continue
        x0, x1 = _ink_extent(gray, line.bbox, line.height)
        y0, y1 = line.bbox[1], line.bbox[1] + line.bbox[3]
        # Never grow into a separate, confidently read block on the same row.
        for other_index, other in enumerate(lines):
            if other_index == index or not _confident(other, words) or not _same_row(line.bbox, other.bbox):
                continue
            if other.bbox[0] >= line.bbox[0] + line.bbox[2] + line.height:
                x1 = min(x1, other.bbox[0] - 2)
            elif other.bbox[0] + other.bbox[2] + line.height <= line.bbox[0]:
                x0 = max(x0, other.bbox[0] + other.bbox[2] + 2)
        box = (x0, y0, x1 - x0, y1 - y0)
        members = [
            other
            for other in range(len(lines))
            if other not in consumed
            and lines[other].height <= 1.3 * line.height
            and _inside(lines[other].bbox, box) >= 0.6
        ]
        if index not in members:
            members.append(index)
        consumed.update(members)
        pad_y, pad_x = int(0.25 * line.height) + 2, int(0.3 * line.height) + 2
        origin = (max(0, x0 - pad_x), max(0, y0 - pad_y))
        jobs.append(_Job(tuple(members), box, origin, max(0.3, min(3.0, TARGET_GLYPH_PX / max(1.0, line.height)))))
    return _merge_row_overlaps(jobs)


def _merge_row_overlaps(jobs: list[_Job]) -> list[_Job]:
    """Two jobs whose boxes share pixels on one printed row become one job.

    Reading the same glyphs twice yields two lines that overlap ("시스템 화면 소개" and
    "소개 · 업무포털"); neither may then be published without duplicating or cutting
    the sentence, so the whole row stays pixels. One reading of the joint row avoids it.
    Jobs come tallest first, so the kept scale is the taller line's."""

    merged = list(jobs)
    changed = True
    while changed:
        changed = False
        for a in range(len(merged)):
            for b in range(a + 1, len(merged)):
                first, second = merged[a], merged[b]
                # Glyph size (the scale is set from each anchor line's glyph height), not
                # box height: a box grows with descenders and stray marks.
                low, high = sorted((first.scale, second.scale))
                shared = min(first.box[0] + first.box[2], second.box[0] + second.box[2]) - max(
                    first.box[0], second.box[0]
                )
                if shared <= 0 or high > 1.3 * low or not _same_row(first.box, second.box):
                    continue
                merged[a] = _Job(
                    first.line_indices + second.line_indices,
                    _union([first.box, second.box]),
                    tuple(min(a, b) for a, b in zip(first.crop_origin, second.crop_origin, strict=True)),
                    first.scale,
                )
                del merged[b]
                changed = True
                break
            if changed:
                break
    return merged


def _confident(line: RedesignLine, words) -> bool:
    value = "".join(_compact(words[i].text) for i in line.word_indices)
    return len(_LETTER.findall(value)) >= 2 and _weighted_conf([words[i] for i in line.word_indices]) >= 0.8


def _same_row(a, b) -> bool:
    overlap = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return overlap >= 0.5 * min(a[3], b[3])


def _inside(inner, outer) -> float:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    inter = max(0, min(ix + iw, ox + ow) - max(ix, ox)) * max(0, min(iy + ih, oy + oh) - max(iy, oy))
    return inter / max(1, iw * ih)


def _read_jobs(
    image: Image.Image, jobs: list[_Job], *, executable: Path, tessdata: Path, timeout_seconds: int
) -> list[list[RedesignWord]]:
    gray = image.convert("L")
    results: list[list[RedesignWord]] = [[] for _ in jobs]
    if not jobs:
        return results
    with tempfile.TemporaryDirectory(prefix="raster-reread-") as folder:
        paths = []
        for number, job in enumerate(jobs):
            x, y, width, height = job.box
            pad_x, pad_y = x - job.crop_origin[0], y - job.crop_origin[1]
            crop = gray.crop(
                (
                    job.crop_origin[0],
                    job.crop_origin[1],
                    min(image.width, x + width + pad_x),
                    min(image.height, y + height + pad_y),
                )
            )
            crop = crop.resize(
                (max(1, round(crop.width * job.scale)), max(1, round(crop.height * job.scale))),
                Image.Resampling.LANCZOS,
            )
            path = Path(folder) / f"line-{number:04d}.png"
            crop.save(path)
            paths.append(str(path))
        listing = Path(folder) / "lines.txt"
        listing.write_text("\n".join(paths), encoding="utf-8")
        base = Path(folder) / "out"
        command = [
            str(executable),
            str(listing),
            str(base),
            "--tessdata-dir",
            str(tessdata),
            "-l",
            "kor+eng",
            "--psm",
            "7",
            "-c",
            "tessedit_create_tsv=1",
            "-c",
            "tessedit_create_txt=1",
        ]
        environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        completed = subprocess.run(command, capture_output=True, timeout=timeout_seconds, check=False, env=environment)
        if completed.returncode:
            raise RuntimeError("local_ocr_failed")
        tsv = base.with_suffix(".tsv").read_text(encoding="utf-8-sig", errors="replace")
        txt = base.with_suffix(".txt").read_text(encoding="utf-8-sig", errors="replace")
    rows_by_page: dict[int, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") == "5" and (row.get("text") or "").strip():
            rows_by_page.setdefault(int(row["page_num"]) - 1, []).append(row)
    texts = txt.split("\f")
    for number, job in enumerate(jobs):
        rows = rows_by_page.get(number, [])
        flags = _space_flags([row["text"].strip() for row in rows], texts[number] if number < len(texts) else "")
        for position, row in enumerate(rows):
            left, top, width, height = (int(row[k]) for k in ("left", "top", "width", "height"))
            x0 = job.crop_origin[0] + int(left / job.scale)
            y0 = job.crop_origin[1] + int(top / job.scale)
            x1 = min(image.width, job.crop_origin[0] + int(np.ceil((left + width) / job.scale)))
            y1 = min(image.height, job.crop_origin[1] + int(np.ceil((top + height) / job.scale)))
            if x1 <= x0 or y1 <= y0:
                continue
            raw = float(row.get("conf") or -1)
            spaced = flags[position] if flags is not None else True
            results[number].append(
                RedesignWord(
                    row["text"].strip(),
                    (x0, y0, x1 - x0, y1 - y0),
                    max(0.0, min(1.0, raw / 100)) if raw >= 0 else None,
                    REREAD_ENGINE,
                    spaced,
                )
            )
    return results


def _weighted_conf(words: list[RedesignWord]) -> float:
    total = sum(max(1, len(word.text)) for word in words)
    return sum(max(1, len(word.text)) * (word.confidence or 0.0) for word in words) / max(1, total)


def reread_lines(
    image: Image.Image,
    words: tuple[RedesignWord, ...],
    lines: tuple[RedesignLine, ...],
    *,
    executable: Path,
    tessdata: Path,
    min_height: float,
    timeout_seconds: int = 120,
) -> tuple[tuple[RedesignWord, ...], tuple[RedesignLine, ...]]:
    """Re-read slide-scale lines; keep the better reading, record agreement."""

    jobs = _plan_jobs(image, words, lines, min_height)
    readings = _read_jobs(image, jobs, executable=executable, tessdata=tessdata, timeout_seconds=timeout_seconds)
    # psm 7 occasionally returns nothing at one scale; retry those lines smaller.
    empty = [number for number, fresh in enumerate(readings) if not fresh]
    if empty:
        retry = [_Job(jobs[n].line_indices, jobs[n].box, jobs[n].crop_origin, jobs[n].scale * 0.7) for n in empty]
        again = _read_jobs(image, retry, executable=executable, tessdata=tessdata, timeout_seconds=timeout_seconds)
        for number, fresh in zip(empty, again, strict=True):
            readings[number] = fresh
    replaced: set[int] = set()
    out_words: list[RedesignWord] = []
    out_lines: list[RedesignLine] = []
    for job, fresh in zip(jobs, readings, strict=True):
        old_indices = [
            i
            for line_index in sorted(job.line_indices, key=lambda k: lines[k].bbox[0])
            for i in lines[line_index].word_indices
        ]
        old_value = "".join(
            _compact(line_text(lines[k], words)) for k in sorted(job.line_indices, key=lambda k: lines[k].bbox[0])
        )
        new_value = "".join(_compact(word.text) for word in fresh)
        old_words = [words[i] for i in old_indices]
        if not fresh:
            continue  # nothing re-read: the original lines stay as they are
        ratio = difflib.SequenceMatcher(None, old_value, new_value, autojunk=False).ratio()
        old_numbers, new_numbers = set(_NUMBER.findall(old_value)), set(_NUMBER.findall(new_value))
        conflict = bool(old_numbers - new_numbers) and bool(new_numbers - old_numbers)
        old_conf, new_conf = _weighted_conf(old_words), _weighted_conf(fresh)
        old_letters, new_letters = len(_LETTER.findall(old_value)), len(_LETTER.findall(new_value))
        better = (new_conf >= old_conf - 0.03 and new_letters >= 0.8 * old_letters) or (old_conf < 0.5 <= new_conf)
        chosen = fresh if better else old_words
        old_text = " ".join(
            line_text(lines[k], words) for k in sorted(job.line_indices, key=lambda k: lines[k].bbox[0])
        )
        fresh_text = line_text(RedesignLine(tuple(range(len(fresh))), job.box, 1.0), tuple(fresh))
        alternates = (old_text, fresh_text)  # both passes, duplicates kept for majority votes
        replaced.update(job.line_indices)
        start = len(out_words)
        out_words.extend(sorted(chosen, key=lambda word: word.bbox[0]))
        indices = tuple(range(start, len(out_words)))
        box = _union([word.bbox for word in chosen])
        heights = sorted(word.bbox[3] for word in chosen)
        out_lines.append(RedesignLine(indices, box, float(heights[len(heights) // 2]), ratio, conflict, alternates))
    for index, line in enumerate(lines):
        if index in replaced:
            continue
        start = len(out_words)
        out_words.extend(words[i] for i in line.word_indices)
        out_lines.append(replace(line, word_indices=tuple(range(start, len(out_words)))))
    return _drop_duplicate_readings(tuple(out_words), tuple(out_lines))


def _drop_duplicate_readings(words, lines):
    """Two jobs can re-read the same pixels; keep one reading of each area."""

    def score(line: RedesignLine) -> tuple[bool, float]:
        # The line-normalised re-read is the better reading of shared pixels.
        reread = all(words[i].engine == REREAD_ENGINE for i in line.word_indices)
        return reread, _weighted_conf([words[i] for i in line.word_indices])

    kept: list[RedesignLine] = []
    for line in sorted(lines, key=score, reverse=True):
        duplicate = any(
            _inside(line.bbox, other.bbox) >= 0.6 or _inside(other.bbox, line.bbox) >= 0.6
            for other in kept
            if max(line.height, other.height) <= 1.5 * min(line.height, other.height)
        )
        if not duplicate:
            kept.append(line)
    order = {id(line): n for n, line in enumerate(lines)}
    kept.sort(key=lambda line: order[id(line)])
    out_words: list[RedesignWord] = []
    out_lines: list[RedesignLine] = []
    for line in kept:
        start = len(out_words)
        out_words.extend(words[i] for i in line.word_indices)
        out_lines.append(replace(line, word_indices=tuple(range(start, len(out_words)))))
    return tuple(out_words), tuple(out_lines)


def _union(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)
