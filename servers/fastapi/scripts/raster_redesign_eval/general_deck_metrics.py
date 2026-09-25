"""PPTX/PDF structural metrics for ordinary (non-raster) generated decks.

Unlike checker.py (image-only redesign, matched against OCR ground truth of a
fixed source deck), this tool has no ground truth: it measures editability and
structure for a generated deck on its own, so two strategies (e.g. svg vs
authored) can be compared on the same inputs.

Usage (from servers/fastapi so python-pptx/Pillow/pdfplumber resolve):
  uv run python scripts/raster_redesign_eval/general_deck_metrics.py \
      --pptx OUT.pptx [--pdf OUT.pdf] --report-dir DIR --label svg_prompt10
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path

from pptx import Presentation

PICTURE_SHAPE_TYPE = 13
DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
SLIDE_XML_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
CHART_XML_RE = re.compile(r"^ppt/charts/chart\d+\.xml$")


def find_tessdata(start: Path) -> Path | None:
    for parent in (start, *start.parents):
        candidate = parent / "tessdata"
        if candidate.is_dir():
            return candidate
    return None


def count_visible_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def ocr_text(image_bytes: bytes, executable: Path, tessdata: Path, timeout: int = 60) -> str:
    command = [
        str(executable), "stdin", "stdout", "--tessdata-dir", str(tessdata),
        "-l", "kor+eng", "--psm", "3",
    ]
    try:
        result = subprocess.run(
            command, input=image_bytes, capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode:
        return ""
    return result.stdout.decode("utf-8-sig", "replace")


def _text_frame_runs(text_frame) -> tuple[int, int, list[float]]:
    chars, runs, sizes = 0, 0, []
    for para in text_frame.paragraphs:
        for run in para.runs:
            if not (run.text and run.text.strip()):
                continue
            runs += 1
            chars += len(run.text)
            try:
                size = run.font.size
            except Exception:  # noqa: BLE001
                size = None
            if size is not None:
                sizes.append(size.pt)
    return chars, runs, sizes


def slide_native_text(slide) -> dict:
    chars = runs = 0
    sizes: list[float] = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            c, r, s = _text_frame_runs(shape.text_frame)
            chars += c
            runs += r
            sizes.extend(s)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    c, r, s = _text_frame_runs(cell.text_frame)
                    chars += c
                    runs += r
                    sizes.extend(s)
    return {"chars": chars, "runs": runs, "font_sizes_pt": sizes}


def slide_pictures(slide, tesseract: Path | None, tessdata: Path | None) -> tuple[list[dict], int]:
    pictures = []
    ocr_chars_total = 0
    for shape in slide.shapes:
        if shape.shape_type != PICTURE_SHAPE_TYPE:
            continue
        try:
            blob = shape.image.blob
            pixel_w, pixel_h = shape.image.size
        except Exception as exc:  # noqa: BLE001
            pictures.append({"error": str(exc)})
            continue
        ocr_chars = 0
        if tesseract is not None and tessdata is not None:
            ocr_chars = count_visible_chars(ocr_text(blob, tesseract, tessdata))
        ocr_chars_total += ocr_chars
        pictures.append({"pixel_size": [pixel_w, pixel_h], "ocr_chars": ocr_chars})
    return pictures, ocr_chars_total


def native_objects_by_part(pptx_path: Path) -> dict:
    tables_per_slide: dict[int, int] = {}
    with zipfile.ZipFile(pptx_path) as archive:
        for name in archive.namelist():
            m = SLIDE_XML_RE.match(name)
            if not m:
                continue
            xml = archive.read(name).decode("utf-8", "replace")
            tables_per_slide[int(m.group(1))] = xml.count("<a:tbl>")
        chart_parts = [n for n in archive.namelist() if CHART_XML_RE.match(n)]
        font_parts = [n for n in archive.namelist() if n.startswith("ppt/fonts/")]
    return {
        "tables_per_slide_index": tables_per_slide,
        "total_native_tables": sum(tables_per_slide.values()),
        "chart_parts": chart_parts,
        "font_parts": font_parts,
    }


def check_pptx(pptx_path: Path, tesseract: Path | None, tessdata: Path | None) -> dict:
    prs = Presentation(str(pptx_path))
    native = native_objects_by_part(pptx_path)
    tables_by_idx = native["tables_per_slide_index"]

    slides_report = []
    all_font_sizes: list[float] = []
    total_native_chars = total_ocr_chars = total_pictures = 0
    for idx, slide in enumerate(prs.slides, start=1):
        text_stats = slide_native_text(slide)
        pictures, ocr_chars = slide_pictures(slide, tesseract, tessdata)
        all_font_sizes.extend(text_stats["font_sizes_pt"])
        total_native_chars += text_stats["chars"]
        total_ocr_chars += ocr_chars
        total_pictures += len(pictures)
        slides_report.append({
            "slide_number": idx,
            "native_text_runs": text_stats["runs"],
            "native_text_chars": text_stats["chars"],
            "min_font_pt": min(text_stats["font_sizes_pt"]) if text_stats["font_sizes_pt"] else None,
            "picture_count": len(pictures),
            "picture_ocr_chars": ocr_chars,
            "native_tables": tables_by_idx.get(idx, 0),
            "pictures": pictures,
        })

    denom = total_native_chars + total_ocr_chars
    editable_ratio = (total_native_chars / denom) if denom else None
    slide_w, slide_h = prs.slide_width, prs.slide_height
    return {
        "pptx_path": str(pptx_path),
        "page_count": len(slides_report),
        "canvas_emu": {"width": slide_w, "height": slide_h},
        "slides": slides_report,
        "summary": {
            "total_native_text_chars": total_native_chars,
            "total_picture_ocr_chars": total_ocr_chars,
            "editable_text_ratio": editable_ratio,
            "total_pictures": total_pictures,
            "total_native_tables": native["total_native_tables"],
            "total_native_charts": len(native["chart_parts"]),
            "deck_min_native_font_pt": min(all_font_sizes) if all_font_sizes else None,
            "cjk_fonts_embedded": bool(native["font_parts"]),
            "embedded_font_parts": native["font_parts"],
            "slides_with_zero_native_text": sum(
                1 for s in slides_report if s["native_text_chars"] == 0
            ),
            "ocr_skipped_no_tesseract": tesseract is None or tessdata is None,
        },
    }


def check_pdf(pdf_path: Path) -> dict:
    import pdfplumber

    pages = []
    zero_char_pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            chars = count_visible_chars(text)
            if chars == 0:
                zero_char_pages.append(i)
            pages.append({
                "page_number": i,
                "extractable_chars": chars,
                "width_pt": page.width,
                "height_pt": page.height,
            })
    return {
        "pdf_path": str(pdf_path),
        "page_count": len(pages),
        "pages": pages,
        "pages_with_zero_extractable_chars": zero_char_pages,
        "file_size_bytes": Path(pdf_path).stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pptx", required=True)
    parser.add_argument("--pdf", default=None)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--tesseract", default=str(DEFAULT_TESSERACT))
    parser.add_argument("--tessdata", default=None, help="defaults to <repo>/tessdata found by walking up from --pptx")
    parser.add_argument(
        "--skip-ocr", action="store_true",
        help="skip picture OCR (editable_text_ratio uses native chars only)",
    )
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    tesseract = Path(args.tesseract)
    tessdata = Path(args.tessdata) if args.tessdata else find_tessdata(Path(args.pptx).resolve())
    if args.skip_ocr or not tesseract.is_file() or tessdata is None or not tessdata.is_dir():
        tesseract = None
        tessdata = None

    report = {"label": args.label, "pptx": check_pptx(Path(args.pptx), tesseract, tessdata)}
    if args.pdf:
        report["pdf"] = check_pdf(Path(args.pdf))

    out_path = report_dir / f"{args.label}_general_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["pptx"]["summary"]
    print(f"=== {args.label} ===")
    print(f"page_count={report['pptx']['page_count']}")
    print(f"total_native_text_chars={s['total_native_text_chars']}")
    print(f"total_picture_ocr_chars={s['total_picture_ocr_chars']} (ocr_skipped={s['ocr_skipped_no_tesseract']})")
    print(f"editable_text_ratio={s['editable_text_ratio']}")
    print(f"total_pictures={s['total_pictures']}")
    print(f"total_native_tables={s['total_native_tables']} total_native_charts={s['total_native_charts']}")
    print(f"deck_min_native_font_pt={s['deck_min_native_font_pt']}")
    print(f"cjk_fonts_embedded={s['cjk_fonts_embedded']} parts={s['embedded_font_parts']}")
    print(f"slides_with_zero_native_text={s['slides_with_zero_native_text']}")
    if "pdf" in report:
        p = report["pdf"]
        print(f"pdf page_count={p['page_count']} file_size_bytes={p['file_size_bytes']}")
        print(f"pdf pages_with_zero_extractable_chars={p['pages_with_zero_extractable_chars']}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
