"""Masked renders of a template's own body pages: every text becomes a bar, no word survives.

The template-redesign author sees no picture of the template (docs/template-body-renders-design-20261105.md).
A render of a template body page shows how the template fills a page, but the page's wording and
figures belong to the template owner's source document and must never reach a prompt: the model
copies visible text, and a template figure in a new deck is an invented fact. So a render is made
from the deck's PDF with every text object removed and a bar of the text's own colour painted over
the middle of its line, then cropped to the page's body region (the frame furniture with the header,
logos and page number is cut away) and checked:

* the masked page's text layer must hold no character at all (deterministic; else no render);
* a picture larger than an icon inside the body may carry words the text layer does not see (a
  screenshot, a logo with letters). When an OCR runtime is available it reads each such picture and
  any recognised text covers the picture with a flat block of its mean colour; without OCR every such
  picture is covered (fail safe). The receipt records which path ran.

Pure apart from the OCR runtime: no artifact store, no database. Registration stores the result
(``services/svg_templates/body_renders.py``).
"""
from __future__ import annotations

import ctypes
import hashlib
import io
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageStat

LOGGER = logging.getLogger(__name__)

RENDER_VERSION = "svg-template-body-render/v1"
#: A picture at most this many canvas pixels on both edges is an icon: kept without OCR.
ICON_EDGE_PX = 120
#: Stored and sent renders are at most this long on their long edge (about 800 input tokens).
MAX_RENDER_EDGE = 1024
#: A text line's bar covers this middle share of the text object's height.
BAR_SHARE = 0.6
#: OCR reads a picture enlarged to at least this many pixels on its short edge.
_OCR_MIN_EDGE = 240
_HANGUL_RUN = re.compile(r"[가-힣]{2,}")
_LATIN_RUN = re.compile(r"[A-Za-z]{3,}")
_DIGIT_RUN = re.compile(r"[0-9]{2,}")

#: ``(png bytes) -> recognised text``; ``None`` when the runtime failed on that picture.
OcrReader = Callable[[bytes], "str | None"]


class BodyRenderRejected(ValueError):
    """A page whose masking could not be verified gets no render (registration continues)."""


@dataclass(frozen=True)
class MaskedRender:
    png: bytes
    width: int
    height: int
    text_objects: int
    pictures: int
    pictures_covered: int
    ocr: str

    def mask_receipt(self) -> dict:
        return {"version": RENDER_VERSION, "text_objects": self.text_objects, "residual_chars": 0,
                "pictures": self.pictures, "pictures_covered": self.pictures_covered, "ocr": self.ocr}


def recognised_text(text: str | None) -> bool:
    """Whether OCR output names words or figures: two Hangul syllables, three Latin letters or two
    digits in a row. Lower than the icon-pack's word rule on purpose: a false hit only covers a
    picture, a miss would leak a word."""
    text = text or ""
    return bool(_HANGUL_RUN.search(text) or _LATIN_RUN.search(text) or _DIGIT_RUN.search(text))


def _tesseract_reader() -> OcrReader | None:
    """The tesseract runtime the authored OCR checks use, when it is installed with kor+eng."""
    from utils.authored_ocr_quality import AuthoredOcrCache, _ocr_bytes, inspect_tesseract

    runtime = inspect_tesseract(explicit_command=None, tessdata_dir=None, language="kor+eng", timeout_seconds=5.0)
    if runtime.status != "ready":
        return None
    cache = AuthoredOcrCache()

    def read(png: bytes) -> str | None:
        result = _ocr_bytes(png, language="kor+eng", cache=cache, timeout_seconds=20.0, command=None,
                            tessdata_dir=None, psm=11)
        return result.text if result.status == "success" else None
    return read


def _liteparse_reader() -> OcrReader | None:
    """LiteParse's built-in tesseract.js with the bundled ``tessdata/`` (kor+eng), as document parsing uses."""
    from services.liteparse_service import LiteParseError, LiteParseService

    service = LiteParseService(timeout_seconds=120)
    ready, _reason = service.check_runtime_ready()
    if not ready:
        return None

    def read(png: bytes) -> str | None:
        with tempfile.TemporaryDirectory(prefix="svg-template-render-ocr-") as directory:
            path = Path(directory) / "picture.png"
            path.write_bytes(png)
            try:
                return service.parse_to_markdown(str(path), ocr_language="kor+eng")
            except (LiteParseError, OSError, ValueError) as error:
                LOGGER.warning("[svg-template] render OCR failed: %s", type(error).__name__)
                return None
    return read


def _memoized(reader: OcrReader) -> OcrReader:
    """One OCR call per distinct picture: soft shadows and repeated pictures share one image."""
    seen: dict[str, str | None] = {}

    def read(png: bytes) -> str | None:
        key = hashlib.sha256(png).hexdigest()
        if key not in seen:
            seen[key] = reader(png)
        return seen[key]
    return read


def resolve_ocr_reader() -> tuple[OcrReader | None, str]:
    """The product's OCR runtime for pictures: tesseract, else LiteParse, else ``unavailable``."""
    for name, factory in (("tesseract", _tesseract_reader), ("liteparse", _liteparse_reader)):
        try:
            reader = factory()
        except Exception as error:  # noqa: BLE001 - an OCR probe never fails a registration
            LOGGER.warning("[svg-template] %s OCR probe failed: %s", name, type(error).__name__)
            reader = None
        if reader is not None:
            return _memoized(reader), name
    return None, "unavailable"


#: A picture whose in-body pixels have almost no sharp edge (a soft shadow, a gradient, a flat panel)
#: cannot carry legible text: it is neither checked nor covered. Letters, screenshots and drawings
#: are made of sharp edges (measured on template A: soft shadows 0, pictures from 0.01 up).
PLAIN_SHARP_EDGE_SHARE = 0.002
_SHARP_STEP = 48


def _fill(raw, handle) -> tuple[int, int, int, int]:
    channels = [ctypes.c_uint() for _ in range(4)]
    if not raw.FPDFPageObj_GetFillColor(handle, *channels) or channels[3].value == 0:
        raw.FPDFPageObj_GetStrokeColor(handle, *channels)
    return tuple(channel.value for channel in channels)


def _page_bounds(obj) -> tuple[float, float, float, float]:
    """An object's bounds in page space (a nested object's bounds are in its form's space)."""
    bounds = obj.get_bounds()
    container = obj.container
    while container is not None:
        bounds = container.get_matrix().on_rect(*bounds)
        container = container.container
    return bounds


def sharp_edge_share(image: Image.Image) -> float:
    """The share of neighbouring pixel pairs whose grey levels differ by a sharp step."""
    grey = image.convert("L")
    if max(grey.size) > 256:
        ratio = 256 / max(grey.size)
        grey = grey.resize((max(1, round(grey.width * ratio)), max(1, round(grey.height * ratio))), Image.NEAREST)
    width, height = grey.size
    if width < 2 or height < 2:
        return 0.0
    pixels = grey.load()
    sharp = sum((abs(pixels[x + 1, y] - pixels[x, y]) > _SHARP_STEP) + (abs(pixels[x, y + 1] - pixels[x, y]) > _SHARP_STEP)
                for y in range(height - 1) for x in range(width - 1))
    return sharp / (2 * (width - 1) * (height - 1))


def _picture_part(obj, page_box, body_box) -> Image.Image | None:
    """The picture's own pixels that fall inside the body, or ``None`` when they cannot be read."""
    try:
        # Rendered, so a soft mask applies: a flat-colour picture whose alpha draws letters reads as letters.
        bitmap = obj.get_bitmap(render=True)
    except Exception:  # noqa: BLE001 - an unreadable picture is covered, never trusted
        return None
    try:
        raw_image = bitmap.to_pil()
    finally:
        bitmap.close()
    if raw_image.mode in {"RGBA", "LA"} or "transparency" in raw_image.info:
        rgba = raw_image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.getchannel("A"))
    else:
        image = raw_image.convert("RGB")
    left, top, right, bottom = page_box
    if right <= left or bottom <= top:
        return None
    fx = image.width / (right - left)
    fy = image.height / (bottom - top)
    crop = (max(0, round((body_box[0] - left) * fx)), max(0, round((body_box[1] - top) * fy)),
            min(image.width, round((body_box[2] - left) * fx)), min(image.height, round((body_box[3] - top) * fy)))
    if crop[2] - crop[0] < 2 or crop[3] - crop[1] < 2:
        return None
    return image.crop(crop)


def _cover(pdfium, obj, mean) -> None:
    """Replace a picture's pixels with one colour, keeping its place in the drawing order."""
    bitmap = pdfium.PdfBitmap.from_pil(Image.new("RGB", (1, 1), mean))
    try:
        obj.set_bitmap(bitmap)
    finally:
        bitmap.close()


def mask_pdf_page(pdf_bytes: bytes, page_index: int, *, canvas_width_px: int,
                  crop: tuple[int, int, int, int], ocr: OcrReader | None, ocr_name: str,
                  expected_pages: int | None = None) -> MaskedRender:
    """The masked body render of ``page_index`` (0-based) of a deck PDF.

    ``crop`` is ``(x, y, width, height)`` in canvas pixels; the page is rasterised at
    ``canvas_width_px`` across. Raises :class:`BodyRenderRejected` when the page cannot be
    proved free of text.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        if expected_pages is not None and len(document) != expected_pages:
            # A deck with hidden slides exports fewer pages: page numbers would point elsewhere.
            raise BodyRenderRejected("svg_template_body_render_page_count_mismatch")
        if not 0 <= page_index < len(document):
            raise BodyRenderRejected("svg_template_body_render_page_missing")
        page = document[page_index]
        try:
            image, counts = _mask_and_render(pdfium, raw, page, canvas_width_px, crop, ocr)
        finally:
            page.close()
    finally:
        document.close()
    x, y, width, height = crop
    body = image.crop((max(0, x), max(0, y), min(image.width, x + width), min(image.height, y + height)))
    if max(body.size) > MAX_RENDER_EDGE:
        ratio = MAX_RENDER_EDGE / max(body.size)
        body = body.resize((max(1, round(body.width * ratio)), max(1, round(body.height * ratio))), Image.LANCZOS)
    output = io.BytesIO()
    body.save(output, format="PNG", optimize=True)
    return MaskedRender(png=output.getvalue(), width=body.width, height=body.height, text_objects=counts[0],
                        pictures=counts[1], pictures_covered=counts[2],
                        ocr=ocr_name if ocr is not None else "unavailable")


def _mask_and_render(pdfium, raw, page, canvas_width_px, crop, ocr):
    width_pt, height_pt = page.get_width(), page.get_height()
    if width_pt <= 0 or height_pt <= 0:
        raise BodyRenderRejected("svg_template_body_render_page_invalid")
    scale = canvas_width_px / width_pt
    x, y, width, height = crop
    if width < 8 or height < 8 or x < 0 or y < 0 or x + width > canvas_width_px + 1 \
            or y + height > height_pt * scale + 1:
        raise BodyRenderRejected("svg_template_body_render_crop_invalid")
    body_box = (x, y, x + width, y + height)
    objects = list(page.get_objects(max_depth=15))
    pictures = covered = 0
    for obj in objects:
        if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = _page_bounds(obj)
        box = (left * scale, (height_pt - top) * scale, right * scale, (height_pt - bottom) * scale)
        inside = (max(box[0], body_box[0]), max(box[1], body_box[1]),
                  min(box[2], body_box[2]), min(box[3], body_box[3]))
        if inside[2] - inside[0] < 1 or inside[3] - inside[1] < 1:
            continue
        if box[2] - box[0] <= ICON_EDGE_PX and box[3] - box[1] <= ICON_EDGE_PX:
            continue
        part = _picture_part(obj, box, inside)
        if part is not None and sharp_edge_share(part) < PLAIN_SHARP_EDGE_SHARE:
            continue
        pictures += 1
        if part is not None and ocr is not None:
            text = ocr(_ocr_png(part))
            # An OCR call that failed on this picture counts as unread: the picture is covered.
            if text is not None and not recognised_text(text):
                continue
        if part is None:
            page.remove_obj(obj)
            obj.close()
        else:
            _cover(pdfium, obj, tuple(round(value) for value in ImageStat.Stat(part).mean[:3]))
        covered += 1
    texts = 0
    for obj in objects:
        if obj.type != raw.FPDF_PAGEOBJ_TEXT:
            continue
        left, bottom, right, top = _page_bounds(obj)
        if (raw.FPDFTextObj_GetTextRenderMode(obj.raw) != raw.FPDF_TEXTRENDERMODE_INVISIBLE
                and right > left and top > bottom):
            red, green, blue, alpha = _fill(raw, obj.raw)
            line = top - bottom
            bar = raw.FPDFPageObj_CreateNewRect(left, bottom + line * (1 - BAR_SHARE) / 2, right - left,
                                                line * BAR_SHARE)
            raw.FPDFPageObj_SetFillColor(bar, red, green, blue, alpha or 255)
            raw.FPDFPath_SetDrawMode(bar, raw.FPDF_FILLMODE_WINDING, 0)
            raw.FPDFPage_InsertObject(page.raw, bar)
        page.remove_obj(obj)
        obj.close()
        texts += 1
    del objects
    page.gen_content()
    textpage = page.get_textpage()
    try:
        residual = textpage.count_chars()
    finally:
        textpage.close()
    if residual:
        raise BodyRenderRejected("svg_template_body_render_text_survived")
    bitmap = page.render(scale=scale)
    try:
        image = bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()
    return image, (texts, pictures, covered)


def _ocr_png(region: Image.Image) -> bytes:
    short = min(region.size)
    if short < _OCR_MIN_EDGE:
        factor = _OCR_MIN_EDGE / max(1, short)
        region = region.resize((max(1, round(region.width * factor)), max(1, round(region.height * factor))),
                               Image.LANCZOS)
    output = io.BytesIO()
    region.save(output, format="PNG")
    return output.getvalue()


__all__ = ["BodyRenderRejected", "ICON_EDGE_PX", "MAX_RENDER_EDGE", "MaskedRender", "PLAIN_SHARP_EDGE_SHARE",
           "RENDER_VERSION", "mask_pdf_page", "recognised_text", "resolve_ocr_reader", "sharp_edge_share"]
