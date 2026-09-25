"""Dedicated high-quality re-reads of headline-sized lines.

Titles matter most and suffer most from page-level segmentation. Every
headline candidate (a large line in the top band) is cropped, upscaled at two
scales and read as a single line (psm 7) with Korean+English and Korean only.
The readings become extra independent alternates of the line, so title text
can be grounded in a majority or a model correction close to one of them.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from PIL import Image

from services.source_content.raster_redesign_ocr import RedesignLine

TITLE_BAND = 0.22
SCALES = (2.0, 3.0)
LANGUAGES = ("kor+eng", "kor")


def headline_lines(lines: tuple[RedesignLine, ...], height: int) -> list[int]:
    band = [i for i, line in enumerate(lines) if line.bbox[1] <= TITLE_BAND * height]
    if not band:
        return []
    tallest = max(lines[i].height for i in band)
    return [i for i in band if lines[i].height >= 0.6 * tallest]


def _read_txt(paths: list[str], folder: Path, language: str, *, executable: Path, tessdata: Path) -> list[str]:
    listing = folder / f"t-{language.replace('+', '_')}.txt"
    listing.write_text("\n".join(paths), encoding="utf-8")
    base = folder / f"t-out-{language.replace('+', '_')}"
    command = [str(executable), str(listing), str(base), "--tessdata-dir", str(tessdata), "-l", language, "--psm", "7"]
    environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    completed = subprocess.run(command, capture_output=True, timeout=120, check=False, env=environment)
    if completed.returncode:
        raise RuntimeError("local_ocr_failed")
    pages = base.with_suffix(".txt").read_text(encoding="utf-8-sig", errors="replace").split("\f")
    return [" ".join(page.split()) for page in pages[: len(paths)]] + [""] * max(0, len(paths) - len(pages))


def reread_headlines(
    image: Image.Image,
    lines: tuple[RedesignLine, ...],
    *,
    executable: Path | None,
    tessdata: Path | None,
) -> tuple[RedesignLine, ...]:
    """Return lines with headline re-reads appended to their alternates (never raises)."""

    targets = headline_lines(lines, image.height)
    if not targets or executable is None or tessdata is None or not Path(executable).is_file():
        return lines
    gray = image.convert("L")
    try:
        with tempfile.TemporaryDirectory(prefix="raster-title-") as name:
            folder = Path(name)
            paths = []
            for index in targets:
                x, y, width, height = lines[index].bbox
                pad = max(4, int(0.25 * height))
                right, bottom = min(image.width, x + width + pad), min(image.height, y + height + pad)
                crop = gray.crop((max(0, x - pad), max(0, y - pad), right, bottom))
                for scale in SCALES:
                    # Scale the glyphs to a steady height; "2-3x" of a 32px reading target.
                    factor = scale * 32 / max(1.0, lines[index].height)
                    path = folder / f"h-{index}-{scale}.png"
                    size = (max(1, round(crop.width * factor)), max(1, round(crop.height * factor)))
                    crop.resize(size, Image.Resampling.LANCZOS).save(path)
                    paths.append(str(path))
            options = {"executable": executable, "tessdata": tessdata}
            reads = [_read_txt(paths, folder, language, **options) for language in LANGUAGES]
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return lines
    updated = list(lines)
    per_line = len(SCALES)
    for n, index in enumerate(targets):
        extra = [read[n * per_line + k] for read in reads for k in range(per_line) if read[n * per_line + k]]
        updated[index] = replace(lines[index], alternates=(*lines[index].alternates, *extra))
    return tuple(updated)
