"""Emit planned regions from a grounded slide structure (see raster_structure_apply)."""

from __future__ import annotations

import re

from dataclasses import dataclass, field

from services.source_content.raster_captures import Box, PageContext, _pad, _union
from services.source_content.raster_paragraphs import paragraphs, pixel_paragraph
from services.source_content.raster_page_plan import PlannedRegion
from services.source_content.raster_redesign_ocr import RedesignWord, line_text


@dataclass
class Build:
    page: PageContext
    words: list[RedesignWord]
    model: str
    unpromoted: set[int]
    corrections: list[str] = field(default_factory=list)
    regions: list[PlannedRegion] = field(default_factory=list)
    texts: dict[int, str | None] = field(default_factory=dict)  # line -> text, None = pixels
    tokens: dict[int, tuple[int, ...]] = field(default_factory=dict)
    readings: dict[int, str] = field(default_factory=dict)  # line -> OCR text of its carried tokens
    targeted: dict[int, tuple[str, ...]] = field(default_factory=dict)  # line -> targeted number reads
    hangul: dict[int, dict[int, str]] = field(default_factory=dict)  # line -> Korean re-reads of digit words
    transcripts: dict[int, str] = field(default_factory=dict)  # line -> independent model transcription


def _same_row_spread(build: Build, members: list[int]) -> None:
    """A text line touching a pixel line on the same row becomes pixels too."""

    lines = build.page.lines
    changed = True
    while changed:
        changed = False
        for a in members:
            if build.texts.get(a) is not None:
                continue
            for b in members:
                if build.texts.get(b) is None:
                    continue
                la, lb = lines[a].bbox, lines[b].bbox
                tall = max(lines[a].height, lines[b].height, 1.0)
                centers = abs((la[1] + la[3] / 2) - (lb[1] + lb[3] / 2))
                gap = max(la[0], lb[0]) - min(la[0] + la[2], lb[0] + lb[2])
                if centers <= 0.6 * tall and gap < tall:
                    build.texts[b] = None
                    changed = True


_MARKER = re.compile(r"^(\d{1,2}[.)]?|[①-⑳])$")


def _marker(texts, i: int) -> bool:
    return bool(_MARKER.match("".join((texts.get(i) or "").split())))


def _same_row(a, b, reach: float = 0.8) -> bool:
    low = min(a[3], b[3])
    overlap = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return overlap >= 0.5 * low and 0 <= b[0] - (a[0] + a[2]) < reach * low


def _rows(page: PageContext, run: list[int], texts) -> list[list[int]]:
    """Consecutive lines continuing one printed row (OCR split "...겠습니" + "다.")
    form one text line, so a sentence is never broken; a word gap is the limit, so
    table cells on one row stay separate lines. A list marker ("1") joins the item
    text beside it."""

    rows: list[list[int]] = []
    for i in run:
        last = rows[-1][-1] if rows else None
        reach = 4.0 if last is not None and len(rows[-1]) == 1 and _marker(texts, last) else 0.8
        if last is not None and _same_row(page.lines[last].bbox, page.lines[i].bbox, reach):
            rows[-1].append(i)
        else:
            rows.append([i])
    return rows


def _items(rows: list[list[int]], texts) -> list[list[list[int]]]:
    """Rows split into list items: a row starting with a marker starts a new item."""

    items: list[list[list[int]]] = []
    for row in rows:
        if not items or _marker(texts, row[0]):
            items.append([row])
        else:
            items[-1].append(row)
    return items


def _joined(page: PageContext, row: list[int], texts) -> str:
    parts = [texts[row[0]] or ""]
    for previous, i in zip(row, row[1:], strict=False):
        a, b = page.lines[previous].bbox, page.lines[i].bbox
        gap = b[0] - (a[0] + a[2])
        parts.append(("" if gap < 0.3 * min(a[3], b[3]) else " ") + (texts[i] or ""))
    return "".join(parts)


def _grid_lines(build: Build, members: list[int]) -> list[int]:
    """Lines forming a data grid: at least two rows of three or more short cells,
    half of them numeric. Empty when the members are ordinary text."""

    page = build.page
    lines = [i for i in members if build.tokens.get(i)]
    rows: list[list[int]] = []
    for i in sorted(lines, key=lambda n: page.lines[n].bbox[1] + page.lines[n].bbox[3] / 2):
        box = page.lines[i].bbox
        for row in rows:
            anchor = page.lines[row[0]].bbox
            overlap = min(anchor[1] + anchor[3], box[1] + box[3]) - max(anchor[1], box[1])
            if overlap >= 0.5 * min(anchor[3], box[3]):
                row.append(i)
                break
        else:
            rows.append([i])
    grid = [row for row in rows if len(row) >= 3]
    cells = [i for row in grid for i in row]
    texts = ["".join(line_text(page.lines[i], page.words).split()) for i in cells]
    numeric = sum(any(c.isdigit() for c in t) for t in texts)
    if len(grid) < 2 or any(len(t) > 12 for t in texts) or numeric * 2 < len(texts):
        return []
    return [i for i in members if i in set(cells)]


def _text_region(build: Build, role: str, members: list[int], group: str | None, headings: int = 0) -> None:
    page = build.page
    text_lines = [i for i in members if build.texts.get(i) is not None and build.tokens.get(i)]
    grid = _grid_lines(build, members)
    if grid:
        # A data grid the structure did not return as a table: one legible image
        # table, never cells flattened into text lines.
        tokens = tuple(t for i in grid for t in build.tokens.get(i, ()))
        build.regions.append(
            PlannedRegion(
                "table",
                _pad(_union([page.lines[i].bbox for i in grid]), 3, page.size),
                tokens,
                "\n".join(line_text(page.lines[i], page.words) for i in grid),
                confidence=0.5,
                review_reason="table_unstructured",
                reason="grid_lines",
                editable_fallback=True,
                group_id=group,
            )
        )
        members = [i for i in members if i not in grid]
        text_lines = [i for i in text_lines if i not in grid]
    for paragraph in paragraphs(build, members, headings, _marker):
        if any(build.texts.get(i) is None for i in paragraph):
            build.regions.append(pixel_paragraph(build, paragraph, group))  # atomic: one crop
            continue
        for rows in _items(_rows(page, paragraph, build.texts), build.texts):
            run = [i for row in rows for i in row]
            tokens = tuple(t for i in run for t in build.tokens[i])
            heading_count = sum(1 for row in rows if row[0] in members[:headings])
            corrected = any(build.words[t].engine.startswith("llm-structure:") for t in tokens)
            build.regions.append(
                PlannedRegion(
                    role,
                    _union([page.lines[i].bbox for i in run]),
                    tokens,
                    "\n".join(_joined(page, row, build.texts) for row in rows),
                    confidence=0.9,
                    review_reason=f"llm_corrected:{build.model}" if corrected else None,  # advisory audit trail
                    reason=f"llm_structure:{build.model}",
                    group_id=group,
                    heading_line_count=heading_count,
                )
            )


def _screenshot(build: Build, box: Box, members: list[int], group: str | None) -> None:
    tokens = tuple(t for i in members for t in build.tokens.get(i, ()))
    build.regions.append(
        PlannedRegion("screenshot", box, tokens, reason=f"llm_structure:{build.model}", group_id=group)
    )


def _table(build: Build, rows: list[list[list[int]]], group: str | None) -> None:
    page = build.page
    members = [i for row in rows for cell in row for i in cell]
    if not members:
        return
    width = max(len(row) for row in rows)
    box = _pad(_union([page.lines[i].bbox for i in members]), 4, page.size)
    tokens = tuple(t for i in members for t in build.tokens.get(i, ()))
    cells = tuple(
        tuple("\n".join(build.texts[i] or "" for i in cell) for cell in row) + ("",) * (width - len(row))
        for row in rows
    )
    ungrounded = [i for i in members if build.texts.get(i) is None]
    # Native when at least 90% of cell lines are grounded; the rest keep their OCR
    # reading with an advisory. Below that the table is kept as source pixels.
    # An unconfirmed number never goes into an editable cell: the table stays pixels.
    unconfirmed_number = any(any(ch.isdigit() for ch in build.readings.get(i, "")) for i in ungrounded)
    native = len(ungrounded) <= 0.1 * len(members) and not unconfirmed_number
    if native and ungrounded:
        cells = tuple(
            tuple("\n".join(build.texts.get(i) or build.readings.get(i, "") for i in cell) for cell in row)
            + ("",) * (width - len(row))
            for row in rows
        )
    partial = "table_cells_partially_unverified" if native else "table_cell_ocr_low_confidence"
    reason = partial if ungrounded else None
    build.regions.append(
        PlannedRegion(
            "table",
            box,
            tokens,
            None if native else "\n".join("\t".join(row) for row in cells),
            table_cells=cells if native else None,
            confidence=0.9,
            review_reason=reason,
            reason=f"llm_structure:{build.model}",
            editable_fallback=not native,
            group_id=group,
        )
    )


def is_noise(page: PageContext, index: int) -> bool:
    """An icon misread as glyphs: no digit and at most two letters, or a short
    low-confidence Latin run with no Hangul (e.g. "oll", "FAY" over an icon)."""

    line = page.lines[index]
    value = "".join(line_text(line, page.words).split())
    if any(ch.isdigit() for ch in value):
        return False
    letters = sum(ch.isalnum() for ch in value)
    hangul = any("\uac00" <= ch <= "\ud7a3" for ch in value)
    return letters <= 2 or (not hangul and letters <= 5 and page.conf(line.word_indices) < 0.8)


def emit(build: Build, title, subtitle, groups, tables, shots, footer, leftovers, noise) -> None:
    page = build.page
    shot_lines = {i for _, lines, _ in shots for i in lines}

    def text_lines(ids):
        return [i for i in ids if i not in shot_lines]

    _same_row_spread(build, text_lines(title))
    _text_region(build, "title", text_lines(title), "title")
    if subtitle:
        _text_region(build, "body", text_lines(subtitle), "header")
    title_bottom = max((page.lines[i].bbox[1] + page.lines[i].bbox[3] for i in title), default=0)
    header_limit = max(title_bottom + 0.02 * page.size[1], 0.12 * page.size[1])
    for n, (heading, body, icons) in enumerate(groups, 1):
        members = text_lines(heading + body)
        _same_row_spread(build, members)
        # Brand, kicker and motto lines above the title's baseline are page header.
        top_band = members and all(page.lines[i].bbox[1] + page.lines[i].bbox[3] <= header_limit for i in members)
        _text_region(build, "body", members, "header" if top_band else f"g{n}", headings=len(text_lines(heading)))
        for box, lines, group in shots:
            if group == f"g{n}":
                _screenshot(build, box, lines, group)
        for i in icons:
            if i in shot_lines:
                continue
            if is_noise(page, i):
                build.unpromoted.update(page.lines[i].word_indices)
                build.regions.append(
                    PlannedRegion("decoration", _pad(page.lines[i].bbox, 3, page.size), (), group_id=f"g{n}")
                )
            else:
                _text_region(build, "body", [i], f"g{n}")
    for rows in tables:
        _table(build, [[text_lines(cell) for cell in row] for row in rows], None)
    for box, lines, group in shots:
        if group is None:
            _screenshot(build, box, lines, None)
    for i in text_lines(footer):
        _text_region(build, "body", [i], "footer")
    for i in text_lines(leftovers):
        _text_region(build, "body", [i], None)
    for i in noise:
        build.regions.append(PlannedRegion("decoration", _pad(page.lines[i].bbox, 3, page.size), ()))
