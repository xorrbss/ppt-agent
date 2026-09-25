"""Cross-script verification of short words inside slide text lines.

A line can be read confidently yet wrongly when a glyph shape exists in both
scripts ("적용" read as "HE", "AI" read as "시"). Short, script-prone words are
re-read alone three ways: mixed Korean+English, Korean only and English only.
The mixed single-word reading replaces the original when it is at least as
confident. When both single-script readings are confident but disagree in
script, the clearly more confident one wins; if neither is clearly better the
word is ambiguous and its line's agreement drops to zero so the planner keeps
that line as source pixels.
"""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord, line_text

VERIFY_ENGINE = "tesseract-kor+eng-psm8-word"
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
TARGET_PX = 32


def _units(line: RedesignLine, words: tuple[RedesignWord, ...]) -> list[list[int]]:
    units: list[list[int]] = []
    for position, index in enumerate(line.word_indices):
        if position and words[index].space_before:
            units.append([index])
        elif units:
            units[-1].append(index)
        else:
            units.append([index])
    return units


def _script_prone(value: str, line_has_hangul: bool) -> bool:
    if not value or len(value) > 4:
        return False
    latin_only = bool(_LATIN.search(value)) and not _HANGUL.search(value)
    single_syllable = len(_HANGUL.findall(value)) == 1 and len(value) <= 2
    return (latin_only and line_has_hangul) or single_syllable


def _box(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _read(paths: list[str], folder: Path, language: str, *, executable: Path, tessdata: Path, timeout: int):
    listing = folder / f"{language.replace('+', '_')}.txt"
    listing.write_text("\n".join(paths), encoding="utf-8")
    base = folder / f"out-{language.replace('+', '_')}"
    command = [
        str(executable),
        str(listing),
        str(base),
        "--tessdata-dir",
        str(tessdata),
        "-l",
        language,
        "--psm",
        "8",
        "-c",
        "tessedit_create_tsv=1",
        "-c",
        "tessedit_create_txt=0",
    ]
    environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False, env=environment)
    if completed.returncode:
        raise RuntimeError("local_ocr_failed")
    readings: dict[int, tuple[str, float]] = {}
    tsv = base.with_suffix(".tsv").read_text(encoding="utf-8-sig", errors="replace")
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        page = int(row["page_num"]) - 1
        confidence = max(0.0, float(row.get("conf") or 0) / 100)
        previous = readings.get(page)
        readings[page] = (previous[0] + text, min(previous[1], confidence)) if previous else (text, confidence)
    return readings


def verify_words(
    image: Image.Image,
    words: tuple[RedesignWord, ...],
    lines: tuple[RedesignLine, ...],
    *,
    executable: Path,
    tessdata: Path,
    min_height: float,
    timeout_seconds: int = 120,
) -> tuple[tuple[RedesignWord, ...], tuple[RedesignLine, ...]]:
    targets: list[tuple[int, list[int]]] = []
    confirm: set[int] = set()
    for line_index, line in enumerate(lines):
        if line.height <= min_height:
            continue
        compact = "".join("".join(words[i].text.split()) for i in line.word_indices)
        disputed = line.numbers_disagree or (line.agreement is not None and line.agreement < 0.6)
        if disputed and len(compact) <= 40:
            # Disputed line: a third, independent reading breaks the tie.
            confirm.add(len(targets))
            targets.append((line_index, list(line.word_indices)))
            continue
        units = _units(line, words)
        line_has_hangul = any(_HANGUL.search(words[i].text) for i in line.word_indices)
        for unit in units:
            value = "".join("".join(words[i].text.split()) for i in unit)
            if len(units) > 1 and _script_prone(value, line_has_hangul):
                targets.append((line_index, unit))
    if not targets:
        return words, lines
    gray = image.convert("L")
    with tempfile.TemporaryDirectory(prefix="raster-verify-") as name:
        folder = Path(name)
        paths = []
        for number, (_, unit) in enumerate(targets):
            x, y, width, height = _box([words[i].bbox for i in unit])
            # Glyph boxes can be truncated; a narrow side margin (well below a
            # word space) recovers the glyph without pulling in neighbours.
            pad, side = max(2, int(0.3 * height)), max(2, int(0.15 * height))
            box = (
                max(0, x - side),
                max(0, y - pad),
                min(image.width, x + width + side),
                min(image.height, y + height + pad),
            )
            crop = gray.crop(box)
            scale = max(0.3, min(4.0, TARGET_PX / max(1, height)))
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.Resampling.LANCZOS
            )
            path = folder / f"w-{number:04d}.png"
            crop.save(path)
            paths.append(str(path))
        options = {"executable": executable, "tessdata": tessdata, "timeout": timeout_seconds}
        mixed = _read(paths, folder, "kor+eng", **options)
        korean = _read(paths, folder, "kor", **options)
        english = _read(paths, folder, "eng", **options)
    replacement: dict[int, RedesignWord] = {}
    removed: set[int] = set()
    ambiguous_lines: set[int] = set()
    confirmed_lines: set[int] = set()
    extra: dict[int, list[str]] = {}

    def variant(line_index: int, unit: list[int], reading: str) -> None:
        text = line_text(lines[line_index], words)
        unit_text = "".join(words[i].text for i in unit)
        candidate = text.replace(unit_text, reading, 1) if unit_text in text else reading
        if candidate != text:  # a substitution variant is a candidate, not an extra vote
            extra.setdefault(line_index, []).append(candidate)

    for number, (line_index, unit) in enumerate(targets):
        current = "".join(words[i].text for i in unit)
        if number in confirm:
            third = mixed.get(number)
            if third:
                extra.setdefault(line_index, []).append(third[0])
            if third and "".join(third[0].split()).casefold() == "".join(current.split()).casefold():
                confirmed_lines.add(line_index)  # two of three readings agree
            continue
        current_conf = min((words[i].confidence or 0.0) for i in unit)
        kor, eng = korean.get(number), english.get(number)
        best = mixed.get(number)
        scripts_clash = bool(
            kor
            and eng
            and kor[1] >= 0.85
            and eng[1] >= 0.85
            and _HANGUL.search(kor[0])
            and _LATIN.search(eng[0])
            and not _HANGUL.search(eng[0])
        )
        if scripts_clash:
            if abs(kor[1] - eng[1]) < 0.03:
                ambiguous_lines.add(line_index)  # no reading can be trusted: keep pixels
                variant(line_index, unit, kor[0])
                variant(line_index, unit, eng[0])
                continue
            best = max(kor, eng, key=lambda reading: reading[1])
            if best[0] != current:
                current_conf = 0.0  # the clearly better script replaces a confident misread
        if best is None or best[0].casefold() == current.casefold() or best[1] < max(0.85, current_conf):
            continue
        if len(best[0]) > len(current) + 2:
            continue
        first = words[unit[0]]
        replacement[unit[0]] = RedesignWord(
            best[0], _box([words[i].bbox for i in unit]), best[1], VERIFY_ENGINE, first.space_before
        )
        removed.update(unit[1:])
    remap: dict[int, int] = {}
    out_words: list[RedesignWord] = []
    for index, word in enumerate(words):
        if index in removed:
            continue
        remap[index] = len(out_words)
        out_words.append(replacement.get(index, word))
    out_lines = []
    for line_index, line in enumerate(lines):
        indices = tuple(remap[i] for i in line.word_indices if i in remap)
        agreement = 0.0 if line_index in ambiguous_lines else line.agreement
        alternates = (*line.alternates, *extra.get(line_index, ()))  # independent reads; duplicates count as votes
        if line_index in confirmed_lines:
            out_lines.append(RedesignLine(indices, line.bbox, line.height, 1.0, False, alternates))
            continue
        out_lines.append(RedesignLine(indices, line.bbox, line.height, agreement, line.numbers_disagree, alternates))
    return tuple(out_words), tuple(out_lines)
