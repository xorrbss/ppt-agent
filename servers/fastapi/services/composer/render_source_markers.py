"""Source list markers kept separate from the immutable bound paragraph text.

Symbol-font markers are validated against the installed original font and
materialized as PNG glyphs. They remain native image assets through publication;
ordinary Unicode and numbered markers remain independently identified text.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path

from services.composer.render_css import px
from services.composer.render_text import TextRenderError, escape_attr, escape_text


@lru_cache(maxsize=128)
def _symbol_png(family: str, glyph: str, color: str) -> tuple[str, float, float]:
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
    from PIL import Image, ImageDraw, ImageFont

    # This is the single source symbol face observed in the supported input,
    # not a fallback font search: an unavailable face must be reported.
    names = {"wingdings": "wingding.ttf"}
    filename = names.get(family.casefold())
    if not filename or len(glyph) != 1:
        raise TextRenderError("source bullet symbol font unsupported")
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
    if not font_path.is_file():
        raise TextRenderError("source bullet font unavailable: Wingdings")
    with TTFont(font_path, lazy=True) as font:
        code = None
        glyph_name = None
        for candidate in (ord(glyph), 0xF000 + ord(glyph)):
            for table in font["cmap"].tables:
                if candidate in table.cmap and table.cmap[candidate] != ".notdef":
                    code, glyph_name = candidate, table.cmap[candidate]
                    break
            if code is not None:
                break
        # Pillow/FreeType do not consistently expose legacy MS symbol charmaps.
        # Build an in-memory Unicode alias to the verified original glyph; the
        # outlines stay untouched and only the rendered PNG leaves this helper.
        font_bytes = BytesIO()
        if glyph_name is not None:
            mapping = CmapSubtable.newSubtable(4)
            mapping.platformID, mapping.platEncID, mapping.language = 3, 1, 0
            mapping.cmap = {ord(glyph): glyph_name}
            font["cmap"].tables = [mapping]
            font.save(font_bytes)
            font_bytes.seek(0)
    if code is None:
        raise TextRenderError("source bullet font glyph unavailable")
    resolution = 192
    face = ImageFont.truetype(font_bytes, resolution)
    text = glyph
    left, top, right, bottom = face.getbbox(text)
    if right <= left or bottom <= top:
        raise TextRenderError("source bullet glyph has empty geometry")
    picture = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    ImageDraw.Draw(picture).text((-left, -top), text, font=face, fill=color)
    if picture.getbbox() is None:
        raise TextRenderError("source bullet glyph raster unavailable")
    buffer = BytesIO()
    picture.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"), picture.width / resolution, picture.height / resolution


def _number_glyph(kind: str, number: int) -> str:
    if kind.startswith("arabic"):
        value = str(number)
    elif kind.startswith("alphaLc") or kind.startswith("alphaUc"):
        value = ""
        while number:
            number, digit = divmod(number - 1, 26)
            value = chr(97 + digit) + value
        if kind.startswith("alphaUc"):
            value = value.upper()
    else:
        raise TextRenderError("source bullet numbering type unsupported")
    if kind.endswith("Period"):
        return value + "."
    if kind.endswith("ParenR"):
        return value + ")"
    if kind.endswith("ParenBoth"):
        return "(" + value + ")"
    if kind.endswith("Plain"):
        return value
    raise TextRenderError("source bullet numbering punctuation unsupported")


def render_source_marker(paragraph, ctx, *, font_px: float, counters: dict[int, int]) -> str | None:
    if not paragraph.bullet or not paragraph.text.strip():
        counters.clear()
        return None
    layout = paragraph.layout
    kind = paragraph.bullet_kind
    qid = ctx.qids.claim(paragraph.source.binding_id + "-marker")
    family = str(layout.get("bulletFontFamily") or "")
    color = next((run.color for run in paragraph.runs if run.color), ctx.tokens.text_primary)
    glyph = str(layout.get("bulletChar") or "")
    if kind == "auto-number":
        start = layout.get("bulletStartAt")
        number = int(start) if start is not None else counters.get(paragraph.level, 0) + 1
        counters[paragraph.level] = number
        glyph = _number_glyph(str(layout.get("bulletAutoNumType") or "arabicPeriod"), number)
    data_uri = layout.get("bulletImageDataUri")
    width, height = 1.0, 1.0
    if kind == "picture":
        if not isinstance(data_uri, str) or not data_uri.startswith("data:image/png;base64,"):
            raise TextRenderError("source picture bullet resource unavailable")
    elif family.casefold() == "wingdings":
        data_uri, width, height = _symbol_png(family, glyph, color)
    elif not glyph:
        raise TextRenderError("source bullet character unavailable")
    if data_uri:
        return (f'<img class="cx-source-marker" data-qid="{qid}" data-semantic-role="image" '
                f'src="{escape_attr(data_uri)}" style="width:{px(font_px * width)};height:{px(font_px * height)};object-fit:contain">')
    return (f'<span class="cx-source-marker" data-qid="{qid}" data-semantic-role="supporting" '
            f'style="font-size:{px(font_px)};font-weight:400;color:{escape_attr(color)}">{escape_text(glyph)}</span>')
