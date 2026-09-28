"""Exported-PPTX checks and slide renders for the canvas bench.

The slide size is read from ``ppt/presentation.xml`` (``p:sldSz``), the one
place PowerPoint takes the page size from.  Slide PNGs are rendered from the
exported PPTX (LibreOffice -> PDF -> pdftocairo), i.e. from the file a user
downloads, and laid out with ``authored_run_contact_sheet.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

_SLD_SZ = re.compile(rb"<p:sldSz\b[^>]*?\bcx=\"(\d+)\"[^>]*?\bcy=\"(\d+)\"")
_DEFAULT_SOFFICE = r"C:\Program Files\LibreOffice\program\soffice.exe"


def pptx_slide_size(path: Path) -> tuple[int, int] | None:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("ppt/presentation.xml")
    match = _SLD_SZ.search(xml)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def pptx_slide_parts(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(1 for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))


def _tool(env_name: str, fallback: str | None, exe: str) -> str | None:
    configured = os.getenv(env_name) or fallback
    if configured and Path(configured).is_file():
        return configured
    return shutil.which(exe)


def render_pptx_slides(
    pptx: Path, out_dir: Path, *, dpi: int = 48, timeout: int = 300
) -> tuple[list[Path], str | None]:
    """Render ``s00.png``.. (0-based, as the contact sheet indexes); returns (paths, skip_reason)."""

    soffice = _tool("SOFFICE_PATH", _DEFAULT_SOFFICE, "soffice")
    pdftocairo = _tool("PDFTOCAIRO_PATH", None, "pdftocairo")
    if soffice is None or pdftocairo is None:
        return [], "renderer_unavailable (set SOFFICE_PATH and PDFTOCAIRO_PATH)"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="canvas-bench-") as work:
        profile = Path(work) / "lo-profile"
        subprocess.run(
            [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
             "--convert-to", "pdf", "--outdir", work, str(pptx)],
            check=False, capture_output=True, timeout=timeout,
        )
        pdf = Path(work) / f"{pptx.stem}.pdf"
        if not pdf.is_file():
            return [], "pptx_to_pdf_failed"
        prefix = Path(work) / "page"
        subprocess.run(
            [pdftocairo, "-png", "-r", str(dpi), str(pdf), str(prefix)],
            check=False, capture_output=True, timeout=timeout,
        )
        pages = sorted(Path(work).glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        if not pages:
            return [], "pdf_to_png_failed"
        rendered = []
        for number, page in enumerate(pages):
            dest = out_dir / f"s{number:02d}.png"
            shutil.copyfile(page, dest)
            rendered.append(dest)
    return rendered, None


def contact_sheet(run_dirs: list[Path], out_dir: Path) -> str | None:
    """Run the shared contact-sheet tool; returns a skip reason or ``None``."""

    runs = [d for d in run_dirs if any(d.glob("s*.png"))]
    if not runs:
        return "no_rendered_slides"
    from scripts import authored_run_contact_sheet

    argv = [arg for run in runs for arg in ("--run", str(run))] + ["--out", str(out_dir)]
    code = authored_run_contact_sheet.main(argv)
    return None if code == 0 else f"contact_sheet_exit_{code}"
