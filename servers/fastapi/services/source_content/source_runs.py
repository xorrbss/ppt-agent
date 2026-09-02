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
from functools import lru_cache
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
    r"|(?P<order_free>\s+\|\s+)"
    rf"|\s+(?=[{re.escape(_ITEM_GLYPHS)}]\s)"
)
_BLANK_FIELD = re.compile(r"(_{3,})")
# A piece made only of bullet/checkbox glyphs and enumerators carries no source
# characters of its own. Digits are deliberately excluded: "16" is a page
# number a verbatim restyle must preserve, not decoration.
_MARKER_ONLY = re.compile(
    rf"^(?:[{re.escape(_MARKER_GLYPHS)}]|[{_ENUMERATORS}]|[.)\s])+$"
)
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
    # A pipe joins independent labels on one line -- a brand beside a course
    # title -- and the new template owns where each lands, so this run may be
    # rendered before the one preceding it. Bullets, arrows and lines enumerate
    # a sequence whose order the source author chose, and keep it.
    order_free_before: bool = False


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

    def accepts(self, start: int, end: int, rendered: object) -> bool:
        """Return whether one element's rendered text satisfies a span.

        The canonical forms from :meth:`accepted_texts` are accepted, and so
        is every rendering that keeps the span's source characters intact
        while treating only presentation loosely:

        * any glyph of any run's marker may be omitted (a CSS bullet, a drawn
          number badge, a list that keeps ``①`` but drops ``•``);
        * a pipe or arrow that joins this span to its neighbour on the same
          line may stay attached to the element's edge (``꿀벌쌤 |`` beside
          ``숏폼대행 1인사업자 홀로서기``).

        Bodies, blank fields, punctuation, and the separators *between* the
        span's own runs must still match character for character.
        """

        self._check_span(start, end)
        return _span_pattern(self, start, end).fullmatch(normalize_source_text(rendered)) is not None

    def _check_span(self, start: int, end: int) -> None:
        if not (0 <= start <= end < len(self.runs)):
            raise ValueError("source run span is out of range")


@lru_cache(maxsize=4096)
def _span_pattern(projection: SourceRunProjection, start: int, end: int) -> re.Pattern[str]:
    """Compile the tolerant matcher for one run span (see ``accepts``)."""

    runs = projection.runs
    parts: list[str] = []
    if start > 0 and runs[start - 1].line == runs[start].line:
        before = projection.text[runs[start - 1].end : runs[start].begin].strip()
        if before:
            parts.append(rf"(?:{re.escape(before)}\s*)?")
    for index in range(start, end + 1):
        run = runs[index]
        if index > start:
            parts.append(re.escape(projection.text[runs[index - 1].end : run.begin]))
        for token in _marker_tokens(run.marker):
            parts.append(rf"(?:{re.escape(token)}\s*)?")
        parts.append(re.escape(run.body))
    if end + 1 < len(runs) and runs[end + 1].line == runs[end].line:
        after = projection.text[runs[end].end : runs[end + 1].begin].strip()
        if after:
            parts.append(rf"(?:\s*{re.escape(after)})?")
    return re.compile("".join(parts))


def _marker_tokens(marker: str) -> list[str]:
    """Split a peeled marker back into the glyphs ``_split_marker`` matched."""

    tokens: list[str] = []
    position = 0
    while position < len(marker):
        match = _MARKER.match(marker, position)
        if match is None or match.end() == position:  # pragma: no cover - markers are _MARKER matches
            tokens.append(marker[position:].strip())
            break
        tokens.append(match.group(0).strip())
        position = match.end()
    return [token for token in tokens if token]


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
        for segment, order_free in _segments(line):
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
                    order_free_before=order_free,
                )
            )
            cursor = position + len(piece)
        line_spans.append((first_run, len(runs) - 1))
        offset += len(normalized_line) + 1
    if not runs:
        raise ValueError("source text block has no visible text")
    return SourceRunProjection(text=text, runs=tuple(runs), lines=tuple(line_spans))


def _segments(line: str) -> list[tuple[str, bool]]:
    """Return each piece with whether the separator before it frees its order."""

    pieces: list[tuple[str, bool]] = []
    cursor = 0
    order_free = False
    for match in _SEPARATOR.finditer(line):
        pieces.extend(_split_blank_fields(line[cursor : match.start()], order_free))
        order_free = match.group("order_free") is not None
        cursor = match.end()
    pieces.extend(_split_blank_fields(line[cursor:], order_free))
    return _drop_marker_only_pieces(pieces)


def _split_blank_fields(chunk: str, order_free: bool) -> list[tuple[str, bool]]:
    parts = [part for part in _BLANK_FIELD.split(chunk) if part.strip()]
    return [(part, order_free and index == 0) for index, part in enumerate(parts)]


def _drop_marker_only_pieces(
    pieces: list[tuple[str, bool]],
) -> list[tuple[str, bool]]:
    """Fold a piece that is only presentation into the item it introduces.

    Splitting before every item glyph turns ``• □ 누구에게`` into ``•`` and
    ``□ 누구에게``: the bullet loses its item and becomes a run of its own.
    Nothing renders it -- authored markup draws bullets with CSS or an empty
    decoration span -- so such a run could never find a DOM owner and failed
    every block that used a glyph pair. A marker belongs to the item after it,
    and a trailing one is presentation with nothing left to introduce.
    """

    kept = [
        piece
        for piece in pieces
        if not _MARKER_ONLY.match(normalize_source_text(piece[0]))
    ]
    # A block that is nothing but glyphs has no item to fold them into; keep it
    # as-is rather than projecting an empty block.
    return kept or pieces


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
