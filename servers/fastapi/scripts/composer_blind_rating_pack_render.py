"""Rendering helpers for the Composer blind rating pack.

Converts each input deck (PDF or PPTX) to a set of per-page PNGs at a single
consistent canvas size, reusing the two mechanisms already proven elsewhere
in this repo instead of inventing a new renderer:

- PDF -> PNG: ``pypdfium2`` page rasterization, the same library
  ``services/source_locked/conversion.py`` uses for legacy-.ppt visual
  readback digests.
- PPTX -> PDF: a headless LibreOffice ``--convert-to pdf`` subprocess with an
  isolated ``UserInstallation`` profile, the same pattern
  ``services/source_locked/conversion.py`` uses for legacy .ppt conversion.

No FastAPI app, database, or worker is started; this only shells out to
``soffice.exe`` once per PPTX input. The isolated profile directory means a
concurrent LibreOffice invocation from another agent in this shared worktree
cannot collide on a shared lock.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pypdfium2
from PIL import Image

DEFAULT_SOFFICE_PATH = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")

# Matches what is already on disk for the Composer offline-eval candidate
# (artifacts/composer-offline-eval/*/slide-*.png) and the Hyundai reference
# renders (output/hyundai-reference-90plus-20260901/reference-first10), so
# those two decks need no resizing at all -- only the floor PDF and the
# parity PPTX (both 960x540pt / 16:9) are rasterized here.
TARGET_SIZE = (1280, 720)

_LIBREOFFICE_TIMEOUT_SECONDS = 180


class RenderError(RuntimeError):
    """A rendering step could not produce the expected output."""


def _slide_name(index: int, total: int) -> str:
    width = max(2, len(str(total)))
    return f"slide-{index:0{width}d}.png"


def convert_pptx_to_pdf(
    pptx_path: Path,
    work_dir: Path,
    *,
    soffice_path: Path = DEFAULT_SOFFICE_PATH,
    timeout_seconds: int = _LIBREOFFICE_TIMEOUT_SECONDS,
) -> Path:
    """Convert ``pptx_path`` to PDF with an isolated headless LibreOffice profile.

    ``work_dir`` should be a short, otherwise-unused directory: LibreOffice
    on Windows fails outright (not gracefully) once the profile/output path
    gets long, so this deliberately avoids nesting under deep temp paths.
    """

    if not pptx_path.is_file():
        raise RenderError(f"PPTX source not found: {pptx_path}")
    executable = soffice_path.resolve()
    if not executable.is_file():
        raise RenderError(f"LibreOffice executable not found: {executable}")

    output_dir = work_dir / "pdf"
    profile_dir = work_dir / "profile"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(executable),
        "--headless",
        "--invisible",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--norestore",
        "--nolockcheck",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(pptx_path),
    ]
    environment = os.environ.copy()
    environment["SAL_DISABLE_OPENCL"] = "1"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"LibreOffice conversion failed to run: {exc}") from exc
    if completed.returncode != 0:
        stderr_tail = completed.stderr.decode(errors="replace")[-800:]
        raise RenderError(
            f"LibreOffice conversion exited {completed.returncode}: {stderr_tail}"
        )

    produced = output_dir / f"{pptx_path.stem}.pdf"
    if not produced.is_file() or produced.stat().st_size <= 0:
        raise RenderError(f"LibreOffice did not produce a PDF at {produced}")
    return produced


def render_pdf_to_pngs(
    pdf_path: Path,
    out_dir: Path,
    *,
    target_size: tuple[int, int] = TARGET_SIZE,
    expected_pages: int | None = None,
) -> list[Path]:
    """Rasterize every page of ``pdf_path`` to a PNG at exactly ``target_size``.

    Renders at the scale that just covers the target box, then does a final
    exact-size LANCZOS resize so every deck in the pack shares identical
    pixel dimensions regardless of the source page's own aspect rounding.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    document = pypdfium2.PdfDocument(str(pdf_path))
    try:
        page_count = len(document)
        if expected_pages is not None and page_count != expected_pages:
            raise RenderError(
                f"{pdf_path} has {page_count} pages, expected {expected_pages}"
            )
        target_w, target_h = target_size
        written: list[Path] = []
        for index in range(page_count):
            page = document[index]
            try:
                page_w, page_h = page.get_size()
                scale = max(target_w / page_w, target_h / page_h)
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
            if image.size != target_size:
                image = image.resize(target_size, Image.Resampling.LANCZOS)
            destination = out_dir / _slide_name(index + 1, page_count)
            image.save(destination, "PNG")
            written.append(destination)
        return written
    finally:
        document.close()


def copy_png_set(
    source_paths: list[Path],
    out_dir: Path,
    *,
    target_size: tuple[int, int] = TARGET_SIZE,
) -> list[Path]:
    """Place an already-rendered PNG sequence into ``out_dir``, renumbered 1..N.

    Used for decks that already have per-page PNGs on disk (the Composer
    offline-eval candidate, the Hyundai reference deck): resizes only if a
    source PNG is not already exactly ``target_size``.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    total = len(source_paths)
    for position, source in enumerate(source_paths, start=1):
        destination = out_dir / _slide_name(position, total)
        with Image.open(source) as image:
            if image.size == target_size and image.mode == "RGB":
                shutil.copyfile(source, destination)
            else:
                image.convert("RGB").resize(target_size, Image.Resampling.LANCZOS).save(
                    destination, "PNG"
                )
        written.append(destination)
    return written
