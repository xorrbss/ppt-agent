"""Ask a vision model for a page's semantic structure, referencing OCR ids only.

The model sees the page image plus numbered evidence: OCR lines ``L1..Ln``
(box, reading, alternative readings) and capture candidates ``C1..Cm``
(panels, detected captures, ruled grids). It returns roles and grouping by id
and optional per-line corrections. It never supplies geometry, and its text
is accepted only through ``raster_text_correction``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from services.source_content.raster_captures import Box, PageContext, _inside
from services.source_content.raster_panels import panel_rectangles
from services.source_content.raster_redesign_ocr import line_text
from services.source_content.raster_tables import detect_ruled_tables

SYSTEM_PROMPT = """You restructure one presentation slide (Korean/English) that exists only as an image.
You receive the slide image, OCR lines (ids L1..) and capture candidates (ids C1..) with pixel boxes [x, y, w, h].
Return JSON only, referencing ids:
- title: line ids of the slide title (the main headline); subtitle: line ids directly under it.
- groups: sections/cards in reading order. heading = heading line ids, body = body line ids in reading order,
  captures = candidate ids of images/screenshots inside the card, icons = line ids that are icon glyphs misread as text.
- tables: real tables only; rows = list of rows, each row a list of cells, each cell a list of line ids.
- screenshots: application screenshots, UI mock-ups, charts or photos. captures = candidate ids forming it,
  lines = every line id whose text is part of that picture (UI labels, form fields, chart labels).
- footer: page footer / page number line ids. decoration: line ids that are pure decoration or noise.
- corrections: only for lines whose OCR reading differs from the image: the exact text as printed
  (you may pick one of the line's alts). Never change any digit or number.
- verified: ids of lines marked "doubtful" whose OCR text is exactly what is printed.
  Every doubtful line outside screenshots (table cells, numbers and titles included) must be either verified
  or corrected; if you cannot read it, omit it.
Use every line id exactly once across title, subtitle, groups, tables, screenshots, footer, decoration.
Brand marks and headers at the top edge are groups (or footer if at the bottom edge)."""

_IDS = {"type": "array", "items": {"type": "string"}}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": _IDS,
        "subtitle": _IDS,
        "groups": {
            "type": "array",
            "items": {"type": "object", "properties": {"heading": _IDS, "body": _IDS, "captures": _IDS, "icons": _IDS}},
        },
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"rows": {"type": "array", "items": {"type": "array", "items": _IDS}}},
            },
        },
        "screenshots": {"type": "array", "items": {"type": "object", "properties": {"captures": _IDS, "lines": _IDS}}},
        "footer": _IDS,
        "decoration": _IDS,
        "corrections": {
            "type": "array",
            "items": {"type": "object", "properties": {"line": {"type": "string"}, "text": {"type": "string"}}},
        },
        "verified": _IDS,
    },
    "required": ["title", "groups", "screenshots"],
}

# (system, parts, schema, name) -> dict | None; parts are text or PNG bytes.
VisionJson = Callable[..., dict | None]


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    box: Box
    kind: str


def _text_card(page: PageContext, box: Box) -> bool:
    """A panel mostly filled with slide-scale text is a card, not a capture."""

    inside = [line for line in page.lines if _inside(line.bbox, box) >= 0.5]
    slide = [line for line in inside if line.height > 1.3 * page.micro_px and not page.is_garbage(line)]
    chars = sum(len(line.word_indices) for line in inside)
    return len(slide) >= 4 and sum(len(line.word_indices) for line in slide) >= 0.5 * max(1, chars)


def capture_candidates(page: PageContext, captures: list[Box], limit: int = 24) -> list[Candidate]:
    area = page.size[0] * page.size[1]
    boxes: list[tuple[Box, str]] = [(box, "capture") for box in captures]
    boxes += [(grid.bbox, "grid") for grid in detect_ruled_tables(page.image, [])]
    boxes += [
        (box, "panel")
        for box in panel_rectangles(page.gray)
        if box[2] * box[3] >= 0.012 * area and not _text_card(page, box)
    ]
    kept: list[tuple[Box, str]] = []
    for box, kind in boxes:
        if not any(_inside(box, other) >= 0.9 and _inside(other, box) >= 0.9 for other, _ in kept):
            kept.append((box, kind))
    return [Candidate(f"C{n}", box, kind) for n, (box, kind) in enumerate(kept[:limit], 1)]


def evidence_payload(
    page: PageContext, candidates: list[Candidate], doubtful: Callable[[Any], bool] | None = None
) -> dict[str, Any]:
    lines = []
    for n, line in enumerate(page.lines, 1):
        text = line_text(line, page.words)
        entry: dict[str, Any] = {"id": f"L{n}", "box": list(line.bbox), "h": round(line.height), "text": text}
        alternates = [alt for alt in dict.fromkeys(line.alternates) if alt != text]
        if alternates:
            entry["alts"] = alternates[:4]
        if doubtful is not None and doubtful(line):
            entry["doubtful"] = True
        lines.append(entry)
    return {
        "image_size": list(page.size),
        "lines": lines,
        "candidates": [{"id": c.candidate_id, "box": list(c.box), "kind": c.kind} for c in candidates],
    }


def request_structure(
    page: PageContext, candidates: list[Candidate], client: VisionJson, doubtful: Callable[[Any], bool] | None = None
) -> dict | None:
    output = io.BytesIO()
    page.image.convert("RGB").save(output, format="PNG")
    payload = json.dumps(evidence_payload(page, candidates, doubtful), ensure_ascii=False)
    return client(SYSTEM_PROMPT, [output.getvalue(), payload], SCHEMA, "slide_structure")
