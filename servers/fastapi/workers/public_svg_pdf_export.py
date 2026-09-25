"""Vector PDF delivery for the isolated SVG export worker.

Each slide's admitted wrapper is printed by Chromium (``Page.printToPDF``) to a
single-page PDF, so SVG ``<text>`` stays selectable text with its fonts
embedded; the pages are then merged in order with pdfium. The raster
compositor in ``workers.public_export_worker._build_pdf`` is not used here.
"""

from __future__ import annotations

from pathlib import Path

from services.document_canvas import canvas_physical_size
from workers.public_worker_runtime import PublicWorkerExecutionError

EMU_PER_INCH = 914_400
EMU_PER_POINT = 12_700
# pdfium reports sizes in points; Chromium rounds the paper to device units.
_PAGE_SIZE_TOLERANCE_PT = 1.0


def _slide_wrappers(sources, admitted_assets, canvas):
    from services.svg_renderer import svg_preview_html
    from workers.public_render_worker import _admit_html

    # Asset SVGs already passed canonical admission and an authenticated
    # manifest/decoder check. Never feed expanded base64 through the
    # legacy canonical-source size limit a second time.
    wrappers = []
    for source, admitted in zip(sources, admitted_assets, strict=True):
        if admitted is not None:
            wrappers.append(admitted)
        else:
            wrapper = svg_preview_html(source, width=canvas.width_px, height=canvas.height_px)
            wrappers.append(_admit_html(wrapper))
    return wrappers


def merge_single_page_pdfs(pdfs, target: Path, *, page_size_pt) -> None:
    """Merge one-page PDFs in order into ``target``; fail closed on shape."""
    import pypdfium2 as pdfium

    expected_width, expected_height = page_size_pt
    merged = pdfium.PdfDocument.new()
    try:
        for payload in pdfs:
            single = pdfium.PdfDocument(payload)
            try:
                if len(single) != 1:
                    raise PublicWorkerExecutionError("svg_export_pdf_page_count_mismatch")
                page = single[0]
                try:
                    width, height = page.get_size()
                finally:
                    page.close()
                if (abs(width - expected_width) > _PAGE_SIZE_TOLERANCE_PT
                        or abs(height - expected_height) > _PAGE_SIZE_TOLERANCE_PT):
                    raise PublicWorkerExecutionError("svg_export_pdf_page_size_mismatch")
                merged.import_pages(single)
            finally:
                single.close()
        if len(merged) != len(pdfs):
            raise PublicWorkerExecutionError("svg_export_pdf_page_count_mismatch")
        merged.save(target)
    finally:
        merged.close()


def export_svg_pdf(sources, admitted_assets, canvas, target: Path, *, timeout: int) -> None:
    """Print every slide to vector PDF and write the merged deck to ``target``."""
    from utils.authored_renderer_session import print_admitted_htmls_to_pdf_over_pipe

    wrappers = _slide_wrappers(sources, admitted_assets, canvas)
    width_emu, height_emu = canvas_physical_size(canvas)
    pdfs = print_admitted_htmls_to_pdf_over_pipe(
        wrappers, timeout, width_px=canvas.width_px, height_px=canvas.height_px,
        paper_width_in=width_emu / EMU_PER_INCH, paper_height_in=height_emu / EMU_PER_INCH,
    )
    if len(pdfs) != len(sources):
        raise PublicWorkerExecutionError("svg_export_render_slide_count_mismatch")
    merge_single_page_pdfs(
        pdfs, target, page_size_pt=(width_emu / EMU_PER_POINT, height_emu / EMU_PER_POINT),
    )
