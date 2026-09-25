"""Text blocks -> exactly partitioned, source-bound paragraphs.

The browser readback binds each projected run of a text block to exactly one
DOM element, or the whole block to one element. This module produces the
``Paragraph`` list the renderer draws for a block so that every run index is
covered exactly once, with the binding id ``source_run_binding_id`` derives,
and attaches the source formatting to each paragraph by aligning the
manifest's ``a:r`` runs onto the projection character by character.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from services.composer.classify_common import (
    FormattedRun,
    Geometry,
    dominant_size_pt,
    field,
    formatted_runs,
    is_sequence,
    mapping_or_empty,
    parse_geometry,
    placeholder_type,
    squash,
)
from services.composer.plan import Paragraph, SourceRunRef, TextRun
from services.source_content.source_run_segments import _MARKER_ONLY
from services.source_content.source_runs import (
    SourceRunProjection,
    normalize_source_text,
    project_source_runs,
    source_run_binding_id,
)

_ENUMERATOR_MARKER = re.compile(r"^(?:\d{1,2}[.)]|[①-⑳⑴-⒇⒈-⒛❶-❿])")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True, slots=True)
class TextBlock:
    """One manifest text block with its projection and the enrichment it carries."""

    block_id: str
    order: int
    raw_text: str
    projection: SourceRunProjection
    geometry: Geometry | None
    placeholder_type: str | None
    dominant_size_pt: float | None
    runs: tuple[FormattedRun, ...]
    paragraphs_meta: tuple[Mapping[str, Any], ...]
    #: Projection line index -> ``paragraphs_meta`` index, ``-1`` when unknown.
    line_paragraph: tuple[int, ...]
    #: Whitespace-free source text and, per character, the ``runs`` index it came from.
    squashed_raw: str
    char_runs: tuple[int, ...]
    text_body: Mapping[str, Any]
    source_shape_index: int
    source_transform: Mapping[str, Any]

    @property
    def text(self) -> str:
        return self.projection.text

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def line_count(self) -> int:
        return len(self.projection.lines)

    @property
    def bold_share(self) -> float:
        """Share of source characters set bold; 0 when formatting is unknown."""

        total = len(self.squashed_raw)
        if total == 0:
            return 0.0
        bold = sum(len(squash(run.text)) for run in self.runs if run.bold)
        return bold / total


def build_text_block(block: Mapping[str, Any]) -> TextBlock | None:
    """Project one manifest text block; ``None`` when it carries no visible text."""

    block_id = str(field(block, "source_block_id", "sourceBlockId", default="") or "")
    if not block_id:
        raise ValueError("source block identity is missing")
    payload = mapping_or_empty(field(block, "payload", default={}))
    runs = formatted_runs(payload)
    raw_text = payload.get("text")
    if not isinstance(raw_text, str):
        raw_text = "".join(run.text for run in runs)
    try:
        projection = project_source_runs(raw_text)
    except ValueError:
        return None
    raw_meta = payload.get("paragraphs")
    meta = tuple(item for item in raw_meta if isinstance(item, Mapping)) if is_sequence(raw_meta) else ()
    try:
        order = int(field(block, "order", default=0) or 0)
    except (TypeError, ValueError):
        order = 0
    return TextBlock(
        block_id=block_id,
        order=order,
        raw_text=raw_text,
        projection=projection,
        geometry=parse_geometry(payload),
        placeholder_type=placeholder_type(block, payload),
        dominant_size_pt=dominant_size_pt(payload, runs),
        runs=runs,
        paragraphs_meta=meta,
        text_body=mapping_or_empty(payload.get("textBody")),
        source_shape_index=int(payload.get("sourceShapeIndex", order)),
        source_transform=mapping_or_empty(payload.get("geometry")),
        line_paragraph=_line_paragraphs(projection, meta),
        squashed_raw="".join(squash(run.text) for run in runs),
        char_runs=tuple(index for index, run in enumerate(runs) for _ in squash(run.text)),
    )


def _line_paragraphs(
    projection: SourceRunProjection, meta: Sequence[Mapping[str, Any]]
) -> tuple[int, ...]:
    """Attribute each projected line to the source paragraph whose text contains it.

    Lines and paragraphs differ only by ``a:br`` breaks inside a paragraph and
    by the marker-only lines the projection drops, so a sequential substring
    search over the normalized paragraph texts recovers the mapping.
    """

    texts = [normalize_source_text(item.get("text")) for item in meta]
    result: list[int] = []
    paragraph, cursor = 0, 0
    for first, last in projection.lines:
        line_text = projection.span_text(first, last)
        while paragraph < len(texts):
            position = texts[paragraph].find(line_text, cursor)
            if position >= 0:
                cursor = position + len(line_text)
                break
            paragraph, cursor = paragraph + 1, 0
        result.append(paragraph if paragraph < len(texts) else -1)
    return tuple(result)


def bind_paragraphs(block: TextBlock) -> list[Paragraph]:
    """Partition a block's runs into paragraphs, each bound exactly once.

    A one-run block, and a one-line block whose runs are separated only by
    whitespace gaps (layout, not structure), bind as the whole block. Lists,
    arrow flows and multi-line blocks bind one paragraph per run so the
    template may place each item, step or line on its own.
    """

    projection = block.projection
    if projection.run_count == 1 or _whitespace_gaps_only(projection):
        return [paragraph_for_span(block, 0, projection.run_count - 1)]
    return [paragraph_for_span(block, index, index) for index in range(projection.run_count)]



def bind_source_paragraphs(block: TextBlock) -> list[Paragraph]:
    """Group projection fragments by actual a:p boundaries, using cumulative offsets.

    Each paragraph's own projection determines its number of fragments. Repeated
    text is safe because no substring search is used to recover its source index.
    Formatting is copied directly from that paragraph's a:r/a:fld sequence.
    """
    if not block.paragraphs_meta:
        return [paragraph_for_span(block, 0, block.projection.run_count - 1)]
    result: list[Paragraph] = []
    cursor = 0
    for index, meta in enumerate(block.paragraphs_meta):
        paragraph_text = str(meta.get("text") or "")
        # The block projection drops a standalone arrow between flow steps.
        # Its source paragraph must not consume the next step's run.
        if len(block.paragraphs_meta) > 1 and _MARKER_ONLY.fullmatch(normalize_source_text(paragraph_text)):
            continue
        projection = _project_or_none(paragraph_text)
        if projection is None:
            result.append(Paragraph(text=str(meta.get("text") or ""),
                source=SourceRunRef(block_id=block.block_id, binding_id=block.block_id),
                layout={**dict(meta), "sourceParagraphIndex": index, "emptySourceParagraph": True}))
            continue
        end = cursor + projection.run_count - 1
        if end >= block.projection.run_count:
            raise ValueError(f"source paragraph range invalid: {block.block_id}")
        span_text = block.projection.span_text(cursor, end)
        if not projection.accepts(0, projection.run_count - 1, span_text):
            raise ValueError(f"source paragraph projection mismatch: {block.block_id}")
        runs = [TextRun(text=r.text, bold=r.bold, italic=r.italic, size_pt=r.size_pt,
                        color=r.color, font_family=r.font_family) for r in formatted_runs(meta)]
        # Source whitespace remains verbatim. The binding matcher owns normalized
        # comparison; neither projected fragments nor formatting invent paragraphs.
        whole = cursor == 0 and end == block.projection.run_count - 1
        bound = Paragraph(text=str(meta.get("text") or ""), runs=runs,
            bullet=bool(meta.get("bullet")), bullet_kind=meta.get("bulletKind"),
            level=min(8, max(0, int(meta.get("level") or 0))),
            source=SourceRunRef(block_id=block.block_id, run_start=None if whole else cursor,
                run_end=None if whole else end,
                binding_id=source_run_binding_id(block.block_id, cursor, end, block.projection.run_count)),
            layout={**dict(meta), "sourceParagraphIndex": index})
        result.append(bound)
        cursor = end + 1
    if cursor != block.projection.run_count:
        raise ValueError(f"source paragraph coverage invalid: {block.block_id}")
    return result

def _whitespace_gaps_only(projection: SourceRunProjection) -> bool:
    if len(projection.lines) != 1:
        return False
    runs = projection.runs
    if any(run.marker or run.order_free_before for run in runs):
        return False
    return all(
        not projection.text[previous.end : current.begin].strip()
        for previous, current in zip(runs, runs[1:], strict=False)
    )


def paragraph_for_span(block: TextBlock, start: int, end: int) -> Paragraph:
    """One paragraph carrying runs ``start..end`` of the block, verbatim."""

    projection = block.projection
    text = projection.span_text(start, end)
    whole = start == 0 and end == projection.run_count - 1
    first = projection.runs[start]
    meta = _meta_for_line(block, first.line)
    marker_bullet = bool(first.marker.strip())
    bullet = bool(meta.get("bullet")) or marker_bullet
    bullet_kind = meta.get("bulletKind") if isinstance(meta.get("bulletKind"), str) else None
    if bullet_kind is None and marker_bullet:
        bullet_kind = "enumerator" if is_enumerator_marker(first.marker) else "character"
    try:
        level = min(max(int(meta.get("level") or 0), 0), 8)
    except (TypeError, ValueError):
        level = 0
    source_paragraph_index = block.line_paragraph[first.line]
    first_source_line = block.line_paragraph.index(source_paragraph_index)
    layout = dict(meta)
    layout["sourceParagraphIndex"] = source_paragraph_index
    layout["sourceBulletOwner"] = start == projection.lines[first_source_line][0]
    return Paragraph(
        layout=layout,
        text=text,
        runs=text_runs_for_span(block, start, end),
        bullet=bullet,
        bullet_kind=bullet_kind if bullet else None,
        level=level,
        source=SourceRunRef(
            block_id=block.block_id,
            run_start=None if whole else start,
            run_end=None if whole else end,
            binding_id=source_run_binding_id(block.block_id, start, end, projection.run_count),
        ),
    )


def _meta_for_line(block: TextBlock, line: int) -> Mapping[str, Any]:
    if 0 <= line < len(block.line_paragraph):
        index = block.line_paragraph[line]
        if 0 <= index < len(block.paragraphs_meta):
            return block.paragraphs_meta[index]
    return {}


def is_enumerator_marker(marker: str) -> bool:
    return _ENUMERATOR_MARKER.match(marker.strip()) is not None


def text_runs_for_span(block: TextBlock, start: int, end: int) -> list[TextRun]:
    """Split the span text at formatting boundaries; the texts join back to the span.

    The manifest's runs and the projection's runs segment the same characters
    differently, so both are reduced to their whitespace-free characters and
    aligned by position; whitespace attaches to the segment before it.
    """

    projection = block.projection
    text = projection.span_text(start, end)
    if not block.char_runs:
        return [TextRun(text=text)]
    prefix = len(squash(projection.text[: projection.runs[start].begin]))
    position = block.squashed_raw.find(squash(text), prefix)
    if position < 0:
        return [TextRun(text=text)]
    segments: list[tuple[FormattedRun, list[str]]] = []
    for char in text:
        if char.isspace():
            if segments:
                segments[-1][1].append(char)
            continue
        run = block.runs[block.char_runs[position]]
        position += 1
        if segments and _same_format(segments[-1][0], run):
            segments[-1][1].append(char)
        else:
            segments.append((run, [char]))
    return [
        TextRun(text="".join(chars), bold=run.bold, italic=run.italic, size_pt=run.size_pt, color=run.color, font_family=run.font_family)
        for run, chars in segments
    ]


def _same_format(left: FormattedRun, right: FormattedRun) -> bool:
    return (left.bold, left.italic, left.size_pt, left.color, left.font_family) == (
        right.bold,
        right.italic,
        right.size_pt,
        right.color,
        right.font_family,
    )


def block_lines(block: TextBlock) -> list[str]:
    """Non-blank raw lines of the block, before the projection dropped any."""

    return [line for line in _LINE_BREAK.split(block.raw_text) if line.strip()]


# --- Tables -------------------------------------------------------------------


def table_rows(block_id: str, payload: Mapping[str, Any]) -> tuple[list[list[Paragraph]], int]:
    """Bind every table cell to the table block as a whole.

    The manifest stores a table as one block with ``rows[][].text`` and no
    block-level ``text``: cells have no identity of their own, the browser
    readback verifies only ``text`` and ``image`` blocks and rejects ``:run:``
    bindings on any other kind. So each cell's ref is the whole table block
    (``binding_id == source_block_id``, no run span); the renderer writes that
    id and its qid once on the ``<table>`` element (native export contract)
    and the cells carry none. Cell text is the projection's normalized text;
    an empty cell stays an empty paragraph so the grid keeps its shape. The
    first row is the header row.

    Returns the rows and the number of normalized characters they carry.
    """

    rows: list[list[Paragraph]] = []
    chars = 0
    raw_rows = payload.get("rows") or []
    for row_index, row in enumerate(_cell_texts(payload)):
        bound: list[Paragraph] = []
        for column_index, text in enumerate(row):
            cell = mapping_or_empty(raw_rows[row_index][column_index]) if row_index < len(raw_rows) and column_index < len(raw_rows[row_index]) else {}
            projection = _project_or_none(text)
            cell_text = text
            chars += len(cell_text)
            cell_runs = formatted_runs(cell)
            bound.append(
                Paragraph(
                    text=cell_text,
                    runs=[TextRun(text=r.text, bold=r.bold, italic=r.italic, size_pt=r.size_pt, color=r.color, font_family=r.font_family) for r in cell_runs] if cell_runs else ([TextRun(text=cell_text)] if cell_text else []),
                    layout=dict(cell),
                    source=SourceRunRef(block_id=block_id, binding_id=block_id),
                )
            )
        rows.append(bound)
    return rows, chars


def _cell_texts(payload: Mapping[str, Any]) -> list[list[str]]:
    rows = payload.get("rows")
    if is_sequence(rows) and all(is_sequence(row) for row in rows):
        return [[str(mapping_or_empty(cell).get("text") or "") for cell in row] for row in rows]
    cells = payload.get("cells")
    if is_sequence(cells) and all(is_sequence(row) for row in cells):
        return [[str(cell or "") for cell in row] for row in cells]
    return []


def _project_or_none(text: str) -> SourceRunProjection | None:
    try:
        return project_source_runs(text)
    except ValueError:
        return None


__all__ = [
    "TextBlock",
    "bind_paragraphs",
    "block_lines",
    "build_text_block",
    "is_enumerator_marker",
    "paragraph_for_span",
    "table_rows",
    "text_runs_for_span",
]
