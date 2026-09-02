"""Deterministic sub-run projection of immutable source text blocks.

A pinned manifest stores one text block per source paragraph.  Authored HTML
legitimately renders a bullet list as one element per item, an arrow flow as
one chip per step, and a multi-line block as one element per line.  This
module projects a block into *atomic runs* (lines, then bullet/arrow/pipe/gap
separated segments) with stable offsets into the normalized block text, so
stamping and readback can bind a contiguous run span to one DOM element and
reproduce the identical projection from the manifest alone.

The manifest itself is never rewritten: run identities are derived from the
block ID plus the run span, ``<blockId>:run:<start>`` or
``<blockId>:run:<start>-<end>``, and a whole-block binding keeps the plain
block ID so existing receipts are unchanged for single-element blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_MARKER_GLYPHS = "•·◦▪▫■□◆◇●○★☆✓✔✗☐☑▶►➤➜→⇒↓⇨‣⁃∙*-–—"
_ITEM_GLYPHS = "•□■◦▪✓✔☐☑▶►➤‣⁃"
_ENUMERATORS = "①-⑳⑴-⒇⒈-⒛❶-❿㉠-㉭㈀-㈜"
_MARKER = re.compile(
    rf"(?:[{re.escape(_MARKER_GLYPHS)}]\s+|[{_ENUMERATORS}]\s*|\d{{1,2}}[.)]\s+)"
)
_SEPARATOR = re.compile(
    r"\t+"
    r"|[ \u00a0\u3000]{2,}"
    r"|\s+[→⇒➜➔⟶↦]\s+"
    r"|\s+\|\s+"
    rf"|\s+(?=[{re.escape(_ITEM_GLYPHS)}]\s)"
)
_BLANK_FIELD = re.compile(r"(_{3,})")
_RUN_ID = re.compile(r"^(?P<block>.+):run:(?P<start>\d+)(?:-(?P<end>\d+))?$")


def normalize_source_text(value: object) -> str:
    """Collapse layout whitespace exactly like the readback verifiers."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


@dataclass(frozen=True, slots=True)
class SourceRun:
    """One atomic run with offsets into ``SourceRunProjection.text``."""

    index: int
    line: int
    marker: str
    body: str
    begin: int
    body_begin: int
    end: int


@dataclass(frozen=True, slots=True)
class SourceRunProjection:
    """Atomic runs plus the line spans they were projected from."""

    text: str
    runs: tuple[SourceRun, ...]
    lines: tuple[tuple[int, int], ...]

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def whole(self) -> tuple[int, int]:
        return (0, len(self.runs) - 1)

    def span_text(self, start: int, end: int) -> str:
        self._check_span(start, end)
        return self.text[self.runs[start].begin : self.runs[end].end]

    def accepted_texts(self, start: int, end: int) -> tuple[str, ...]:
        """Rendered texts that satisfy a span verbatim.

        Bullet glyphs, enumerators, and leading arrows are presentational, so
        the span is also accepted without the *leading* marker of its first
        run (CSS bullets, numbered badges, drawn arrows).  Nothing else may be
        added, dropped, or reordered.
        """

        full = self.span_text(start, end)
        first = self.runs[start]
        if first.marker:
            return (full, self.text[first.body_begin : self.runs[end].end])
        return (full,)

    def _check_span(self, start: int, end: int) -> None:
        if not (0 <= start <= end < len(self.runs)):
            raise ValueError("source run span is out of range")


def project_source_runs(raw_text: object) -> SourceRunProjection:
    """Split raw block text into deterministic atomic runs."""

    raw = str(raw_text or "")
    lines = [line for line in re.split(r"\r\n|\r|\n", raw) if line.strip()]
    normalized_lines = [normalize_source_text(line) for line in lines]
    text = " ".join(normalized_lines)
    runs: list[SourceRun] = []
    line_spans: list[tuple[int, int]] = []
    offset = 0
    for line_index, (line, normalized_line) in enumerate(
        zip(lines, normalized_lines, strict=True)
    ):
        cursor = 0
        first_run = len(runs)
        for segment in _segments(line):
            marker, body = _split_marker(normalize_source_text(segment))
            piece = marker + body
            position = normalized_line.find(piece, cursor)
            if position < 0:  # pragma: no cover - segments are substrings
                raise ValueError("source run projection is inconsistent")
            begin = offset + position
            runs.append(
                SourceRun(
                    index=len(runs),
                    line=line_index,
                    marker=marker,
                    body=body,
                    begin=begin,
                    body_begin=begin + len(marker),
                    end=begin + len(piece),
                )
            )
            cursor = position + len(piece)
        line_spans.append((first_run, len(runs) - 1))
        offset += len(normalized_line) + 1
    if not runs:
        raise ValueError("source text block has no visible text")
    return SourceRunProjection(text=text, runs=tuple(runs), lines=tuple(line_spans))


def _segments(line: str) -> list[str]:
    pieces: list[str] = []
    for chunk in _SEPARATOR.split(line):
        for part in _BLANK_FIELD.split(chunk):
            if part.strip():
                pieces.append(part)
    return pieces


def _split_marker(segment: str) -> tuple[str, str]:
    """Peel leading presentational markers, keeping a non-empty body."""

    marker_end = 0
    while True:
        match = _MARKER.match(segment, marker_end)
        if match is None or match.end() >= len(segment):
            break
        marker_end = match.end()
    return segment[:marker_end], segment[marker_end:]


def source_run_binding_id(
    block_id: str, start: int, end: int, run_count: int
) -> str:
    """Return the DOM binding ID for a run span of ``block_id``."""

    if start == 0 and end == run_count - 1:
        return block_id
    if start == end:
        return f"{block_id}:run:{start}"
    return f"{block_id}:run:{start}-{end}"


def parse_source_binding_id(value: str) -> tuple[str, int | None, int | None]:
    """Split a DOM binding ID into ``(block_id, start, end)``.

    A plain block ID yields ``(block_id, None, None)``.
    """

    raw = str(value or "").strip()
    match = _RUN_ID.match(raw)
    if match is None:
        return raw, None, None
    start = int(match.group("start"))
    end = int(match.group("end")) if match.group("end") is not None else start
    return match.group("block"), start, end


__all__ = [
    "SourceRun",
    "SourceRunProjection",
    "normalize_source_text",
    "parse_source_binding_id",
    "project_source_runs",
    "source_run_binding_id",
]
