"""SVG decks export to a real vector PDF: selectable text, embedded fonts.

Runs the isolated worker's ``execute_svg_export`` against a real headless
Chromium (``Page.printToPDF``) and reads the result back with pdfium. The old
raster path (screenshot + ``_build_pdf``) is rendered from the same wrappers
as the baseline the vector path is measured against.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import time
import uuid
from types import SimpleNamespace

from PIL import ImageChops, ImageStat
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import canvas_physical_size, document_canvas_for_orientation
from services.isolated_worker_contracts import ArtifactRef
from services.svg_contract import SVG_CONTRACT_VERSION
from utils.slide_capture import find_chrome
from workers.public_svg_export_worker import (
    CONVERTER_VERSION,
    SVG_EXPORT_SCHEMA,
    converter_digest,
    execute_svg_export,
)
from utils.slide_capture_pipe_pdf import print_scale
from workers.public_svg_pdf_export import _slide_wrappers

requires_chrome = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")

OWNER = uuid.UUID("10000000-0000-4000-8000-000000000001")
TITLES = ("Quarterly Revenue Review", "Roadmap 2027 Highlights")
BODIES = ("매출 성장률 12% 달성", "신규 시장 진출 계획")


class MemoryStore:
    """The worker's content-addressed store, in memory."""

    def __init__(self, tmp_path):
        self.tmp_path, self.blobs = tmp_path, {}

    def _ref(self, payload, *, operation_id, kind, media_type):
        return ArtifactRef(
            artifact_id=uuid.uuid4(), owner_id=OWNER, tenant_id="tenant-a", kind=kind,
            sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload),
            media_type=media_type, storage_key=f"tenant/owner/{uuid.uuid4().hex}",
            created_by_operation_id=operation_id, retention_class="staged",
        )

    def put(self, payload, *, source=None, operation_id, kind, media_type):
        ref = self._ref(payload, operation_id=operation_id, kind=kind, media_type=media_type)
        self.blobs[ref.artifact_id] = payload
        return ref

    def put_path(self, path, **kwargs):
        return self.put(path.read_bytes(), **kwargs)

    def read(self, ref):
        payload = self.blobs[ref.artifact_id]
        assert hashlib.sha256(payload).hexdigest() == ref.sha256
        return payload

    def verified_path(self, ref):
        path = self.tmp_path / f"{ref.artifact_id}.bin"
        path.write_bytes(self.read(ref))
        return path


def _svg(canvas, title, body):
    width, height = canvas.width_px, canvas.height_px
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>'
        '<rect x="60" y="60" width="200" height="12" fill="#1265e3"/>'
        f'<text x="60" y="160" font-family="Pretendard" font-weight="700" font-size="48" '
        f'fill="#111111">{title}</text>'
        f'<text x="60" y="260" font-family="Pretendard" font-size="32" fill="#333333">{body}</text>'
        '</svg>'
    )


def _job_and_store(tmp_path, canvas, svgs):
    store = MemoryStore(tmp_path)
    operation_id = uuid.uuid4()
    slides = []
    for index, svg in enumerate(svgs):
        ref = store.put(svg.encode("utf-8"), operation_id=operation_id, kind="slide-svg",
                        media_type="image/svg+xml")
        slides.append({"slideIndex": index, "artifactRef": ref.to_wire(), "notes": ""})
    bundle = {
        "schemaVersion": SVG_EXPORT_SCHEMA, "contractVersion": SVG_CONTRACT_VERSION,
        "converterVersion": CONVERTER_VERSION, "converterDigest": converter_digest(),
        "canonicalExportIdentity": hashlib.sha256(b"identity").hexdigest(),
        "exportFormat": "pdf", "canvas": canvas.model_dump(mode="json", by_alias=True),
        "fontEmbedding": False,
        "slides": slides,
    }
    bundle_ref = store.put(json.dumps(bundle).encode("utf-8"), operation_id=operation_id,
                           kind="export-bundle", media_type="application/json")
    job = SimpleNamespace(input_artifact=bundle_ref, operation_id=operation_id,
                          deadline_epoch_seconds=time.time() + 300)
    return job, store


def _page_texts(document):
    texts = []
    for index in range(len(document)):
        page = document[index]
        textpage = page.get_textpage()
        try:
            texts.append(textpage.get_text_range())
        finally:
            textpage.close()
            page.close()
    return texts


def _embedded_fonts(document):
    """``{base font name: embedded}`` for every text object in the document."""
    fonts = {}
    for index in range(len(document)):
        page = document[index]
        try:
            for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_TEXT,)):
                font = pdfium_c.FPDFTextObj_GetFont(obj.raw)
                size = pdfium_c.FPDFFont_GetBaseFontName(font, None, 0)
                buffer = ctypes.create_string_buffer(size)
                pdfium_c.FPDFFont_GetBaseFontName(font, buffer, size)
                name = buffer.value.decode("utf-8", "replace")
                fonts[name] = bool(pdfium_c.FPDFFont_GetIsEmbedded(font))
        finally:
            page.close()
    return fonts


def _draw(payload, index, canvas):
    with pdfium.PdfDocument(payload) as document:
        page = document[index]
        try:
            bitmap = page.render(scale=canvas.width_px / page.get_width())
            image = bitmap.to_pil().convert("RGB")
        finally:
            page.close()
    return image.resize((canvas.width_px, canvas.height_px))


def _raster_baseline(tmp_path, canvas, svgs):
    """The pre-change path: screenshot every wrapper, composite PNGs to PDF."""
    from utils.slide_capture import _render_admitted_htmls_over_pipe
    from workers.public_export_worker import _build_pdf

    wrappers = _slide_wrappers(svgs, [None] * len(svgs), canvas)
    rendered = _render_admitted_htmls_over_pipe(
        wrappers, timeout=60, width_px=canvas.width_px, height_px=canvas.height_px)
    store = MemoryStore(tmp_path)
    operation_id = uuid.uuid4()
    refs = [store.put(png, operation_id=operation_id, kind="slide-png", media_type="image/png")
            for png, _ in rendered]
    target = tmp_path / "raster.pdf"
    _build_pdf(refs, store, target, canvas)
    return target.read_bytes()


@requires_chrome
@pytest.mark.parametrize("canvas", [
    canvas_metadata_for_aspect_ratio("16:9"),
    document_canvas_for_orientation("landscape"),
], ids=["authored-16x9", "document-a4-landscape"])
def test_svg_pdf_export_has_a_real_text_layer(tmp_path, monkeypatch, canvas):
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "900")
    svgs = [_svg(canvas, title, body) for title, body in zip(TITLES, BODIES, strict=True)]
    job, store = _job_and_store(tmp_path, canvas, svgs)

    output = asyncio.run(execute_svg_export(job, store))

    evidence = output.evidence
    assert evidence["requestedExportMode"] == evidence["deliveredExportMode"] == "svg-vector-pdf"
    assert evidence["pdfTextLayer"] == "vector"
    assert evidence["structuralReadback"] == {
        "slideCount": 2, "rasterized": False, "vectorText": True}
    assert output.artifact.kind == "presentation-pdf"
    vector = store.read(output.artifact)
    width_emu, height_emu = canvas_physical_size(canvas)
    with pdfium.PdfDocument(vector) as document:
        assert len(document) == len(svgs)
        for index in range(len(document)):
            page = document[index]
            assert page.get_size() == pytest.approx(
                (width_emu / 12700, height_emu / 12700), abs=1.0)
            page.close()
        texts = _page_texts(document)
        fonts = _embedded_fonts(document)
    for text, title, body in zip(texts, TITLES, BODIES, strict=True):
        assert title in text
        assert body in text
    assert fonts and all(fonts.values()), fonts
    assert any("pretendard" in name.casefold() for name in fonts), fonts

    raster = _raster_baseline(tmp_path, canvas, svgs)
    with pdfium.PdfDocument(raster) as document:
        raster_texts = _page_texts(document)
        raster_fonts = _embedded_fonts(document)
    assert len(raster_texts) == len(svgs)
    assert sum(len(text.strip()) for text in raster_texts) == 0
    assert raster_fonts == {}
    # Same geometry as the screenshot: the print scale maps the CSS canvas
    # exactly onto the physical page (A4 canvases are not 96 px/in).
    for index in range(len(svgs)):
        drawn = [_draw(payload, index, canvas) for payload in (vector, raster)]
        difference = ImageChops.difference(*drawn).convert("L")
        assert ImageStat.Stat(difference).mean[0] < 2.0
    print(f"[{canvas.width_px}x{canvas.height_px}] vector: {len(vector)} bytes, "
          f"{sum(len(t.strip()) for t in texts)} chars, fonts={sorted(fonts)}; "
          f"raster: {len(raster)} bytes, 0 chars")


def test_print_scale_maps_the_css_canvas_onto_its_physical_paper():
    authored = canvas_metadata_for_aspect_ratio("16:9")
    a4 = document_canvas_for_orientation("portrait")
    for canvas, expected in ((authored, 1.0), (a4, 7560000 / 914400 * 96 / a4.width_px)):
        width_emu, height_emu = canvas_physical_size(canvas)
        assert print_scale(width_px=canvas.width_px, height_px=canvas.height_px,
                           paper_width_in=width_emu / 914400,
                           paper_height_in=height_emu / 914400) == pytest.approx(expected)
    with pytest.raises(ValueError, match="aspect ratio"):
        print_scale(width_px=1280, height_px=720, paper_width_in=10, paper_height_in=10)
