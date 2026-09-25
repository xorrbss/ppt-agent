"""Independent model transcription of every text line of a page.

Korean OCR misreads are often valid syllables ("어떤" read as "이떤"), so
confidence cannot decide what is trusted. A second, independent reader is
asked to transcribe each line from its pixels alone: the model receives
labelled crops of the lines (never the OCR text), so its reading cannot
copy the OCR. A line is published as editable text only when both readers
agree (see ``raster_text_correction.transcription_agrees``).
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw

from services.source_content.raster_captures import PageContext

SYSTEM_PROMPT = """You transcribe text from cropped lines of a presentation slide (Korean/English).
Each crop is labelled with its id (L1, L2, ...) at the left margin. For EVERY id, return the exact text
printed in that crop: same characters, digits, punctuation and spacing as printed; do not translate,
complete, fix or summarise. A crop may show a partial word at its edge: transcribe only what is fully visible.
If a crop holds no readable text (icon, graphic), return an empty string for it."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}}},
        }
    },
    "required": ["lines"],
}

_MARGIN = 64
_GAP = 10
_SHEET_HEIGHT = 1800
_MIN_HEIGHT = 28


def _excluded(raw: dict | None) -> set[str]:
    """Line ids the structure placed in screenshots or decoration: pixels anyway."""

    if not raw:
        return set()
    ids = set(raw.get("decoration") or ())
    for shot in raw.get("screenshots") or ():
        ids.update(shot.get("lines") or ())
    for group in raw.get("groups") or ():
        ids.update(group.get("icons") or ())
    return {str(i) for i in ids}


def _sheets(page: PageContext, indices: list[int]) -> list[bytes]:
    """Labelled line crops stacked into sheets no taller than ``_SHEET_HEIGHT``."""

    crops = []
    for n in indices:
        x, y, w, h = page.lines[n].bbox
        pad = max(3, int(0.15 * h))
        crop = page.image.convert("RGB").crop(
            (max(0, x - pad), max(0, y - pad), min(page.size[0], x + w + pad), min(page.size[1], y + h + pad))
        )
        if crop.height < _MIN_HEIGHT:
            factor = _MIN_HEIGHT / max(1, crop.height)
            crop = crop.resize((round(crop.width * factor), _MIN_HEIGHT), Image.Resampling.LANCZOS)
        crops.append((f"L{n + 1}", crop))
    sheets, batch, height = [], [], 0
    for item in crops:
        if batch and height + item[1].height + _GAP > _SHEET_HEIGHT:
            sheets.append(_render(batch))
            batch, height = [], 0
        batch.append(item)
        height += item[1].height + _GAP
    if batch:
        sheets.append(_render(batch))
    return sheets


def _render(batch: list[tuple[str, Image.Image]]) -> bytes:
    width = _MARGIN + max(crop.width for _, crop in batch) + _GAP
    height = sum(crop.height + _GAP for _, crop in batch) + _GAP
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    y = _GAP
    for label, crop in batch:
        draw.text((4, y + max(0, crop.height // 2 - 6)), label, fill=(200, 0, 0))
        sheet.paste(crop, (_MARGIN, y))
        draw.rectangle((_MARGIN - 1, y - 1, _MARGIN + crop.width, y + crop.height), outline=(160, 160, 160))
        y += crop.height + _GAP
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


def request_transcripts(page: PageContext, raw: dict | None, client) -> dict[int, str]:
    """Line index -> the model's independent transcription (missing: not transcribed)."""

    skip = _excluded(raw)
    indices = [n for n in range(len(page.lines)) if f"L{n + 1}" not in skip]
    if not indices:
        return {}
    sheets = _sheets(page, indices)
    note = f"Transcribe these ids: {', '.join(f'L{n + 1}' for n in indices)}"
    result = client(SYSTEM_PROMPT, [*sheets, note], SCHEMA, "line_transcripts")
    wanted = {f"L{n + 1}": n for n in indices}
    transcripts: dict[int, str] = {}
    for entry in (result or {}).get("lines") or ():
        if isinstance(entry, dict) and str(entry.get("id")) in wanted and isinstance(entry.get("text"), str):
            transcripts[wanted[str(entry["id"])]] = " ".join(entry["text"].split())
    return transcripts
