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


def admit_slide_wrapper_html(html: str):
    """Admit one slide wrapper page through the renderer policy.

    The wrapper is tried as authored HTML first and as static output HTML
    second; trusted SVG fonts are then materialized into the admitted page.
    """
    from utils.renderer_isolation import (
        RendererPolicyError,
        admit_authored_html,
        admit_static_output_html,
    )

    try:
        admitted = admit_authored_html(html)
    except RendererPolicyError:
        try:
            admitted = admit_static_output_html(html)
        except RendererPolicyError as static_error:
            raise PublicWorkerExecutionError(
                "public_render_policy_rejected"
            ) from static_error
    try:
        from services.svg_renderer import materialize_trusted_svg_fonts
        return materialize_trusted_svg_fonts(html, admitted)
    except ValueError as error:
        raise PublicWorkerExecutionError("public_render_trusted_font_invalid") from error


def _slide_wrappers(sources, admitted_assets, canvas, *, font_manifest=None, canonical_sources=None):
    from services.svg_renderer import svg_preview_html

    # Asset SVGs already passed canonical admission and an authenticated
    # manifest/decoder check. Never feed expanded base64 through the
    # legacy canonical-source size limit a second time.
    wrappers = []
    for index, (source, admitted) in enumerate(zip(sources, admitted_assets, strict=True)):
        if admitted is None:
            wrapper = svg_preview_html(source, width=canvas.width_px, height=canvas.height_px,
                                       font_manifest=font_manifest)
            admitted = admit_slide_wrapper_html(wrapper)
        if font_manifest is not None:
            from services.svg_renderer import materialize_template_manifest_admitted
            canonical = canonical_sources[index] if canonical_sources is not None else source
            admitted = materialize_template_manifest_admitted(admitted, font_manifest, canonical)
        wrappers.append(admitted)
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


def export_svg_pdf(sources, admitted_assets, canvas, target: Path, *, timeout: int,
                   font_manifest=None, canonical_sources=None) -> None:
    """Print every slide to vector PDF and write the merged deck to ``target``."""
    from utils.authored_renderer_session import print_admitted_htmls_to_pdf_over_pipe

    wrappers = _slide_wrappers(sources, admitted_assets, canvas, font_manifest=font_manifest,
                               canonical_sources=canonical_sources)
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
