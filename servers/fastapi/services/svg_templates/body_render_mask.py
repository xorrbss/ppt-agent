"""Masked renders of a template's own body pages: every text becomes a bar, no word survives.

The template-redesign author sees no picture of the template (docs/template-body-renders-design-20261105.md).
A render of a template body page shows how the template fills a page, but the page's wording and
figures belong to the template owner's source document and must never reach a prompt: the model
copies visible text, and a template figure in a new deck is an invented fact. So a render is made
from the deck's PDF with every text object removed and a bar of the text's own colour painted over
the middle of its line, then cropped to the page's body region (the frame furniture with the header,
logos and page number is cut away) and checked:

* the masked page's text layer must hold no character at all (deterministic; else no render);
* annotations and form fields are not drawn;
* every picture inside the body, icons included, may carry words the text layer does not see (a
  screenshot, a logo with letters). When an OCR runtime is available it reads each picture and any
  recognised text covers the picture with one colour in place; without OCR every picture with sharp
  edges is covered (fail safe), and only a flat one (a soft shadow, a gradient) is kept;
* text drawn as paths (WordArt, outlined text, an embedded metafile) is no text object: when an OCR
  runtime is available the final masked crop is read once more, and a page on which OCR still finds
  a word (three Hangul syllables or five Latin letters in a row; calibrated on the real template's
  ten renders, whose bar noise reached two and four) gets no render. The receipt records both paths.

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

#: Bounds for the OCR used to mask template body renders.
_TESSERACT_PROBE_TIMEOUT_SECONDS = 5.0
_PAGE_OCR_TIMEOUT_SECONDS = 20.0
_LITEPARSE_TIMEOUT_SECONDS = 120

LOGGER = logging.getLogger(__name__)

RENDER_VERSION = "svg-template-body-render/v1"
#: A picture with fewer strong-edge pixels than this (a soft shadow, a gradient) cannot carry a
#: legible letter: without OCR it is kept, every other picture is covered.
MIN_EDGE_PIXELS = 16
_EDGE_STEP = 48
_FINAL_HANGUL = re.compile(r"[가-힣]{3,}")
_FINAL_LATIN = re.compile(r"[A-Za-z]{5,}")
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
    #: Pictures removed as a shadow or a text effect (bitmaps that may hold a blurred copy of a text).
    text_effects: int = 0
    #: ``passed``: OCR read the final crop and found no word; ``unavailable``: no OCR runtime.
    final_check: str = "unavailable"

    def mask_receipt(self) -> dict:
        return {"version": RENDER_VERSION, "text_objects": self.text_objects, "residual_chars": 0,
                "pictures": self.pictures, "pictures_covered": self.pictures_covered,
                "text_effects": self.text_effects, "ocr": self.ocr, "final_check": self.final_check}


def recognised_text(text: str | None) -> bool:
    """Whether OCR output names words or figures: two Hangul syllables, three Latin letters or two
    digits in a row. Lower than the icon-pack's word rule on purpose: a false hit only covers a
    picture, a miss would leak a word."""
    text = text or ""
    return bool(_HANGUL_RUN.search(text) or _LATIN_RUN.search(text) or _DIGIT_RUN.search(text))


def final_text_found(text: str | None) -> bool:
    """Whether OCR of a whole masked crop found a word: three Hangul syllables or five Latin letters
    in a row. Bars and pictograms read as noise of up to two syllables and four letters on the real
    template's renders; digits are left out, since bars read as long digit runs."""
    text = text or ""
    return bool(_FINAL_HANGUL.search(text) or _FINAL_LATIN.search(text))


def strong_edge_pixels(image: Image.Image) -> int:
    """Pixels on a strong grey-level edge, at full resolution (a letter's stroke keeps its edges)."""
    from PIL import ImageFilter

    grey = image.convert("L")
    if grey.width < 3 or grey.height < 3:
        return 0
    edges = grey.filter(ImageFilter.FIND_EDGES).crop((1, 1, grey.width - 1, grey.height - 1))
    return sum(edges.histogram()[_EDGE_STEP + 1:])


def _uniform(image: Image.Image) -> bool:
    return all(high - low <= 2 for low, high in image.getextrema())


def _tesseract_reader() -> OcrReader | None:
    """The tesseract runtime the authored OCR checks use, when it is installed with kor+eng."""
    from utils.authored_ocr_quality import AuthoredOcrCache, _ocr_bytes, inspect_tesseract

    runtime = inspect_tesseract(explicit_command=None, tessdata_dir=None, language="kor+eng", timeout_seconds=_TESSERACT_PROBE_TIMEOUT_SECONDS)
    if runtime.status != "ready":
        return None
    cache = AuthoredOcrCache()

    def read(png: bytes) -> str | None:
        result = _ocr_bytes(png, language="kor+eng", cache=cache, timeout_seconds=_PAGE_OCR_TIMEOUT_SECONDS, command=None,
                            tessdata_dir=None, psm=11)
        return result.text if result.status == "success" else None
    return read


def _liteparse_reader() -> OcrReader | None:
    """LiteParse's built-in tesseract.js with the bundled ``tessdata/`` (kor+eng), as document parsing uses."""
    from services.liteparse_service import LiteParseError, LiteParseService

    service = LiteParseService(timeout_seconds=_LITEPARSE_TIMEOUT_SECONDS)
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


def _soft_shadow(obj) -> bool:
    """A translucent grey picture: the PDF export's drop shadow of a shape (with a blurred copy of the
    shape's text in it, readable once the text itself is gone). Measured on the real template: every
    card and label shadow is a soft-masked grey bitmap."""
    try:
        bitmap = obj.get_bitmap(render=True)
    except Exception:  # noqa: BLE001 - not a readable shadow
        return False
    try:
        image = bitmap.to_pil()
    finally:
        bitmap.close()
    if image.mode not in {"RGBA", "LA"}:
        return False
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getextrema()[0] >= 250:
        return False  # opaque: a picture, not a shadow
    red, green, blue = (channel for channel in rgba.convert("RGB").split())
    from PIL import ImageChops

    spread = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(red, green), blue),
                                 ImageChops.darker(ImageChops.darker(red, green), blue))
    return spread.getextrema()[1] <= 16


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
            image, check, counts = _mask_and_render(pdfium, raw, page, canvas_width_px, crop, ocr)
        finally:
            page.close()
    finally:
        document.close()
    x, y, width, height = crop
    box = (max(0, x), max(0, y), min(image.width, x + width), min(image.height, y + height))
    body = image.crop(box)
    final_check = "unavailable"
    if check is not None:
        # Text that was never a text object (paths, metafiles, clip text) is caught here.
        crop_png = io.BytesIO()
        check.crop(box).save(crop_png, format="PNG")
        text = ocr(crop_png.getvalue())
        if text is None:
            raise BodyRenderRejected("svg_template_body_render_final_ocr_failed")
        if final_text_found(text):
            raise BodyRenderRejected("svg_template_body_render_final_ocr_text")
        final_check = "passed"
    if max(body.size) > MAX_RENDER_EDGE:
        ratio = MAX_RENDER_EDGE / max(body.size)
        body = body.resize((max(1, round(body.width * ratio)), max(1, round(body.height * ratio))), Image.LANCZOS)
    output = io.BytesIO()
    body.save(output, format="PNG", optimize=True)
    return MaskedRender(png=output.getvalue(), width=body.width, height=body.height, text_objects=counts[0],
                        pictures=counts[1], pictures_covered=counts[2], text_effects=counts[3],
                        ocr=ocr_name if ocr is not None else "unavailable", final_check=final_check)


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
    text_boxes = [_page_bounds(obj) for obj in objects if obj.type == raw.FPDF_PAGEOBJ_TEXT]
    pictures = covered = effects = 0
    for obj in objects:
        if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = _page_bounds(obj)
        if _text_effect((left, bottom, right, top), text_boxes) or _soft_shadow(obj):
            # A text's shadow or glow, or a shape's drop shadow, exported as a bitmap that may hold a
            # blurred copy of the text: removed with the text.
            page.remove_obj(obj)
            obj.close()
            effects += 1
            continue
        box = (left * scale, (height_pt - top) * scale, right * scale, (height_pt - bottom) * scale)
        inside = (max(box[0], body_box[0]), max(box[1], body_box[1]),
                  min(box[2], body_box[2]), min(box[3], body_box[3]))
        if inside[2] - inside[0] < 1 or inside[3] - inside[1] < 1:
            continue
        part = _picture_part(obj, box, inside)
        if part is not None and _uniform(part):
            continue  # one colour: nothing to read
        pictures += 1
        if part is not None and ocr is not None:
            text = ocr(_ocr_png(part))
            # An OCR call that failed on this picture counts as unread: the picture is covered.
            if text is not None and not recognised_text(text):
                continue
        elif part is not None and strong_edge_pixels(part) < MIN_EDGE_PIXELS:
            continue  # no OCR, but no edge a letter could be made of
        if part is None:
            page.remove_obj(obj)
            obj.close()
        else:
            _cover(pdfium, obj, tuple(round(value) for value in ImageStat.Stat(part).mean[:3]))
        covered += 1
    texts, bars = 0, []
    for obj in objects:
        if obj.type != raw.FPDF_PAGEOBJ_TEXT:
            continue
        left, bottom, right, top = _page_bounds(obj)
        if (raw.FPDFTextObj_GetTextRenderMode(obj.raw) != raw.FPDF_TEXTRENDERMODE_INVISIBLE
                and right > left and top > bottom):
            bars.append((left, bottom, right, top, _fill(raw, obj.raw)))
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
    # The page without its text objects and before any bar: what OCR may still read here was never a
    # text object (paths, metafiles, clip text). Bars would only add noise to that reading.
    check = _render(page, scale) if ocr is not None else None
    for left, bottom, right, top, (red, green, blue, alpha) in bars:
        line = top - bottom
        bar = raw.FPDFPageObj_CreateNewRect(left, bottom + line * (1 - BAR_SHARE) / 2, right - left,
                                            line * BAR_SHARE)
        raw.FPDFPageObj_SetFillColor(bar, red, green, blue, alpha or 255)
        raw.FPDFPath_SetDrawMode(bar, raw.FPDF_FILLMODE_WINDING, 0)
        raw.FPDFPage_InsertObject(page.raw, bar)
    page.gen_content()
    return _render(page, scale), check, (texts, pictures, covered, effects)


def _text_effect(box, text_boxes) -> bool:
    """Whether a picture lies within the text it overlaps, grown by most of a line: a shadow, glow or
    reflection the PDF export drew as a bitmap of that text (measured on the real template: blurred
    copies of the lines a few points off them, readable once the text itself is gone)."""
    left, bottom, right, top = box
    touching = [text for text in text_boxes
                if text[0] < right and text[2] > left and text[1] < top and text[3] > bottom]
    if not touching:
        return False
    lines = sorted(text[3] - text[1] for text in touching)
    margin = max(4.0, 0.75 * lines[len(lines) // 2])
    return (left >= min(text[0] for text in touching) - margin and right <= max(text[2] for text in touching) + margin
            and bottom >= min(text[1] for text in touching) - margin and top <= max(text[3] for text in touching) + margin)


def _render(page, scale) -> Image.Image:
    # Annotations (stamps, free text) and form fields carry text of their own: never drawn.
    bitmap = page.render(scale=scale, draw_annots=False, may_draw_forms=False)
    try:
        return bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()


def _ocr_png(region: Image.Image) -> bytes:
    short = min(region.size)
    if short < _OCR_MIN_EDGE:
        factor = _OCR_MIN_EDGE / max(1, short)
        region = region.resize((max(1, round(region.width * factor)), max(1, round(region.height * factor))),
                               Image.LANCZOS)
    output = io.BytesIO()
    region.save(output, format="PNG")
    return output.getvalue()


__all__ = ["BodyRenderRejected", "MAX_RENDER_EDGE", "MIN_EDGE_PIXELS", "MaskedRender", "RENDER_VERSION",
           "final_text_found", "mask_pdf_page", "recognised_text", "resolve_ocr_reader", "strong_edge_pixels"]
