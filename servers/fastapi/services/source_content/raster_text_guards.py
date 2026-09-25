"""Guards that keep misreads out of editable text.

Numbers. Two Korean+English Tesseract passes share failure modes ("83%" read
as "8396" by both), so agreement between them is not proof. Every digit-
bearing OCR word also gets a targeted re-read with the English model at a
steady glyph height (and, for words without Hangul, a second read restricted
to ``0-9 % . , + - ~``). A number token may be published as editable text only
if a targeted read contains it (possibly followed by a digit the English model
invented for a Hangul glyph), never if a targeted read shows the same leading
digits with another ending (``83%`` vs ``8396``), and otherwise only when at
least two line reads agree on it. Failing that the line stays pixels.

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
from pathlib import Path

from PIL import Image

NUMBER = re.compile(r"\d[\d,.]*%?")
_HANGUL = re.compile(r"[가-힣]")
_GLUE = re.compile(r"[\\\[\](){}|]")
_BULLETS = set("•※·*▪■◆●○-–—→>")
_WHITELIST = "0123456789%.,+-~"


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


def _number_ok(number: str, targeted: tuple[str, ...], votes: Counter) -> bool:
    # The English re-read may append a digit for a Hangul glyph ("40,0008" for
    # "40,000원"): a targeted number extending this one supports it.
    if any(t == number or (t.startswith(number) and len(t) > len(number)) for t in targeted):
        return True
    # A targeted read of the same leading digits with another ending is a
    # conflict ("83%" against "8396"): never publish either as text.
    stem = number.rstrip("%")
    if any(t != number and (stem.startswith(t.rstrip("%")) or t.rstrip("%").startswith(stem)) for t in targeted):
        return False
    return votes[number] >= 2


def numbers_supported(text: str, reads: tuple[str, ...], targeted: tuple[str, ...] | None) -> bool:
    numbers = NUMBER.findall(_compact(text))
    if not numbers:
        return True
    votes = Counter(number for read in reads for number in set(NUMBER.findall(_compact(read))))
    return all(_number_ok(number, targeted or (), votes) for number in numbers)


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
    texts = [""] * len(paths)
    tsv = base.with_suffix(".tsv").read_text(encoding="utf-8-sig", errors="replace")
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") == "5" and (row.get("text") or "").strip():
            page = int(row["page_num"]) - 1
            if 0 <= page < len(texts):
                texts[page] += ("" if not texts[page] or lang == "eng" else " ") + row["text"].strip()
    return texts


def targeted_number_reads(image: Image.Image, words, lines, *, executable: Path | None, tessdata: Path | None) -> dict:
    """Line index -> number tokens read by targeted English reads of its digit words."""

    if executable is None or tessdata is None or not Path(executable).is_file():
        return {}
    targets = [
        (n, i) for n, line in enumerate(lines) for i in line.word_indices if any(ch.isdigit() for ch in words[i].text)
    ]
    if not targets:
        return {}
    gray = image.convert("L")
    try:
        with tempfile.TemporaryDirectory(prefix="raster-numbers-") as name:
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
                path = folder / f"n-{k}.png"
                crop.save(path)
                paths.append(str(path))
            options = {"executable": executable, "tessdata": tessdata}
            plain = _read(paths, folder, [], "eng", **options)
            restricted = _read(paths, folder, ["-c", f"tessedit_char_whitelist={_WHITELIST}"], "digits", **options)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return {}
    result: dict[int, list[str]] = {}
    for k, (n, i) in enumerate(targets):
        found = NUMBER.findall(plain[k])
        if not _HANGUL.search(words[i].text):
            found += NUMBER.findall(restricted[k])
        result.setdefault(n, []).extend(found)
    return {n: tuple(dict.fromkeys(values)) for n, values in result.items()}


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
