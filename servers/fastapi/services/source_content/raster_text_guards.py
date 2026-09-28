"""Guards that keep misreads out of editable text.

Numbers. Two Korean+English Tesseract passes share failure modes ("83%" read
as "8396" by both), so agreement between them is not proof. Every number is
re-read as the WHOLE visual number it belongs to (``raster_number_units``: the
adjacent glyphs of its row, including glyphs OCR dropped), once as one line with
the English model at a steady glyph height and once restricted to digits and
``% . , + - ~ / ( ) : $``. A reading's numbers may be published as editable text only
when they equal, in order, the reads of their units (a digit the English model
appends for a Hangul glyph right after the number is allowed, and a number with a
unit word written after it may be confirmed by its head read plus a Korean read of
the unit word; ``raster_number_suffix``). A fragment of a
number never confirms itself ("0" of "01"), a number wrapped over rows is never
confirmed, and line-read votes never substitute for the unit read. Failing that
the line stays pixels.

Glyph fragments. Line-edge tokens made only of symbols (icon misreads such as
"-()-") are not text; a token gluing brackets or backslashes to letters or
digits ("\\S-IS", "2)", "505]") is a misread that only a grounded correction
can fix; a short numeric fragment in a page corner is a page number (footer).
"""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from services.source_content.raster_number_units import (
    NUMBER,
    UnitRead,
    number_matches,
    number_units,
    sequence_confirmed,
    text_numbers,
)
from services.source_content.raster_number_suffix import (
    SplitRead,
    glyph_runs,
    glyph_shape,
    suffix_splits,
    tail_suffix,
)
from services.source_content.raster_stage_timing import stage

_HANGUL = re.compile(r"[가-힣]")
_GLUE = re.compile(r"[\\\[\](){}|]")
_BULLETS = set("•※·*▪■◆●○-–—→>")
_WHITELIST = "0123456789%.,+-~/():$"
HEURISTIC_MIN_CONFIDENCE = 0.7


def _compact(value: str) -> str:
    return "".join(value.split())


def symbol_only(text: str) -> bool:
    value = _compact(text)
    return bool(value) and not any(ch.isalnum() for ch in value) and not set(value) <= _BULLETS


def glued_symbols(text: str) -> bool:
    """A backslash glued to letters/digits, or unbalanced brackets in the line."""

    for token in text.split():
        if not any(ch.isalnum() for ch in token):
            continue
        if "\\" in token:  # pipes are common separators ("방식|데이터"); backslashes are not
            return True
    # Brackets may span tokens ("(표·차트 포함)"): balance is judged on the line.
    return any(text.count(a) != text.count(b) for a, b in ("()", "[]", "{}"))


def _number_ok(number: str, targeted: tuple[str, ...], votes: Counter, hangul_follows: bool = False) -> bool:
    """Legacy per-number rule, for targeted reads given as a plain tuple of tokens."""

    if any(number_matches(number, t, hangul_follows) for t in targeted):
        return True
    # A targeted read of the same leading digits with another ending is a
    # conflict ("83%" against "8396"): never publish either as text.
    stem = number.rstrip("%")
    if any(t != number and (stem.startswith(t.rstrip("%")) or t.rstrip("%").startswith(stem)) for t in targeted):
        return False
    return votes[number] >= 2


def numbers_supported(text: str, reads: tuple[str, ...], targeted: tuple[str, ...] | None) -> bool:
    """Whether every number of ``text`` is confirmed.

    With ``NumberReads`` (the product path) each number must equal, in order, the read
    of the whole visual number it belongs to; votes of line reads never substitute
    for that. A plain tuple of tokens keeps the per-token rule, and None (no local
    OCR engine) falls back to two agreeing line reads."""

    if isinstance(targeted, NumberReads):
        return sequence_confirmed(text_numbers(text), targeted.units, allow_skip=True)
    numbers = text_numbers(_compact(text))
    if not numbers:
        return True
    votes = Counter(number for read in reads for number in set(NUMBER.findall(_compact(read))))
    return all(_number_ok(number, targeted or (), votes, follows) for number, follows in numbers)


def number_patched(text: str, targeted: tuple[str, ...] | None) -> str | None:
    """The reading with its number tokens replaced by the targeted reads, in order."""

    numbers = NUMBER.findall(text)
    if not targeted or not numbers or len(numbers) != len(targeted):
        return None
    parts, position = [], 0
    for match, replacement in zip(NUMBER.finditer(text), targeted, strict=True):
        parts.append(text[position : match.start()])
        parts.append(replacement)
        position = match.end()
    return "".join(parts) + text[position:]


def corner_fragment(box: tuple[int, int, int, int], text: str, size: tuple[int, int]) -> bool:
    """A short numeric fragment in a page corner or edge band: page-number artifact."""

    value = _compact(text)
    if not value or len(value) > 3 or not any(ch.isdigit() for ch in value):
        return False
    x, y, w, h = box
    near_side = x < 0.08 * size[0] or x + w > 0.92 * size[0]
    near_edge = y < 0.12 * size[1] or y + h > 0.9 * size[1]
    return near_side and near_edge


def _read(
    paths: list[str], folder: Path, extra: list[str], tag: str, *, executable: Path, tessdata: Path, lang: str = "eng"
) -> list[str]:
    joiner = "" if lang == "eng" else " "
    reads = _read_words(paths, folder, extra, tag, executable=executable, tessdata=tessdata, lang=lang)
    return [joiner.join(text for text, _, _ in words) for words in reads]


def _read_words(
    paths: list[str], folder: Path, extra: list[str], tag: str, *, executable: Path, tessdata: Path, lang: str = "eng"
) -> list[list[tuple[str, int, int]]]:
    """Each crop's words as (text, left, width) in crop pixels, in reading order."""

    listing = folder / f"{tag}.txt"
    listing.write_text("\n".join(paths), encoding="utf-8")
    base = folder / f"{tag}-out"
    command = [
        str(executable),
        str(listing),
        str(base),
        "--tessdata-dir",
        str(tessdata),
        "-l",
        lang,
        "--psm",
        "7",
        "-c",
        "tessedit_create_tsv=1",
        *extra,
    ]
    environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    completed = subprocess.run(command, capture_output=True, timeout=120, check=False, env=environment)
    if completed.returncode:
        raise RuntimeError("local_ocr_failed")
    words: list[list[tuple[str, int, int]]] = [[] for _ in paths]
    tsv = base.with_suffix(".tsv").read_text(encoding="utf-8-sig", errors="replace")
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") == "5" and (row.get("text") or "").strip():
            page = int(row["page_num"]) - 1
            if 0 <= page < len(words):
                words[page].append((row["text"].strip(), int(row["left"]), int(row["width"])))
    return words


class NumberReads(tuple):
    """A line's number tokens as its whole-number units read them, in reading order.

    A plain tuple for callers that patch readings (``number_patched``); ``units``
    carries the unit reads that ``numbers_supported`` verifies against."""

    units: tuple[UnitRead, ...] = ()

    def __new__(cls, units=()):
        tokens = tuple(
            token
            for read in units
            for token in (read.readings() or tuple(option[0] for option in read.suffixed()) or ((),))[0]
        )
        value = super().__new__(cls, tokens)
        value.units = tuple(units)
        return value

    def __reduce__(self):
        return (NumberReads, (self.units,))


def _crop_origin(box) -> tuple[int, float]:
    """Left edge and scale factor of a box's crop (``_crop_paths``)."""

    x, _, _, h = box
    return max(0, x - max(3, int(0.3 * h))), max(1.0, 48 / max(1, h))


def _crop_paths(gray: Image.Image, boxes, folder: Path, prefix: str, closed: str = "") -> list[str]:
    """Upscaled crops of ``boxes`` whose margins show background only (ink of the rows
    above/below or the words beside a number is not part of its read), as dark
    glyphs on a light ground. A ``closed`` edge ("left"/"right") is a cut between
    glyphs: nothing beyond it is kept."""

    paths = []
    for k, (x, y, w, h) in enumerate(boxes):
        pad = max(3, int(0.3 * h))
        x0, y0 = _crop_origin((x, y, w, h))[0], max(0, y - pad)
        right, bottom = min(gray.width, x + w + pad), min(gray.height, y + h + pad)
        pixels = np.asarray(gray.crop((x0, y0, right, bottom))).copy()
        keep_y, keep_x = max(1, int(0.12 * h)), max(2, int(0.15 * h))
        left_keep, right_keep = (0 if closed == "left" else keep_x), (0 if closed == "right" else keep_x)
        inside = np.zeros(pixels.shape, dtype=bool)
        inside[max(0, y - keep_y - y0) : y + h + keep_y - y0, max(0, x - left_keep - x0) : x + w + right_keep - x0] = (
            True
        )
        if pixels.size and inside.any():
            background = int(np.median(pixels[inside]))
            pixels[~inside] = background
            if background < 128:  # light text on a dark ground: the engine reads dark on light
                pixels = 255 - pixels
        crop = Image.fromarray(pixels)
        factor = _crop_origin((x, y, w, h))[1]
        crop = crop.resize((round(crop.width * factor), round(crop.height * factor)), Image.Resampling.LANCZOS)
        path = folder / f"{prefix}-{k}.png"
        crop.save(path)
        paths.append(str(path))
    return paths


def read_number_units(image: Image.Image, words, seeds, *, executable: Path | None, tessdata: Path | None):
    """Each whole visual number of the digit words ``seeds``, read once as one line.

    None when the local OCR engine is unavailable (callers keep their older rules)."""

    with stage(None, "number_units"):
        return _read_number_units(image, words, seeds, executable=executable, tessdata=tessdata)


def _read_number_units(image: Image.Image, words, seeds, *, executable: Path | None, tessdata: Path | None):
    if executable is None or tessdata is None or not Path(executable).is_file():
        return None
    gray = image.convert("L")
    array = np.asarray(gray, dtype=np.int16)
    units = number_units(array, words, seeds)
    if not units:
        return ()
    try:
        with tempfile.TemporaryDirectory(prefix="raster-numbers-") as name:
            folder = Path(name)
            paths = _crop_paths(gray, [unit.box for unit in units], folder, "n")
            options = {"executable": executable, "tessdata": tessdata}
            plain = _read(paths, folder, [], "eng", **options)
            restricted = _read(paths, folder, ["-c", f"tessedit_char_whitelist={_WHITELIST}"], "digits", **options)
            reads = tuple(
                UnitRead(unit, tuple(NUMBER.findall(plain[k])), tuple(NUMBER.findall(restricted[k])))
                for k, unit in enumerate(units)
            )
            return _with_splits(gray, array, words, reads, folder, options)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None


def _with_splits(gray: Image.Image, array: np.ndarray, words, reads, folder: Path, options: dict):
    """Unit reads with the suffix splits of every unit that holds Hangul or whose read
    does not confirm its own OCR text ("7.9억원" read "7922"): head read by the English
    and the digit-only reader, tail by the Korean reader (``raster_number_suffix``)."""

    pending = [
        (k, split)
        for k, read in enumerate(reads)
        if _HANGUL.search(read.unit.text)
        or not sequence_confirmed(text_numbers(read.unit.text), (read,), allow_skip=False)
        for split in suffix_splits(array, read.unit, words)
    ]
    if not pending:
        return reads
    heads = _crop_paths(gray, [split.head for _, split in pending], folder, "h", closed="right")
    tails = _crop_paths(gray, [split.tail for _, split in pending], folder, "t", closed="left")
    plain = _read(heads, folder, [], "split-eng", **options)
    restricted = _read(heads, folder, ["-c", f"tessedit_char_whitelist={_WHITELIST}"], "split-digits", **options)
    korean = _read_words(tails, folder, [], "split-kor", lang="kor", **options)
    splits: dict[int, list[SplitRead]] = {}
    for n, (k, split) in enumerate(pending):
        origin, factor = _crop_origin(split.tail)
        suffix = tail_suffix(korean[n], origin, factor, glyph_runs(array, split.tail), split.tail[3])
        head = (tuple(NUMBER.findall(plain[n])), tuple(NUMBER.findall(restricted[n])))
        splits.setdefault(k, []).append(SplitRead(*head, suffix, glyph_shape(array, split.head)))
    return tuple(replace(read, splits=tuple(splits.get(k, ()))) for k, read in enumerate(reads))


def targeted_number_reads(image: Image.Image, words, lines, *, executable: Path | None, tessdata: Path | None) -> dict:
    """Line index -> ``NumberReads`` of the whole-number units of its digit words.

    Every line gets an entry when the local OCR engine is available (a line without
    digit units then confirms no number); an empty dict otherwise."""

    seeds = sorted({i for line in lines for i in line.word_indices if any(ch.isdigit() for ch in words[i].text)})
    reads = read_number_units(image, words, seeds, executable=executable, tessdata=tessdata)
    if reads is None:
        return {}
    result = {}
    for n, line in enumerate(lines):
        mine = set(line.word_indices)
        result[n] = NumberReads(
            sorted((read for read in reads if mine & set(read.unit.words)), key=lambda read: read.unit.box[0])
        )
    return result


def targeted_hangul_reads(image: Image.Image, words, lines, *, executable: Path | None, tessdata: Path | None) -> dict:
    """Line index -> {word index: Korean-only read} for digit words inside Hangul lines.

    A Hangul word the mixed pass read as digits ("챗봇" as "2452") can only be
    corrected when this independent Korean read and the model agree on it.
    """

    if executable is None or tessdata is None or not Path(executable).is_file():
        return {}
    targets = [
        (n, i)
        for n, line in enumerate(lines)
        if any(_HANGUL.search(words[j].text) for j in line.word_indices)
        for i in line.word_indices
        if any(ch.isdigit() for ch in words[i].text) and not _HANGUL.search(words[i].text)
    ]
    if not targets:
        return {}
    with stage(None, "rereads"):
        return _hangul_reads(image, words, targets, executable=executable, tessdata=tessdata)


def _hangul_reads(image: Image.Image, words, targets, *, executable: Path, tessdata: Path) -> dict:
    gray = image.convert("L")
    try:
        with tempfile.TemporaryDirectory(prefix="raster-hangul-") as name:
            folder = Path(name)
            paths = []
            for k, (_, i) in enumerate(targets):
                x, y, w, h = words[i].bbox
                pad = max(3, int(0.3 * h))
                crop = gray.crop(
                    (max(0, x - pad), max(0, y - pad), min(image.width, x + w + pad), min(image.height, y + h + pad))
                )
                factor = max(1.0, 48 / max(1, h))
                crop = crop.resize((round(crop.width * factor), round(crop.height * factor)), Image.Resampling.LANCZOS)
                crop.save(folder / f"k-{k}.png")
                paths.append(str(folder / f"k-{k}.png"))
            reads = _read(paths, folder, [], "kor", executable=executable, tessdata=tessdata, lang="kor")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return {}
    result: dict[int, dict[int, str]] = {}
    for k, (n, i) in enumerate(targets):
        value = _compact(reads[k])
        if _HANGUL.search(value) and not any(ch.isdigit() for ch in value):
            result.setdefault(n, {})[i] = value
    return result


def overlapping_line(lines, words, index: int) -> bool:
    """Two OCR lines reading the same glyphs (merged passes that disagree on where a
    line ends) cannot both be text: publishing either duplicates or cuts a sentence.
    Judged on word boxes (line boxes of large type are loose)."""

    mine = [words[i].bbox for i in lines[index].word_indices]
    for n, other in enumerate(lines):
        if n == index:
            continue
        for a in mine:
            for b in (words[i].bbox for i in other.word_indices):
                width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                small = min(a[2] * a[3], b[2] * b[3])
                if width > 0 and height >= 0.5 * min(a[3], b[3]) and width * height >= 0.5 * small > 0:
                    return True
    return False
