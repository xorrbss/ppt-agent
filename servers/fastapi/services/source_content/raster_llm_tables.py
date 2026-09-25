"""Second, table-specific vision call: cell structure by OCR line ids.

For each table candidate (a ruled grid the detector found, or a table the
structure call named) the model sees the table crop and the OCR lines inside
it, and returns rows x columns of line ids, plus verified and corrected
readings for those lines. Cells are grounded later by the same rules as all
other text.
"""

from __future__ import annotations

import io
import json
from typing import Any

from services.source_content.raster_captures import Box, PageContext, _center_in, _inside, _union
from services.source_content.raster_llm_structure import VisionJson
from services.source_content.raster_redesign_ocr import line_text
from services.source_content.raster_tables import detect_ruled_tables

TABLE_PROMPT = """You transcribe the structure of one table from a presentation slide (Korean/English).
You get the table image and the OCR lines inside it (ids, boxes relative to the image, text, alternative reads).
Return rows from top to bottom; each row is a list of cells from left to right; each cell is a list of line ids
(several lines may form one cell; an empty cell is []). Every line id must be used at most once.
If this is not a real table (for example a set of cards), return "rows": [].
verified: ids of lines whose OCR text is exactly what is printed. corrections: exact printed text for lines
whose OCR text is wrong (never change a digit)."""

_IDS = {"type": "array", "items": {"type": "string"}}
TABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {"type": "array", "items": _IDS}},
        "verified": _IDS,
        "corrections": {
            "type": "array",
            "items": {"type": "object", "properties": {"line": {"type": "string"}, "text": {"type": "string"}}},
        },
    },
    "required": ["rows"],
}


def table_candidates(page: PageContext, raw: dict) -> list[tuple[Box, list[int]]]:
    """Model-named tables and detected ruled grids, as (box, line indices)."""

    count = len(page.lines)
    found: list[tuple[Box, list[int]]] = []
    for entry in raw.get("tables") or ():
        ids = [
            int(str(v)[1:]) - 1
            for row in entry.get("rows") or ()
            for cell in row
            for v in cell
            if str(v).startswith("L") and str(v)[1:].isdigit() and 1 <= int(str(v)[1:]) <= count
        ]
        if ids:
            found.append((_union([page.lines[i].bbox for i in ids]), sorted(set(ids))))
    for grid in detect_ruled_tables(page.image, []):
        if any(_inside(grid.bbox, box) >= 0.5 or _inside(box, grid.bbox) >= 0.5 for box, _ in found):
            continue
        inside = [i for i, line in enumerate(page.lines) if _center_in(line.bbox, grid.bbox)]
        if len(inside) >= 4:
            found.append((grid.bbox, inside))
    return found


def request_table(page: PageContext, box: Box, indices: list[int], client: VisionJson) -> dict | None:
    x, y, width, height = box
    pad = 6
    right, bottom = min(page.size[0], x + width + pad), min(page.size[1], y + height + pad)
    crop_box = (max(0, x - pad), max(0, y - pad), right, bottom)
    output = io.BytesIO()
    page.image.convert("RGB").crop(crop_box).save(output, format="PNG")
    lines = []
    for i in indices:
        line = page.lines[i]
        text = line_text(line, page.words)
        lx, ly, lw, lh = line.bbox
        entry: dict[str, Any] = {"id": f"L{i + 1}", "box": [lx - crop_box[0], ly - crop_box[1], lw, lh], "text": text}
        alternates = [alt for alt in dict.fromkeys(line.alternates) if alt != text]
        if alternates:
            entry["alts"] = alternates[:4]
        lines.append(entry)
    payload = json.dumps({"lines": lines}, ensure_ascii=False)
    return client(TABLE_PROMPT, [output.getvalue(), payload], TABLE_SCHEMA, "table_structure")


def restructure_tables(page: PageContext, raw: dict, client: VisionJson) -> dict:
    """Replace the structure's tables by per-table calls; failures keep the original entry."""

    tables, verified, corrections = [], list(raw.get("verified") or ()), list(raw.get("corrections") or ())
    for box, indices in table_candidates(page, raw):
        try:
            answer = request_table(page, box, indices, client)
        except Exception:
            answer = None
        if answer and answer.get("rows"):
            tables.append({"rows": answer["rows"]})
            verified.extend(answer.get("verified") or ())
            corrections.extend(answer.get("corrections") or ())
    if not tables:
        return raw
    return {**raw, "tables": tables, "verified": verified, "corrections": corrections}
