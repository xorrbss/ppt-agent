"""Local evidence-first raster-to-A4-SVG pilot; not a production ingestion route.

Annotations are private and use source-image pixel boxes. See --help for usage.
The pilot deliberately leaves agent transcription and OCR candidates under review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path


DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_TESSDATA = Path(__file__).resolve().parents[1] / "tessdata"


if __package__:
    from .raster_pilot_media import (
        embedded_page_image,
        json_file,
        media_inventory,
        pixel_digest,
    )
else:
    from raster_pilot_media import (
        embedded_page_image,
        json_file,
        media_inventory,
        pixel_digest,
    )


if __package__:
    pass
else:
    pass


if __package__:
    from .raster_pilot_page import SCHEMA, CANVASES, build_page
else:
    from raster_pilot_page import SCHEMA, CANVASES, build_page


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build local A4 SVG reconstruction evidence from annotated PPTX raster pages"
    )
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--orientation", choices=CANVASES, default="landscape")
    parser.add_argument("--tesseract", type=Path, default=DEFAULT_TESSERACT)
    parser.add_argument("--tessdata", type=Path, default=DEFAULT_TESSDATA)
    parser.add_argument("--whole-ocr-timeout", type=float, default=90)
    parser.add_argument("--crop-ocr-timeout", type=float, default=25)
    args = parser.parse_args()
    if not args.tesseract.is_file() or not args.tessdata.is_dir():
        raise ValueError("Local Tesseract binary or tessdata is unavailable")
    annotations = json.loads(args.annotations.read_text(encoding="utf-8-sig"))
    if not isinstance(annotations, dict) or annotations.get("version") != SCHEMA:
        raise ValueError("Annotation version must be raster-pilot/v1")
    pages = annotations.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("Annotation pages must be a nonempty list")
    source_sha, media, full_slide_images = media_inventory(args.pptx)
    if annotations.get("source_sha256") and annotations["source_sha256"] != source_sha:
        raise ValueError("PPTX digest does not match annotation source_sha256")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    review = []
    seen_pages = set()
    for page in pages:
        if not isinstance(page, dict) or page.get("page") in seen_pages:
            raise ValueError("Page entries must be objects with unique page numbers")
        seen_pages.add(page["page"])
        entry, blob, image, shape = embedded_page_image(page, media, full_slide_images)
        receipt, items = build_page(
            page,
            image,
            entry,
            blob,
            pixel_digest(image),
            shape,
            args.output_dir,
            orientation=args.orientation,
            command=args.tesseract,
            tessdata=args.tessdata,
            whole_timeout=args.whole_ocr_timeout,
            crop_timeout=args.crop_ocr_timeout,
        )
        receipts.append(receipt)
        review.extend(items)
    json_file(
        args.output_dir / "review.json",
        {
            "version": SCHEMA,
            "source_pptx_sha256": source_sha,
            "items": review,
            "acceptance": "review_required",
        },
    )
    json_file(
        args.output_dir / "manifest.json",
        {
            "version": SCHEMA,
            "source_pptx_sha256": source_sha,
            "orientation": args.orientation,
            "page_order": [item["page"] for item in receipts],
            "page_receipts": [f"page-{item['page']:03d}.receipt.json" for item in receipts],
            "acceptance": "pilot_evidence_only",
        },
    )
    print(
        json.dumps(
            {
                "pages": len(receipts),
                "svg": len(receipts),
                "review_items": len(review),
                "uncovered_ocr_tokens": sum(
                    item["whole_page_ocr"]["category_counts"]["uncovered"] for item in receipts
                ),
                "status": "pilot_evidence_only",
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ValueError,
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as error:
        print(f"pilot failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
