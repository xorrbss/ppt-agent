"""Build an image-only PPTX from a text-native source PPTX, plus a ground-truth
JSON of the ORIGINAL editable text (not OCR): per-slide text, table cells,
numeric tokens. Used to test whether auto_redesign generalizes beyond a single
large real-world reference deck.

Pipeline: source.pptx --[python-pptx]--> ground_truth.json
          source.pptx --[soffice --convert-to pdf]--> source.pdf
          source.pdf  --[pymupdf render]--> NN.png (one per slide, native size)
          NN.png * N  --[python-pptx]--> image_only.pptx (one full-slide picture/slide)

Run with: uv run --with pymupdf python build_image_only_deck.py <source.pptx> <out_dir> [--slides N]

`image_only.pptx` for deck1-3 is intentionally not checked into the repo (see
../README.md "Rebuilding deck assets"): re-run this script against the same
source template to regenerate it, or use `--from-images` to rebuild it from
already-checked-in `ground_truth.json` + `source_pages/*.png` alone (no
source pptx, soffice, or pymupdf needed for that path).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

SOFFICE = r"C:\Program Files\LibreOffice\program\soffice.exe"
NUMBER_RE = re.compile(r"\d[\d,.]*%?")


def extract_ground_truth(pptx_path: Path, max_slides: int | None) -> dict:
    prs = Presentation(str(pptx_path))
    pages = []
    for idx, slide in enumerate(prs.slides, start=1):
        if max_slides and idx > max_slides:
            break
        text_parts = []
        tables = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.text:
                            text_parts.append(run.text)
            if getattr(shape, "has_table", False):
                tbl = shape.table
                cells = [[cell.text for cell in row.cells] for row in tbl.rows]
                tables.append({"rows": len(tbl.rows), "cols": len(tbl.columns), "cells": cells})
                for row in cells:
                    text_parts.extend(row)
        full_text = " ".join(t for t in text_parts if t.strip())
        normalized = re.sub(r"\s+", " ", full_text).strip()
        pages.append({
            "page_number": idx,
            "text_raw": full_text,
            "text_normalized": normalized,
            "char_count": len(normalized.replace(" ", "")),
            "numeric_tokens": NUMBER_RE.findall(normalized),
            "tables": tables,
        })
    return {
        "source_path": str(pptx_path),
        "slide_width_emu": prs.slide_width,
        "slide_height_emu": prs.slide_height,
        "page_count": len(pages),
        "pages": pages,
    }


def convert_to_pdf(pptx_path: Path, out_dir: Path) -> Path:
    result = subprocess.run(
        [SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path)],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"soffice conversion failed: {result.stderr}")
    pdf_path = out_dir / (pptx_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"expected {pdf_path} not found after conversion")
    return pdf_path


def render_pdf_pages(pdf_path: Path, out_dir: Path, max_slides: int | None) -> list[Path]:
    import fitz  # pymupdf

    pages_dir = out_dir / "source_pages"
    pages_dir.mkdir(exist_ok=True)
    doc = fitz.open(str(pdf_path))
    paths = []
    for i, page in enumerate(doc, start=1):
        if max_slides and i > max_slides:
            break
        # Render at ~2x the PDF's own point size for a crisp "screenshot" (matches
        # a real screen-capture source better than a low-DPI thumbnail).
        zoom = 2.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        p = pages_dir / f"{i:02d}.png"
        pix.save(str(p))
        paths.append(p)
    doc.close()
    return paths


def build_image_only_pptx(page_images: list[Path], slide_width_emu: int, slide_height_emu: int,
                           out_path: Path) -> None:
    prs = Presentation()
    prs.slide_width = Emu(slide_width_emu)
    prs.slide_height = Emu(slide_height_emu)
    blank_layout = prs.slide_layouts[6]  # "Blank"
    for img_path in page_images:
        slide = prs.slides.add_slide(blank_layout)
        slide.shapes.add_picture(str(img_path), 0, 0, width=Emu(slide_width_emu), height=Emu(slide_height_emu))
    prs.save(str(out_path))


def _rebuild_from_images(out_dir: Path) -> None:
    """Rebuild image_only.pptx from an already-populated out_dir (ground_truth.json
    + source_pages/*.png), with no source pptx / soffice / pymupdf involved. Used
    to regenerate the (intentionally not checked-in) image_only.pptx for deck1-3
    from the checked-in ground_truth.json + source_pages fixtures."""
    gt_path = out_dir / "ground_truth.json"
    pages_dir = out_dir / "source_pages"
    if not gt_path.exists() or not pages_dir.exists():
        raise SystemExit(f"--from-images needs {gt_path} and {pages_dir} to already exist")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    page_images = sorted(pages_dir.glob("*.png"))
    if not page_images:
        raise SystemExit(f"no PNGs found under {pages_dir}")
    out_path = out_dir / "image_only.pptx"
    build_image_only_pptx(page_images, gt["slide_width_emu"], gt["slide_height_emu"], out_path)
    print(f"[from-images] wrote {out_path} ({out_path.stat().st_size} bytes) from {len(page_images)} pages")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", help="source .pptx (omit with --from-images)")
    parser.add_argument("out_dir")
    parser.add_argument("--slides", type=int, default=None)
    parser.add_argument("--from-images", action="store_true",
                         help="skip source/soffice/pymupdf; rebuild image_only.pptx from an "
                              "existing ground_truth.json + source_pages/*.png in out_dir")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.from_images:
        _rebuild_from_images(out_dir)
        return

    if not args.source:
        raise SystemExit("source is required unless --from-images is given")
    source_path = Path(args.source)

    print(f"[1/4] extracting ground truth from {source_path.name}")
    gt = extract_ground_truth(source_path, args.slides)
    (out_dir / "ground_truth.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      {gt['page_count']} pages, {sum(p['char_count'] for p in gt['pages'])} total chars, "
          f"{sum(len(p['tables']) for p in gt['pages'])} tables")

    print("[2/4] converting to PDF via soffice")
    pdf_path = convert_to_pdf(source_path, out_dir)

    print("[3/4] rendering pages to PNG")
    page_images = render_pdf_pages(pdf_path, out_dir, args.slides)
    print(f"      rendered {len(page_images)} pages")

    print("[4/4] assembling image-only PPTX")
    image_only_path = out_dir / "image_only.pptx"
    build_image_only_pptx(page_images, gt["slide_width_emu"], gt["slide_height_emu"], image_only_path)
    size = image_only_path.stat().st_size
    print(f"      wrote {image_only_path} ({size} bytes)")


if __name__ == "__main__":
    main()
