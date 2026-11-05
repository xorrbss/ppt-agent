"""Render slide SVGs to PNG with Playwright (run through uv, see run_deck.py).

  uv run --no-project --with playwright==1.52.0 python render_pngs.py <svg-dir> <png-dir>

Uses the system Chrome/Chromium (CHROME_PATH, google-chrome, chromium) so no
`playwright install` download is needed; falls back to Playwright's own Chromium.
Output names: slide1.png, slide2.png, ... (1-based).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWBOX = re.compile(r'viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)"')
DIMENSIONS = re.compile(r'<svg[^>]*?\swidth="([\d.]+)(?:px)?"[^>]*?\sheight="([\d.]+)(?:px)?"')


def _size(svg: str) -> tuple[int, int]:
    for pattern in (VIEWBOX, DIMENSIONS):
        match = pattern.search(svg[:2000])
        if match:
            return round(float(match[1])), round(float(match[2]))
    return 1280, 720


def main(svg_dir: Path, png_dir: Path) -> int:
    files = sorted(svg_dir.glob("s*.svg"))
    png_dir.mkdir(parents=True, exist_ok=True)
    chrome = os.environ.get("CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox", "--disable-gpu"]) \
            if chrome else playwright.chromium.launch(args=["--no-sandbox"])
        for index, path in enumerate(files, start=1):
            svg = path.read_text(encoding="utf-8")
            if not svg.strip():
                continue
            width, height = _size(svg)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(f'<html><body style="margin:0;background:#fff">{svg}</body></html>')
            page.screenshot(path=str(png_dir / f"slide{index}.png"), clip={"x": 0, "y": 0, "width": width, "height": height})
            page.close()
        browser.close()
    print(f"rendered {len(files)} slides to {png_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
