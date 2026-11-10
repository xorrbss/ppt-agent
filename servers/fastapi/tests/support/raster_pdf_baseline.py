"""PNG-per-page PDF compositor kept as a test oracle.

This is the raster PDF builder the removed authored export lane ran
(``workers/public_export_worker._build_pdf`` until 2026-11-10). The svg vector
PDF tests compare their output against it, so it stays here unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from services.document_canvas import canvas_physical_size


def build_raster_pdf(slides, store, target: Path, canvas) -> None:
    width_emu, height_emu = canvas_physical_size(canvas)
    width_pt = width_emu / 12700
    height_pt = height_emu / 12700
    document = pdfium.PdfDocument.new()
    try:
        for reference in slides:
            with Image.open(store.verified_path(reference)) as image:
                if image.size != (canvas.width_px, canvas.height_px):
                    raise AssertionError("raster baseline slide canvas mismatch")
                rgb = image.convert("RGB")
                try:
                    bitmap = pdfium.PdfBitmap.from_pil(rgb)
                    page = document.new_page(width_pt, height_pt)
                    pdf_image = pdfium.PdfImage.new(document)
                    pdf_image.set_bitmap(bitmap)
                    pdf_image.set_matrix(pdfium.PdfMatrix(width_pt, 0, 0, height_pt, 0, 0))
                    page.insert_obj(pdf_image)
                    page.gen_content()
                    page.close()
                finally:
                    rgb.close()
        document.save(target)
    finally:
        document.close()
