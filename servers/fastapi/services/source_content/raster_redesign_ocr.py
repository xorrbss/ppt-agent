"""Line-level OCR for image-only slide redesign.

Tesseract's TSV splits Korean words into syllable tokens, while its plain-text
renderer keeps the real inter-word spacing. Both come from one recognition
pass, so they are aligned token by token: geometry from TSV, spacing from TXT.
A sparse (psm 11) pass supplements text the layout pass did not find, and an
independent reading of a differently preprocessed image measures agreement:
engine scores alone are overconfident on digits and saturated text.
"""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

OCR_SCALE = 2
LAYOUT_ENGINE = "tesseract-kor+eng-psm3-x2"
LAYOUT_PSM = 3
SPARSE_ENGINE = "tesseract-kor+eng-psm11-x2"
# Glyph height (source px per 941 px of page height) at or below which text is
# application-UI micro text; at slide scale it is also below the 9.5pt floor.
MICRO_TEXT_RATIO = 13.5 / 941
_NUMBER = re.compile(r"\d[\d,.]*")


@dataclass(frozen=True)
class RedesignWord:
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float | None
    engine: str
    space_before: bool


@dataclass(frozen=True)
class RedesignLine:
    word_indices: tuple[int, ...]
    bbox: tuple[int, int, int, int]
    height: float
    # Similarity with the independent reading (1.0 = identical characters);
    # None when the second reading found no text at this position.
    agreement: float | None = None
    numbers_disagree: bool = False
    # Full-line readings from the independent passes (for arbitration).
    alternates: tuple[str, ...] = ()


def _prepared(image: Image.Image, variant: str) -> Image.Image:
    luminance = image.convert("L")
    if variant == "blend":
        # Darkening saturated text changes stroke shapes, which is exactly what
        # makes this an independent second reading of the same pixels.
        red, green, blue = image.convert("RGB").split()
        darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
        luminance = Image.blend(luminance, darkest, 0.5)
    return luminance.resize((image.width * OCR_SCALE, image.height * OCR_SCALE), Image.Resampling.LANCZOS)


def _run_tesseract(
    image: Image.Image,
    *,
    executable: Path,
    tessdata: Path,
    psm: int,
    variant: str,
    timeout_seconds: int,
) -> tuple[list[dict], str]:
    with tempfile.TemporaryDirectory(prefix="raster-ocr-") as folder:
        source = Path(folder) / "page.png"
        base = Path(folder) / "out"
        _prepared(image, variant).save(source, format="PNG")
        command = [
            str(executable),
            str(source),
            str(base),
            "--tessdata-dir",
            str(tessdata),
            "-l",
            "kor+eng",
            "--psm",
            str(psm),
            "-c",
            "tessedit_create_tsv=1",
            "-c",
            "tessedit_create_txt=1",
            # Slides are never set vertically, but the page layout analysis classifies
            # tightly set Korean blocks (a table of contents) as vertical text and
            # returns column-shaped garbage words that suppress the sparse pass.
            *(("-c", "textord_tabfind_vertical_text=0") if psm == LAYOUT_PSM else ()),
        ]
        # Pages are recognised in parallel; one engine thread each avoids
        # oversubscribing the CPU with OpenMP workers.
        environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        result = subprocess.run(command, capture_output=True, timeout=timeout_seconds, check=False, env=environment)
        if result.returncode:
            raise RuntimeError("local_ocr_failed")
        tsv = base.with_suffix(".tsv").read_text(encoding="utf-8-sig", errors="replace")
        txt = base.with_suffix(".txt").read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "left", "top", "width", "height", "conf", "text", "block_num", "par_num", "line_num"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise RuntimeError("local_ocr_invalid_tsv")
    return [row for row in reader if row.get("level") == "5" and (row.get("text") or "").strip()], txt


def _space_flags(tokens: list[str], txt: str) -> list[bool] | None:
    """Recover inter-word spacing by aligning TSV tokens with the TXT stream."""

    flags: list[bool] = []
    position = 0
    for token in tokens:
        spaced = False
        while position < len(txt) and txt[position].isspace():
            spaced = True
            position += 1
        compact = "".join(token.split())
        if txt[position : position + len(compact)] != compact:
            return None
        flags.append(spaced)
        position += len(compact)
    return flags


def _gap_spaced(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    gap = right[0] - (left[0] + left[2])
    return gap > 0.3 * max(left[3], right[3])


def _words_from_pass(
    image: Image.Image,
    *,
    executable: Path,
    tessdata: Path,
    psm: int,
    variant: str,
    engine: str,
    timeout_seconds: int,
) -> list[tuple[RedesignWord, tuple[int, int, int]]]:
    rows, txt = _run_tesseract(
        image, executable=executable, tessdata=tessdata, psm=psm, variant=variant, timeout_seconds=timeout_seconds
    )
    flags = _space_flags([row["text"].strip() for row in rows], txt)
    words: list[tuple[RedesignWord, tuple[int, int, int]]] = []
    for position, row in enumerate(rows):
        try:
            box = [int(row[name]) for name in ("left", "top", "width", "height")]
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            raw = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            continue
        x0 = max(0, box[0] // OCR_SCALE)
        y0 = max(0, box[1] // OCR_SCALE)
        x1 = min(image.width, -(-(box[0] + box[2]) // OCR_SCALE))
        y1 = min(image.height, -(-(box[1] + box[3]) // OCR_SCALE))
        if x1 <= x0 or y1 <= y0:
            continue
        bbox = (x0, y0, x1 - x0, y1 - y0)
        if flags is not None:
            spaced = flags[position]
        else:
            spaced = not words or words[-1][1] != key or _gap_spaced(words[-1][0].bbox, bbox)
        confidence = max(0.0, min(1.0, raw / 100)) if raw >= 0 else None
        words.append((RedesignWord(row["text"].strip(), bbox, confidence, engine, spaced), key))
        if len(words) > 6000:
            raise RuntimeError("local_ocr_token_limit")
    return words


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    inter = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
    return inter / max(1, min(aw * ah, bw * bh))


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    inter = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
    return inter / max(1, aw * ah + bw * bh - inter)


def _median_height(indices: list[int], words: list[RedesignWord]) -> int:
    heights = sorted(words[index].bbox[3] for index in indices)
    return heights[len(heights) // 2]


def _split_line(indices: list[int], words: list[RedesignWord]) -> list[list[int]]:
    """Split one engine line at column-sized gaps so cards never merge."""

    ordered = sorted(indices, key=lambda index: words[index].bbox[0])
    height = _median_height(ordered, words)
    parts: list[list[int]] = [[ordered[0]]]
    for index in ordered[1:]:
        previous = words[parts[-1][-1]]
        gap = words[index].bbox[0] - (previous.bbox[0] + previous.bbox[2])
        if gap > max(1.6 * height, 24):
            parts.append([index])
        else:
            parts[-1].append(index)
    return parts


def _box(indices: list[int] | tuple[int, ...], words: list[RedesignWord]) -> tuple[int, int, int, int]:
    x0 = min(words[i].bbox[0] for i in indices)
    y0 = min(words[i].bbox[1] for i in indices)
    x1 = max(words[i].bbox[0] + words[i].bbox[2] for i in indices)
    y1 = max(words[i].bbox[1] + words[i].bbox[3] for i in indices)
    return (x0, y0, x1 - x0, y1 - y0)


def _same_row(a: list[int], b: list[int], words: list[RedesignWord]) -> bool:
    """True when two word runs sit on one baseline and touch horizontally."""

    first, second = _box(a, words), _box(b, words)
    ha, hb = _median_height(a, words), _median_height(b, words)
    if max(ha, hb) > 1.5 * min(ha, hb):
        return False
    overlap = min(first[1] + first[3], second[1] + second[3]) - max(first[1], second[1])
    if overlap < 0.6 * min(first[3], second[3]):
        return False
    left, right = (first, second) if first[0] <= second[0] else (second, first)
    gap = right[0] - (left[0] + left[2])
    return -2 <= gap <= max(1.6 * max(ha, hb), 24)


def _merge_rows(parts: list[list[int]], words: list[RedesignWord]) -> list[list[int]]:
    merged = [list(part) for part in parts]
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                if _same_row(merged[i], merged[j], words):
                    merged[i] = sorted(merged[i] + merged[j], key=lambda index: words[index].bbox[0])
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _compact(text: str) -> str:
    return "".join(text.split())


def redesign_ocr(
    image: Image.Image,
    *,
    executable: Path,
    tessdata: Path,
    timeout_seconds: int = 120,
) -> tuple[tuple[RedesignWord, ...], tuple[RedesignLine, ...]]:
    """Return words (inventory order) and visual lines with agreement scores."""

    options = {"executable": executable, "tessdata": tessdata, "timeout_seconds": timeout_seconds}
    layout = _words_from_pass(image, psm=LAYOUT_PSM, variant="luma", engine=LAYOUT_ENGINE, **options)
    sparse = _words_from_pass(image, psm=11, variant="luma", engine=SPARSE_ENGINE, **options)
    words: list[RedesignWord] = []
    groups: dict[tuple[int, int, int, int], list[int]] = {}
    candidates = [(1, word, key) for word, key in layout] + [(2, word, key) for word, key in sparse]
    # Tesseract occasionally reads one pixel area twice (overlapping blocks, or
    # both passes). A second reading of the same pixels is not content: keep
    # the layout pass first, then the more confident reading.
    ranking = sorted(range(len(candidates)), key=lambda i: (candidates[i][0], -(candidates[i][1].confidence or 0)))
    kept: list[int] = []
    for index in ranking:
        rank, word, _ = candidates[index]
        if rank == 1:
            duplicate = any(_iou(word.bbox, candidates[other][1].bbox) > 0.5 for other in kept)
        else:
            duplicate = any(_overlap_ratio(word.bbox, candidates[other][1].bbox) > 0.25 for other in kept)
        if not duplicate:
            kept.append(index)
    for index in sorted(kept):
        rank, word, key = candidates[index]
        groups.setdefault((rank, *key), []).append(len(words))
        words.append(word)
    parts = [part for indices in groups.values() for part in _split_line(indices, words)]
    lines = tuple(
        RedesignLine(tuple(part), _box(part, words), float(_median_height(part, words)))
        for part in _merge_rows(parts, words)
    )
    kept_words, kept_lines = drop_fragment_lines(tuple(words), lines)
    from services.source_content.raster_line_reread import reread_lines
    from services.source_content.raster_word_verify import verify_words

    options = {
        "executable": executable,
        "tessdata": tessdata,
        "min_height": MICRO_TEXT_RATIO * image.height,
        "timeout_seconds": timeout_seconds,
    }
    reread_words, reread = reread_lines(image, kept_words, kept_lines, **options)
    return verify_words(image, reread_words, reread, **options)


def drop_fragment_lines(
    words: tuple[RedesignWord, ...],
    lines: tuple[RedesignLine, ...],
) -> tuple[tuple[RedesignWord, ...], tuple[RedesignLine, ...]]:
    """Remove partial re-readings of glyphs that already belong to a taller line.

    Tesseract sometimes segments the upper jamo of Hangul syllables as a
    separate short line lying inside the real line. Those tokens re-read
    pixels already carried by the containing line, so they are not content.
    """

    def inflated(line: RedesignLine) -> bool:
        return line.bbox[3] > 2.2 * line.height

    fragments: set[int] = set()
    for index, line in enumerate(lines):
        for other_index, other in enumerate(lines):
            if other_index == index or other_index in fragments or inflated(other):
                continue
            larger = line.bbox[2] * line.bbox[3] < other.bbox[2] * other.bbox[3]
            if other.height >= 1.4 * line.height and _overlap_ratio(line.bbox, other.bbox) >= 0.7 and larger:
                fragments.add(index)
                break
    if not fragments:
        return words, lines
    remap: dict[int, int] = {}
    kept_words: list[RedesignWord] = []
    dropped = {i for index in fragments for i in lines[index].word_indices}
    for i, word in enumerate(words):
        if i not in dropped:
            remap[i] = len(kept_words)
            kept_words.append(word)
    kept_lines = tuple(
        RedesignLine(
            tuple(remap[i] for i in line.word_indices), line.bbox, line.height, line.agreement, line.numbers_disagree
        )
        for index, line in enumerate(lines)
        if index not in fragments
    )
    return tuple(kept_words), kept_lines


def line_text(line: RedesignLine, words: tuple[RedesignWord, ...]) -> str:
    parts: list[str] = []
    previous: RedesignWord | None = None
    for index in line.word_indices:
        word = words[index]
        if previous is not None:
            same_engine_run = previous.engine == word.engine and word.bbox[0] >= previous.bbox[0]
            spaced = word.space_before if same_engine_run else _gap_spaced(previous.bbox, word.bbox)
            if spaced:
                parts.append(" ")
        parts.append(word.text)
        previous = word
    return "".join(parts)
