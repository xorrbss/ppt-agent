"""Masked template body renders (docs/template-body-renders-design-20261105.md).

Synthetic PDFs only, built here with PDFium: text objects with fictional words, pictures and shapes.
No customer data; the secret words must never survive masking or reach a stored string.
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PIL import Image, ImageDraw, ImageFont

from models.svg_template_frame import (
    SvgTemplateBodyComposition, SvgTemplateBodyExemplar, SvgTemplateBodyRender,
)
from services.svg_templates import body_render_mask as mask
from services.svg_templates import body_renders as renders
from services.svg_templates import generation

SECRET_WORDS = ("Quokkafield", "Zephyrine", "Marmalade")
#: Page in points; rendered at 2 canvas px per point (canvas 1200 x 800).
PAGE_W, PAGE_H, CANVAS_W = 600, 400, 1200
BODY = (40, 120, 1120, 640)  # x, y, width, height in canvas px
OWNER = uuid.UUID("11111111-2222-3333-4444-555555555555")
TENANT = "tenant-a"


def _text(document, page, words: str, x: float, y: float, size: float = 14, color=(30, 60, 160)):
    font = raw.FPDFText_LoadStandardFont(document.raw, b"Helvetica")
    obj = raw.FPDFPageObj_CreateTextObj(document.raw, font, size)
    buffer = ctypes.create_string_buffer((words + "\x00").encode("utf-16-le"))
    raw.FPDFText_SetText(obj, ctypes.cast(buffer, raw.FPDF_WIDESTRING))
    raw.FPDFPageObj_SetFillColor(obj, *color, 255)
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page.raw, obj)


def _picture(document, page, image: Image.Image, x: float, y: float, w: float, h: float):
    obj = pdfium.PdfImage.new(document)
    bitmap = pdfium.PdfBitmap.from_pil(image)
    obj.set_bitmap(bitmap)
    bitmap.close()
    obj.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, y))
    page.insert_obj(obj)


def _rect(page, x, y, w, h, color):
    rect = raw.FPDFPageObj_CreateNewRect(x, y, w, h)
    raw.FPDFPageObj_SetFillColor(rect, *color, 255)
    raw.FPDFPath_SetDrawMode(rect, raw.FPDF_FILLMODE_WINDING, 0)
    raw.FPDFPage_InsertObject(page.raw, rect)


def _worded_picture(text="HELLO WORLD 2027", size=(480, 160)) -> Image.Image:
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=56)
    except TypeError:  # Pillow without sized default fonts
        font = ImageFont.load_default()
    draw.text((16, 40), text, fill=(0, 0, 0), font=font)
    return image


def _deck(pages: int = 2, *, picture: Image.Image | None = None, icon: bool = False) -> bytes:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        page = document.new_page(PAGE_W, PAGE_H)
        _rect(page, 30, 40, 250, 120, (200, 220, 240))            # a card inside the body
        _text(document, page, SECRET_WORDS[0] + " 194", 40, 300)  # body text
        _text(document, page, SECRET_WORDS[1], 320, 260, size=24, color=(200, 30, 30))
        _text(document, page, SECRET_WORDS[2], 20, 380)           # header text (outside the body)
        if picture is not None:
            _picture(document, page, picture, 330, 60, 240, 80)   # 480 x 160 canvas px: larger than an icon
        if icon:
            _picture(document, page, _worded_picture("NEW", (100, 100)), 300, 180, 40, 40)  # 80 px: an icon
        page.gen_content()
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _no_text_left(png: bytes) -> None:
    """The stored PNG is a picture without a text layer, and no fixture word is in its bytes."""
    assert png.startswith(b"\x89PNG")
    for word in SECRET_WORDS:
        assert word.encode() not in png


def _pixel(png: bytes, canvas_x: float, canvas_y: float) -> tuple[int, int, int]:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    ratio = image.width / BODY[2]
    return image.getpixel((round((canvas_x - BODY[0]) * ratio), round((canvas_y - BODY[1]) * ratio)))


# --- masking ----------------------------------------------------------------------------------------


def test_every_text_becomes_a_bar_and_no_character_survives():
    result = mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None, ocr_name="unavailable",
                                expected_pages=2)
    assert result.text_objects == 3 and result.ocr == "unavailable"
    _no_text_left(result.png)
    # The red heading's line is now a red bar at its place (PDF y 260 -> canvas y 280 at its middle).
    red, green, blue = _pixel(result.png, 2 * 320 + 20, (PAGE_H - 260 - 7) * 2)
    assert red > 150 and green < 90 and blue < 90
    # The card (a shape, not text) is untouched.
    assert _pixel(result.png, 2 * 100, (PAGE_H - 100) * 2) == (200, 220, 240)
    receipt = result.mask_receipt()
    assert receipt == {"version": mask.RENDER_VERSION, "text_objects": 3, "residual_chars": 0, "pictures": 0,
                       "pictures_covered": 0, "text_effects": 0, "ocr": "unavailable", "final_check": "unavailable"}


def test_the_masked_pdf_page_has_an_empty_text_layer(monkeypatch):
    seen = {}
    real = mask._mask_and_render

    def spy(pdfium_module, raw_module, page, *args):
        result = real(pdfium_module, raw_module, page, *args)
        textpage = page.get_textpage()
        seen["chars"] = textpage.count_chars()
        seen["text"] = textpage.get_text_range()
        textpage.close()
        return result

    monkeypatch.setattr(mask, "_mask_and_render", spy)
    mask.mask_pdf_page(_deck(), 1, canvas_width_px=CANVAS_W, crop=BODY, ocr=None, ocr_name="unavailable")
    assert seen == {"chars": 0, "text": ""}


def test_a_page_whose_text_survives_is_rejected(monkeypatch):
    class Stubborn:
        def __init__(self, page):
            self.page = page

        def count_chars(self):
            return 5

        def close(self):
            pass

    real = mask._mask_and_render

    def keep_text(pdfium_module, raw_module, page, *args):
        page.get_textpage = lambda: Stubborn(page)
        return real(pdfium_module, raw_module, page, *args)

    monkeypatch.setattr(mask, "_mask_and_render", keep_text)
    with pytest.raises(mask.BodyRenderRejected, match="text_survived"):
        mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None, ocr_name="unavailable")


@pytest.mark.parametrize(("pages", "expected", "crop", "code"), [
    (2, 3, BODY, "page_count_mismatch"),     # a hidden slide shifts every page: no render
    (2, None, (0, 0, 4, 4), "crop_invalid"),
    (2, None, (900, 100, 600, 300), "crop_invalid"),
])
def test_unverifiable_pages_are_rejected(pages, expected, crop, code):
    with pytest.raises(mask.BodyRenderRejected, match=code):
        mask.mask_pdf_page(_deck(pages), 0, canvas_width_px=CANVAS_W, crop=crop, ocr=None, ocr_name="unavailable",
                           expected_pages=expected)


def _flat_block(png: bytes, box_pt) -> bool:
    """Whether the picture at ``box_pt`` (PDF x0, y0, x1, y1) is one colour in the render (covered)."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    ratio = image.width / BODY[2]
    x0, y0, x1, y1 = box_pt
    box = tuple(round(value * ratio) for value in (2 * x0 - BODY[0] + 4, (PAGE_H - y1) * 2 - BODY[1] + 4,
                                                   2 * x1 - BODY[0] - 4, (PAGE_H - y0) * 2 - BODY[1] - 4))
    return len(image.crop(box).getcolors(maxcolors=4) or ()) == 1


def test_without_ocr_every_picture_with_edges_is_covered_icons_included():
    result = mask.mask_pdf_page(_deck(picture=_worded_picture(), icon=True), 0, canvas_width_px=CANVAS_W, crop=BODY,
                                ocr=None, ocr_name="unavailable")
    assert (result.pictures, result.pictures_covered, result.ocr) == (2, 2, "unavailable")
    assert _flat_block(result.png, (330, 60, 570, 140)) and _flat_block(result.png, (300, 180, 340, 220))


def test_an_icon_sized_logo_with_a_word_is_read_and_covered():
    # A 105 x 38 px logo: icon-sized, but it carries a word.
    logo = _worded_picture("ACMECORP", (220, 80))
    document = pdfium.PdfDocument.new()
    page = document.new_page(PAGE_W, PAGE_H)
    _picture(document, page, logo, 320, 200, 52, 19)
    page.gen_content()
    output = io.BytesIO()
    document.save(output)
    read = []

    def ocr_text(png):
        read.append(png)
        return "ACMECORP" if len(read) == 1 else ""

    result = mask.mask_pdf_page(output.getvalue(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=ocr_text,
                                ocr_name="liteparse")
    assert (result.pictures, result.pictures_covered) == (1, 1) and len(read) == 2  # the logo, then the final crop
    assert _flat_block(result.png, (320, 200, 372, 219))
    without = mask.mask_pdf_page(output.getvalue(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None,
                                 ocr_name="unavailable")
    assert without.pictures_covered == 1


def test_a_near_white_picture_with_a_small_caption_is_always_read_and_has_edges():
    big = Image.new("RGB", (1600, 1000), (245, 245, 245))
    draw = ImageDraw.Draw(big)
    try:
        font = ImageFont.load_default(size=47)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((60, 860), "Falcon", fill=(30, 30, 30), font=font)
    # Without OCR the strokes keep their edges at full resolution, so the picture is covered.
    assert mask.strong_edge_pixels(big) >= mask.MIN_EDGE_PIXELS
    assert mask.strong_edge_pixels(Image.new("RGB", (1600, 1000), (245, 245, 245))) == 0
    read = []
    result = mask.mask_pdf_page(_deck(picture=big), 0, canvas_width_px=CANVAS_W, crop=BODY,
                                ocr=lambda png: read.append(png) or ("Falcon" if len(read) == 1 else ""),
                                ocr_name="liteparse")
    assert result.pictures_covered == 1 and len(read) == 2
    without = mask.mask_pdf_page(_deck(picture=big), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None,
                                 ocr_name="unavailable")
    assert without.pictures_covered == 1


def test_with_ocr_only_a_picture_that_reads_as_text_is_covered():
    calls = []

    def worded(png):
        calls.append(png)
        return "Quokkafield 2027" if len(calls) == 1 else ""

    result = mask.mask_pdf_page(_deck(picture=_worded_picture()), 0, canvas_width_px=CANVAS_W, crop=BODY,
                                ocr=worded, ocr_name="liteparse")
    assert (result.pictures, result.pictures_covered, result.ocr, result.final_check) == (1, 1, "liteparse", "passed")
    clean = mask.mask_pdf_page(_deck(picture=_worded_picture()), 0, canvas_width_px=CANVAS_W, crop=BODY,
                               ocr=lambda png: " ~ | ", ocr_name="tesseract")
    assert (clean.pictures, clean.pictures_covered, clean.ocr) == (1, 0, "tesseract")
    # An OCR call that fails on a picture leaves it unread: covered (the final crop then reads fine).
    reads = []
    failed = mask.mask_pdf_page(_deck(picture=_worded_picture()), 0, canvas_width_px=CANVAS_W, crop=BODY,
                                ocr=lambda png: reads.append(png) or (None if len(reads) == 1 else ""),
                                ocr_name="liteparse")
    assert failed.pictures_covered == 1


def test_a_one_colour_picture_is_neither_read_nor_covered():
    flat = Image.new("RGB", (480, 160), (180, 180, 180))
    reads = []
    result = mask.mask_pdf_page(_deck(picture=flat), 0, canvas_width_px=CANVAS_W, crop=BODY,
                                ocr=lambda png: reads.append(png) or "", ocr_name="liteparse")
    assert (result.pictures, result.pictures_covered) == (0, 0) and len(reads) == 1  # the final crop only


def test_annotations_and_form_fields_are_not_drawn():
    document = pdfium.PdfDocument.new()
    page = document.new_page(PAGE_W, PAGE_H)
    annot = raw.FPDFPage_CreateAnnot(page.raw, raw.FPDF_ANNOT_STAMP)
    raw.FPDFAnnot_SetRect(annot, raw.FS_RECTF(100, 150, 500, 250))
    font = raw.FPDFText_LoadStandardFont(document.raw, b"Helvetica")
    text = raw.FPDFPageObj_CreateTextObj(document.raw, font, 48.0)
    buffer = ctypes.create_string_buffer((SECRET_WORDS[0] + "\x00").encode("utf-16-le"))
    raw.FPDFText_SetText(text, ctypes.cast(buffer, raw.FPDF_WIDESTRING))
    raw.FPDFPageObj_SetFillColor(text, 0, 0, 0, 255)
    raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 120, 180)
    assert raw.FPDFAnnot_AppendObject(annot, text)
    raw.FPDFPage_CloseAnnot(annot)
    page.gen_content()
    output = io.BytesIO()
    document.save(output)
    result = mask.mask_pdf_page(output.getvalue(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None,
                                ocr_name="unavailable")
    image = Image.open(io.BytesIO(result.png)).convert("L")
    assert image.getextrema()[0] > 240  # nothing dark: the stamp's text was not drawn


def test_text_that_is_no_text_object_fails_the_final_check():
    """Path-drawn text (WordArt, metafiles) is read on the masked crop before any bar is drawn."""
    seen = []

    def final_reads_a_word(png):
        seen.append(png)
        return "Quokkafield"

    with pytest.raises(mask.BodyRenderRejected, match="final_ocr_text"):
        mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=final_reads_a_word,
                           ocr_name="liteparse")
    # The crop OCR reads carries no bar: the red heading's bar is not in it.
    check = Image.open(io.BytesIO(seen[-1])).convert("RGB")
    red, green, blue = check.getpixel((2 * 320 + 20 - BODY[0], (PAGE_H - 260 - 7) * 2 - BODY[1]))
    assert not (red > 150 and green < 90 and blue < 90)
    with pytest.raises(mask.BodyRenderRejected, match="final_ocr_failed"):
        mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=lambda png: None, ocr_name="liteparse")
    noise = mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=lambda png: "가나 abcd 123456 |",
                               ocr_name="liteparse")
    assert noise.final_check == "passed"


@pytest.mark.parametrize(("text", "expected"), [
    ("", False), ("가나 abcd 1234567", False), ("가나다", True), ("Hello", True), ("Dress", True),
])
def test_final_text_rule(text, expected):
    assert mask.final_text_found(text) is expected


def test_a_translucent_grey_shadow_holding_a_blurred_text_is_removed():
    shadow = Image.new("RGBA", (480, 160), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((20, 40), "SHADOWED WORDS", fill=(90, 90, 90, 120),
                                font=ImageFont.load_default(size=40) if hasattr(ImageFont, "load_default") else None)
    result = mask.mask_pdf_page(_deck(picture=shadow), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None,
                                ocr_name="unavailable")
    assert result.text_effects == 1 and result.pictures == 0


def test_a_picture_inside_a_text_line_is_removed_with_the_text():
    glow = Image.new("RGB", (200, 30), (250, 200, 40))
    ImageDraw.Draw(glow).rectangle((10, 5, 190, 25), fill=(30, 30, 30))
    document = pdfium.PdfDocument.new()
    page = document.new_page(PAGE_W, PAGE_H)
    _picture(document, page, glow, 318, 256, 100, 22)          # within the 24 pt heading below
    _text(document, page, SECRET_WORDS[1], 320, 260, size=24)
    page.gen_content()
    output = io.BytesIO()
    document.save(output)
    result = mask.mask_pdf_page(output.getvalue(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None,
                                ocr_name="unavailable")
    assert (result.text_effects, result.pictures) == (1, 0)


@pytest.mark.parametrize(("text", "expected"), [
    ("", False), ("  ~ | . ", False), ("가", False), ("가나", True), ("ab", False), ("abc", True),
    ("7", False), ("27", True), ("SP\n", False),
])
def test_recognised_text_rule(text, expected):
    assert mask.recognised_text(text) is expected


def test_the_product_ocr_runtime_reads_a_worded_picture_when_installed():
    reader, name = mask.resolve_ocr_reader()
    if reader is None:
        pytest.skip("no OCR runtime (tesseract or LiteParse) on this machine")
    assert name in {"tesseract", "liteparse"}
    buffer = io.BytesIO()
    _worded_picture().save(buffer, format="PNG")
    assert mask.recognised_text(reader(buffer.getvalue()))
    result = mask.mask_pdf_page(_deck(picture=_worded_picture()), 0, canvas_width_px=CANVAS_W, crop=BODY,
                                ocr=reader, ocr_name=name)
    assert result.pictures_covered == 1 and result.ocr == name and result.final_check == "passed"


# --- registration -------------------------------------------------------------------------------------


def _exemplar(page: int, **extra) -> SvgTemplateBodyExemplar:
    return SvgTemplateBodyExemplar(source_page=page, region_width=BODY[2], region_height=BODY[3], kinds=("cards",),
                                   sections=2, text_items=12,
                                   items=({"kind": "card", "x": 0, "y": 0, "w": 100, "h": 60},), **extra)


def _composition(*exemplars) -> SvgTemplateBodyComposition:
    return SvgTemplateBodyComposition.model_validate({
        "grammar": {"pages_sampled": 2, "sections": (1, 2, 3), "numbered_headings": 0.5, "key_message_bar": 0.5,
                    "content_kinds_per_page": (1, 2, 3), "kind_shares": (("cards", 1.0),),
                    "text_items": (10, 12, 14), "ink_coverage": 0.5, "bottom_reach": 0.9,
                    "text_sizes": (18.0, 14.5, 12.0)},
        "exemplars": [exemplar.model_dump(mode="json") for exemplar in exemplars]})


def test_an_exemplar_without_a_render_serializes_as_before():
    plain = _exemplar(3)
    dumped = plain.model_dump(mode="json")
    assert "render" not in dumped and "region_x" not in dumped and "region_y" not in dumped
    placed = _exemplar(3, region_x=40, region_y=120)
    assert placed.model_dump(mode="json")["region_x"] == 40 and "render" not in placed.model_dump(mode="json")


def test_render_exemplar_pages_masks_each_exemplar_and_honours_the_exclusions(tmp_path):
    package = SimpleNamespace(
        body_composition=_composition(_exemplar(0, region_x=BODY[0], region_y=BODY[1]),
                                      _exemplar(1, region_x=BODY[0], region_y=BODY[1]), _exemplar(0)),
        source_canvas=SimpleNamespace(width_px=CANVAS_W))
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"not a zip")  # the slide count is unknown: no page count check
    rendered, receipt = renders.render_exemplar_pages(package, source, captured_pdf=_deck(),
                                                      excluded_pages=frozenset({2}), ocr=None, ocr_name="unavailable")
    assert sorted(rendered) == [0]
    assert receipt == {"exemplars": 3, "rendered": 1, "excluded": 1, "rejected": {"region_unknown": 1},
                       "status": "rendered", "ocr": "unavailable"}
    _no_text_left(rendered[0].png)


class _Session:
    """Records the savepoints the store calls run in."""

    def __init__(self):
        self.savepoints = 0

    def begin_nested(self):
        session = self

        class Savepoint:
            async def __aenter__(self):
                session.savepoints += 1

            async def __aexit__(self, *exc):
                return False
        return Savepoint()


class _Package(SimpleNamespace):
    def model_copy(self, update):
        return _Package(**{**vars(self), **update})


def _stub_attach(monkeypatch, renders_by_index, fail_on=()):
    stored = []

    class Service:
        async def persist_bytes(self, session, **kwargs):
            if len(stored) in fail_on:
                stored.append(None)
                raise RuntimeError("object store down")
            stored.append(kwargs)
            digest = hashlib.sha256(kwargs["payload"]).hexdigest()
            return SimpleNamespace(artifact_id=uuid.uuid4(), owner_id=kwargs["owner_id"],
                                   tenant_id=kwargs["tenant_id"], sha256=digest)

    monkeypatch.setattr(renders, "render_exemplar_pages",
                        lambda *a, **k: (renders_by_index, {"status": "rendered", "rendered": len(renders_by_index),
                                                            "ocr": "unavailable"}))
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: Service())
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: (OWNER, TENANT))
    monkeypatch.setattr("services.svg_templates.frame_fonts.artifact_digest", lambda value: "sha256:new")
    return stored


def test_attach_body_renders_stores_owner_bound_references_and_recomputes_the_digest(monkeypatch, tmp_path):
    composition = _composition(_exemplar(0, region_x=BODY[0], region_y=BODY[1]))
    package = _Package(body_composition=composition, source_canvas=SimpleNamespace(width_px=CANVAS_W), digest="old")
    masked = mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None, ocr_name="unavailable")
    stored = _stub_attach(monkeypatch, {0: masked})
    session = _Session()
    updated, ids, receipt = asyncio.run(renders.attach_body_renders(session, package, tmp_path / "deck.pptx",
                                                                    captured_pdf=None, operation_id=uuid.uuid4()))
    render = updated.body_composition.exemplars[0].render
    assert isinstance(render, SvgTemplateBodyRender)
    assert (render.owner_id, render.tenant_id) == (str(OWNER), TENANT) and ids == [render.artifact_id]
    assert render.sha256 == hashlib.sha256(masked.png).hexdigest() and updated.digest == "sha256:new"
    assert stored[0]["kind"] == "image-derivative" and stored[0]["retention_class"] == "published"
    assert session.savepoints == 1 and receipt == {"status": "rendered", "rendered": 1, "ocr": "unavailable"}
    # Nothing stored about the render is text of the page.
    strings = list(_strings(updated.body_composition.model_dump(mode="json")))
    assert not any(word in value for word in SECRET_WORDS for value in strings)


def test_a_render_that_cannot_be_stored_is_left_out_and_the_registration_goes_on(monkeypatch, tmp_path):
    composition = _composition(_exemplar(0, region_x=BODY[0], region_y=BODY[1]),
                               _exemplar(1, region_x=BODY[0], region_y=BODY[1]))
    package = _Package(body_composition=composition, source_canvas=SimpleNamespace(width_px=CANVAS_W), digest="old")
    masked = mask.mask_pdf_page(_deck(), 0, canvas_width_px=CANVAS_W, crop=BODY, ocr=None, ocr_name="unavailable")
    _stub_attach(monkeypatch, {0: masked, 1: masked}, fail_on={0})
    updated, ids, receipt = asyncio.run(renders.attach_body_renders(_Session(), package, tmp_path / "deck.pptx",
                                                                    captured_pdf=None, operation_id=uuid.uuid4()))
    exemplars = updated.body_composition.exemplars
    assert exemplars[0].render is None and exemplars[1].render is not None and len(ids) == 1
    assert receipt["rendered"] == 1 and receipt["unstored"] == 1
    # No store at all: the package is registered without renders.
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service",
                        lambda: (_ for _ in ()).throw(RuntimeError("no store")))
    same, ids, receipt = asyncio.run(renders.attach_body_renders(_Session(), package, tmp_path / "deck.pptx",
                                                                 captured_pdf=None, operation_id=None))
    assert same is package and ids == [] and receipt["unstored"] == 2


def test_attach_body_renders_leaves_the_package_alone_when_nothing_renders(monkeypatch, tmp_path):
    package = SimpleNamespace(body_composition=_composition(_exemplar(0)), digest="old")
    monkeypatch.setattr(renders, "render_exemplar_pages", lambda *a, **k: ({}, {"status": "pdf_unavailable"}))
    assert asyncio.run(renders.attach_body_renders(None, package, tmp_path / "x.pptx", captured_pdf=None,
                                                   operation_id=None)) == (package, [], {"status": "pdf_unavailable"})

    def boom(*a, **k):
        raise RuntimeError("libreoffice crashed")

    monkeypatch.setattr(renders, "render_exemplar_pages", boom)
    assert asyncio.run(renders.attach_body_renders(None, package, tmp_path / "x.pptx", captured_pdf=None,
                                                   operation_id=None)) == (
        package, [], {"status": "error", "reason": "RuntimeError"})


def test_the_deck_pdf_keeps_hidden_slides_and_is_converted_once(monkeypatch, tmp_path):
    from services.svg_templates import source_review

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        (tmp_path / "out").mkdir(exist_ok=True)
        Path(args[args.index("--outdir") + 1], "source.pdf").write_bytes(_deck(1))
        return SimpleNamespace(returncode=0)

    source = tmp_path / "deck.pptx"
    source.write_bytes(b"deck")
    package = SimpleNamespace(source_digest=source_review.bytes_digest(b"deck"),
                              source_canvas=SimpleNamespace(width_px=CANVAS_W, height_px=800),
                              frames=(SimpleNamespace(source_page=0),), font_manifest=None)
    monkeypatch.setattr(source_review, "_libreoffice_executable", lambda: "soffice")
    monkeypatch.setattr(source_review, "_libreoffice_version", lambda executable: "LibreOffice 24.2")
    monkeypatch.setattr(source_review, "_private_fontconfig", lambda directory, package: {})
    monkeypatch.setattr(source_review.subprocess, "run", run)
    version, pdf = source_review.convert_source_pdf_libreoffice(source, package)
    assert version == "LibreOffice 24.2" and pdf.startswith(b"%PDF")
    assert '"ExportHiddenSlides":{"type":"boolean","value":"true"}' in calls[0][calls[0].index("--convert-to") + 1]
    # The source review rasterises from the same PDF without a second conversion.
    _version, images = source_review._render_source_libreoffice(source, package, (version, pdf))
    assert len(calls) == 1 and list(images) == [0]


# --- generation: owner-scoped loading, selection, prompt -------------------------------------------


def _render(payload: bytes, owner=OWNER, tenant=TENANT) -> dict:
    return {"artifact_id": str(uuid.uuid4()), "owner_id": str(owner), "tenant_id": tenant,
            "sha256": hashlib.sha256(payload).hexdigest(), "width": 10, "height": 10,
            "mask": {"version": mask.RENDER_VERSION, "text_objects": 1, "residual_chars": 0, "pictures": 0,
                     "pictures_covered": 0, "ocr": "unavailable"}}


class _Store:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads, self.resolved = payloads, []

    async def resolve_ref(self, session, *, artifact_id, owner_id, tenant_id, expected_digest, check_object):
        self.resolved.append((owner_id, tenant_id))
        return SimpleNamespace(kind="image-derivative", media_type="image/png", sha256=expected_digest)

    async def read_verified_ref_bytes(self, ref, max_bytes):
        return self.payloads[ref.sha256], None


def _png(color) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_renders_load_only_for_the_owner_of_the_template_pin(monkeypatch):
    mine, theirs = _png((1, 2, 3)), _png((4, 5, 6))
    exemplars = [{"render": _render(mine)}, {"render": _render(theirs, owner=uuid.uuid4())},
                 {"render": _render(theirs, tenant="tenant-b")}, {}]
    store = _Store({hashlib.sha256(mine).hexdigest(): mine, hashlib.sha256(theirs).hexdigest(): theirs})
    monkeypatch.setattr("services.svg_templates.selection_material.template_package",
                        lambda selection: {"body_composition": {"exemplars": exemplars}})
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: store)
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: (OWNER, TENANT))
    renders.clear_render_cache()
    counts = asyncio.run(renders.ensure_body_renders(None, {"mode": "template-redesign"}))
    assert counts == {"loaded": 1, "cached": 0, "missing": 0, "scope_mismatch": 2}
    assert store.resolved == [(OWNER, TENANT)]
    assert renders.cached_render(str(OWNER), TENANT, hashlib.sha256(mine).hexdigest()) == mine
    assert renders.cached_render(str(OWNER), TENANT, hashlib.sha256(theirs).hexdigest()) is None
    # A render loaded for one owner is never served to another, even with the same digest.
    assert renders.cached_render(str(uuid.uuid4()), TENANT, hashlib.sha256(mine).hexdigest()) is None
    # A second load is served from the process cache; a free-design selection loads nothing.
    assert asyncio.run(renders.ensure_body_renders(None, {"mode": "template-redesign"}))["cached"] == 1
    assert asyncio.run(renders.ensure_body_renders(None, {"mode": "free-design"}))["loaded"] == 0
    # Without an owner in context nothing is read.
    from fastapi import HTTPException

    def no_principal():
        raise HTTPException(401, "svg_template_principal_required")

    monkeypatch.setattr("services.svg_templates.catalog.principal", no_principal)
    renders.clear_render_cache()
    store.resolved.clear()
    assert asyncio.run(renders.ensure_body_renders(None, {"mode": "template-redesign"}))["scope_mismatch"] == 3
    assert store.resolved == [] and renders.cached_render(str(OWNER), TENANT, hashlib.sha256(mine).hexdigest()) is None


def test_a_render_whose_bytes_do_not_match_is_missing(monkeypatch):
    payload = _png((7, 8, 9))
    render = _render(payload)
    store = _Store({render["sha256"]: _png((9, 9, 9))})
    monkeypatch.setattr("services.svg_templates.selection_material.template_package",
                        lambda selection: {"body_composition": {"exemplars": [{"render": render}]}})
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: store)
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: (OWNER, TENANT))
    renders.clear_render_cache()
    assert asyncio.run(renders.ensure_body_renders(None, {"mode": "template-redesign"}))["missing"] == 1
    assert renders.cached_render(str(OWNER), TENANT, render["sha256"]) is None
    # A store that cannot even be configured fails open too.
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service",
                        lambda: (_ for _ in ()).throw(RuntimeError("no store")))
    assert asyncio.run(renders.ensure_body_renders(None, {"mode": "template-redesign"}))["missing"] == 1


def _stub_page(monkeypatch, exemplars, role="body"):
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: {"mode": "template-redesign"})
    monkeypatch.setattr("services.svg_templates.selection_material.template_package",
                        lambda selection: {"body_composition": {"exemplars": exemplars}})
    monkeypatch.setattr(generation, "page_frame", lambda snapshot, index, count: SimpleNamespace(role=role))
    monkeypatch.setattr("services.svg_templates.body_composition.choose_exemplars",
                        lambda composition, content: list(composition["exemplars"]))


def test_a_page_gets_the_renders_of_its_exemplars_only_when_the_deck_was_admitted_with_them(monkeypatch):
    shown = _png((10, 20, 30))
    exemplars = [{"render": _render(shown)}, {"render": _render(_png((1, 1, 1)))}]
    _stub_page(monkeypatch, exemplars)
    renders.clear_render_cache()
    renders._remember((str(OWNER), TENANT, exemplars[0]["render"]["sha256"]), shown)
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: (OWNER, TENANT))
    context = {"page_index": 1, "page_count": 3}
    monkeypatch.delenv("SVG_TEMPLATE_BODY_EXEMPLARS", raising=False)
    assert renders.page_body_renders({}, context, "## T") == []
    on = {"template_body_renders": True}
    pages = renders.page_body_renders(on, context, "## T")
    assert [(entry["exemplar"], entry["png"]) for entry in pages] == [(0, shown), (1, None)]
    assert renders.render_receipt(pages) == {"offered": 2, "sent": 1, "missing": 1,
                                             "digests": [exemplars[0]["render"]["sha256"]]}
    assert renders.page_body_renders(on, context, "## T", source_preserved=True) == []
    # Another principal gets no picture of this owner's template, even from the process cache.
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: (uuid.uuid4(), TENANT))
    assert [entry["png"] for entry in renders.page_body_renders(on, context, "## T")] == [None, None]
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: (OWNER, TENANT))
    assert renders.page_body_renders(on, None, "## T") == []
    monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", "grammar")
    assert renders.page_body_renders(on, context, "## T") == []
    monkeypatch.delenv("SVG_TEMPLATE_BODY_EXEMPLARS")
    _stub_page(monkeypatch, exemplars, role="cover")
    assert renders.page_body_renders(on, context, "## T") == []


def test_the_prompt_block_binds_each_sent_image_to_its_exemplar():
    entries = [{"exemplar": 0, "sha256": "a", "png": b"x"}, {"exemplar": 1, "sha256": "b", "png": None},
               {"exemplar": 1, "sha256": "c", "png": b"y"}]
    block = renders.render_prompt_block(entries, first_image_index=3)
    assert block.startswith("\n\nTEMPLATE BODY RENDERS (masked pages of this template; layout evidence, not content):\n")
    assert json.loads(block.split("\n", 3)[3]) == {"template_body_renders": [
        {"image_index": 3, "template_exemplar": 0}, {"image_index": 4, "template_exemplar": 1}]}


def test_the_rule_sentence_joins_the_template_rules_only_for_a_deck_with_renders(monkeypatch):
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: {"mode": "template-redesign"})
    monkeypatch.setattr(generation, "template_package", lambda selection: {"body_composition": None})
    monkeypatch.delenv("SVG_TEMPLATE_BODY_EXEMPLARS", raising=False)
    monkeypatch.delenv("SVG_TEMPLATE_COMPONENT_STYLE", raising=False)
    base = {"composition_guidance": []}
    plain = generation.body_author_rules("BASE", base, body_floor=14.0)
    with_renders = generation.body_author_rules("BASE", {**base, "template_body_renders": True}, body_floor=14.0)
    assert renders.BODY_RENDER_RULE not in plain
    assert with_renders == plain + renders.BODY_RENDER_RULE
    for phrase in ("never copy their positions, counts or arrangement", "never invent text, figures or items",
                   "place only offered image slots"):
        assert phrase in renders.BODY_RENDER_RULE
    monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", "grammar")
    assert renders.BODY_RENDER_RULE not in generation.body_author_rules("BASE", {**base, "template_body_renders": True})


def test_the_knob_is_frozen_into_a_template_snapshot_and_absent_when_off(monkeypatch):
    selection = {"mode": "template-redesign", "template_pin": {"templateId": "tpl"},
                 "style_projection": {"fonts": {}}}
    monkeypatch.setattr(generation, "template_package", lambda value: {
        "font_manifest": {"assets": [{"family": "Pretendard", "weight": 700, "face": "Bold"}]}})
    request = SimpleNamespace(svg_template_pin={"templateId": "tpl"}, _svg_template_selection=selection)

    def snapshot():
        value = {"provenance": {}, "tokens": {}, "digest": ""}
        generation.apply_template_snapshot(value, request)
        return value

    monkeypatch.delenv("SVG_TEMPLATE_BODY_RENDERS", raising=False)
    off = snapshot()
    monkeypatch.setenv("SVG_TEMPLATE_BODY_RENDERS", "on")
    on = snapshot()
    assert "template_body_renders" not in off and on["template_body_renders"] is True
    assert {k: v for k, v in on.items() if k not in {"template_body_renders", "digest"}} == \
        {k: v for k, v in off.items() if k != "digest"}
    assert renders.snapshot_body_renders(on) and not renders.snapshot_body_renders(off)
    # The model's design view never shows the flag.
    from services.generation_core.authoring.author_slide_svg import svg_prompt_design
    assert "template_body_renders" not in json.dumps(svg_prompt_design({**on, "svg_template": None}))


def test_the_author_sends_the_render_images_and_their_block(monkeypatch):
    from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V9
    from tests.unit.test_svg_author_prompt_cache import PAGES, SLIDE_COUNT, _Wire, _snapshot, _stub
    from services.generation_core.authoring import author_slide_svg as module
    from enums.llm_provider import LLMProvider
    from utils.hashing import canonical_json_digest

    wire, seen = _Wire(), []
    _stub(monkeypatch, LLMProvider.ANTHROPIC, wire, seen)
    shown = _png((40, 50, 60))
    entries = [{"exemplar": 0, "sha256": hashlib.sha256(shown).hexdigest(), "png": shown},
               {"exemplar": 1, "sha256": "f" * 64, "png": None}]
    monkeypatch.setattr(generation, "template_selection",
                        lambda snapshot: {"mode": "template-redesign"} if snapshot.get("svg_template") else None)
    monkeypatch.setattr(generation, "template_page_context", lambda *a, **k: {"page_index": 1, "page_count": 3})
    monkeypatch.setattr(generation, "template_prompt_facts", lambda *a, **k: {})
    monkeypatch.setattr(generation, "template_exemplar_facts", lambda *a, **k: {})
    monkeypatch.setattr(generation, "body_author_rules", lambda rules, snapshot, body_floor=None: rules)

    def run(page_renders):
        monkeypatch.setattr(renders, "page_body_renders", lambda *a, **k: page_renders)
        snapshot = {**_snapshot(ART_DIRECTION_PROFILE_V9), "svg_template": {"mode": "template-redesign"}}
        snapshot["digest"] = canonical_json_digest({k: v for k, v in snapshot.items() if k != "digest"},
                                                   allow_nan=True)
        result = asyncio.run(module.author_slide_svg(PAGES[1], snapshot, "content", 1, SLIDE_COUNT))
        return wire.bodies[-1], result.receipt

    plain_body, plain_receipt = run([])
    body, receipt = run(entries)
    unsent_body, unsent_receipt = run([{**entry, "png": None} for entry in entries])

    def images(request):
        return [part for message in request["messages"] for part in message["content"]
                if isinstance(part, dict) and part.get("type") == "image"]

    def texts(request):
        return "".join(part.get("text", "") for message in request["messages"] for part in message["content"]
                       if isinstance(part, dict))

    assert len(images(body)) == len(images(plain_body)) + 1
    assert "TEMPLATE BODY RENDERS" in texts(body) and "TEMPLATE BODY RENDERS" not in texts(plain_body)
    assert receipt["template_body_renders"] == {"offered": 2, "sent": 1, "missing": 1, "digests": [entries[0]["sha256"]]}
    assert "template_body_renders" not in plain_receipt
    # Nothing sent: no block and no image, but the receipt says what was missing.
    assert "TEMPLATE BODY RENDERS" not in texts(unsent_body) and len(images(unsent_body)) == len(images(plain_body))
    assert unsent_receipt["template_body_renders"] == {"offered": 2, "sent": 0, "missing": 2, "digests": []}
    # The deck-stable system block is the same with and without renders (the prompt cache holds).
    assert plain_body["system"] == body["system"]


def test_a_frozen_deck_loads_its_renders_wherever_its_material_is_ensured(monkeypatch):
    from services.svg_templates import selection_material

    calls = []

    async def ensure(session, selection):
        calls.append(selection)
        return {}

    async def material(session, selection):
        return None

    monkeypatch.setattr(renders, "ensure_body_renders", ensure)
    monkeypatch.setattr(selection_material, "ensure_selection_material", material)
    selection = {"mode": "template-redesign"}
    asyncio.run(selection_material.ensure_snapshot_material(None, {"svg_template": selection}))
    assert calls == []
    asyncio.run(selection_material.ensure_snapshot_material(None, {"svg_template": selection,
                                                                   "template_body_renders": True}))
    assert calls == [selection]
