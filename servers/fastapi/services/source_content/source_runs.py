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
# The divider alternative is listed first on purpose: its leading
# whitespace also matches the gap alternative's two-or-more spaces, and
# alternation resolves left to right, so gap-first left "승인 가능  |  미통과"
# with a bare "|" as a run of its own -- a divider the template draws, so
# no element can own it. Divider-first makes every spacing of " | " behave
# like the single-space form the projection already got right.
_SEPARATOR = re.compile(
    r"(?P<order_free>\s+\|\s+)"
    r"|\t+"
    r"|[ \u00a0\u3000]{2,}"
    r"|(?P<arrow>\s+[→⇒➜➔⟶↦]\s+)"
    rf"|\s+(?=[{re.escape(_ITEM_GLYPHS)}]\s)"
)
# A separator inside a bracket or quote pair is content, not structure:
# "(사례: 재작업 5회 → 1회)" is one parenthetical, and splitting it tears
# the pair across two runs.
_BRACKET_PAIRS = {
    "(": ")", "[": "]", "{": "}", "（": "）", "［": "］", "｛": "｝",
    "〔": "〕", "【": "】", "「": "」", "『": "』", "《": "》",
    "〈": "〉", "“": "”", "‘": "’",
}
_BRACKET_CLOSERS = frozenset(_BRACKET_PAIRS.values())
_SYMMETRIC_QUOTES = "\"'"
_SENTENCE_ENDERS = ".。?!？！"
_CLOSING_MARKS = "\"'’”)]}」』》〉"
_BLANK_FIELD = re.compile(r"(_{3,})")
# A piece made only of bullet/checkbox glyphs and enumerators carries no source
# characters of its own. Digits are deliberately excluded: "16" is a page
# number a verbatim restyle must preserve, not decoration.
_MARKER_ONLY = re.compile(
    rf"^(?:[{re.escape(_MARKER_GLYPHS)}]|[{_ENUMERATORS}]|[.)\s])+$"
)
_RUN_ID = re.compile(r"^(?P<block>.+):run:(?P<start>\d+)(?:-(?P<end>\d+))?$")


def _squash(value: object) -> str:
    """Drop whitespace entirely so layout cannot hide present source text."""

    return re.sub(r"\s+", "", str(value or ""))


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

    def contains(self, start: int, end: int, rendered: object) -> bool:
        """Return whether one element's text carries this span's characters.

        Whitespace is the template's and the characters are the source's, so
        both sides are compared with whitespace removed and every run's marker
        optional.  Unlike :meth:`accepts` the element may carry other text as
        well, and the span's runs may appear in any position: this answers
        *presence*, which is the content contract, while ``accepts`` answers
        exclusive ownership, which is a measurement convenience.
        """

        haystack = _squash(rendered)
        if not haystack:
            return False
        for index in range(start, end + 1):
            run = self.runs[index]
            body = _squash(run.body)
            if body and body not in haystack:
                return False
        return True

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
    lines = _drop_marker_only_lines(lines)
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


def _drop_marker_only_lines(lines: list[str]) -> list[str]:
    """Drop a line that is only presentation, keeping the block's own text.

    A flow written as ``문제 제시`` / ``↓`` / ``해결·정보`` puts the arrow on its
    own line.  The arrow is drawn by the new template -- as a border, a CSS
    glyph or an empty span -- so a run holding nothing but that arrow can never
    find a DOM owner, and the block failed every slide that used the shape.
    ``_drop_marker_only_pieces`` already folds such a piece into the item after
    it, but only inside one line; a line of its own never reached it.

    A block that is nothing but glyphs keeps them: there is no item to fold
    into, and projecting an empty block would lose the source entirely.
    """

    kept = [
        line
        for line in lines
        if not _MARKER_ONLY.match(normalize_source_text(line))
    ]
    return kept or lines


def _segments(line: str) -> list[tuple[str, bool]]:
    """Return each piece with whether the separator before it frees its order.

    A candidate separator only becomes a boundary where splitting there is
    structure: outside every bracket or quote pair the line closes, and -- for
    an arrow -- while the flow it chains has not already run into prose.
    """

    pieces: list[tuple[str, bool]] = []
    protected = _protected_spans(line)
    cursor = 0
    order_free = False
    chain_ended = False
    for match in _SEPARATOR.finditer(line):
        if _is_protected(protected, match.start(), match.end()):
            continue
        if match.group("arrow") is not None:
            if chain_ended or _holds_a_sentence_break(line[cursor : match.start()]):
                chain_ended = True
                continue
        pieces.extend(_split_blank_fields(line[cursor : match.start()], order_free))
        order_free = match.group("order_free") is not None
        cursor = match.end()
    pieces.extend(_split_blank_fields(line[cursor:], order_free))
    return _drop_marker_only_pieces(pieces)


def _protected_spans(line: str) -> tuple[tuple[int, int], ...]:
    """Return the span of every bracket or quote pair the line closes.

    Only a pair that closes protects anything. An opener left dangling at
    end of line -- a smiley, a stray quote, a truncated source cell --
    protects nothing, so an unbalanced line degrades to the plain separator
    rules instead of swallowing its whole remainder into one run.
    """

    spans: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    quotes: dict[str, int] = {}
    for index, char in enumerate(line):
        if char in _BRACKET_CLOSERS:
            for depth in range(len(stack) - 1, -1, -1):
                if _BRACKET_PAIRS[stack[depth][0]] == char:
                    spans.append((stack[depth][1], index))
                    # Openers nested above this one never close; drop them.
                    del stack[depth:]
                    break
        elif char in _BRACKET_PAIRS:
            stack.append((char, index))
        elif char in _SYMMETRIC_QUOTES:
            opened = quotes.get(char)
            if opened is not None and _closes_quote(line, index):
                spans.append((opened, index))
                del quotes[char]
            elif opened is None and _opens_quote(line, index):
                quotes[char] = index
    return tuple(spans)


def _is_protected(
    spans: tuple[tuple[int, int], ...], start: int, end: int
) -> bool:
    return any(begin < start and end <= close for begin, close in spans)


def _opens_quote(line: str, index: int) -> bool:
    """A straight quote opens only where a word can begin after it."""

    before = line[index - 1] if index else " "
    after = line[index + 1] if index + 1 < len(line) else ""
    if not after or after.isspace():
        return False
    return before.isspace() or before in _BRACKET_PAIRS


def _closes_quote(line: str, index: int) -> bool:
    """A straight quote closes only where a word can have ended before it.

    This stops the apostrophe in "don't" opening a region that never closes.
    """

    before = line[index - 1] if index else ""
    after = line[index + 1] if index + 1 < len(line) else " "
    if not before or before.isspace():
        return False
    return (
        after.isspace()
        or after in _BRACKET_CLOSERS
        or after in _SENTENCE_ENDERS
        or after in ",;:"
    )


def _terminates_sentence(text: str, index: int) -> bool:
    """Whether ``text[index]`` ends a sentence rather than punctuating a token.

    A period after a digit is a decimal, an enumerator or a Korean date part
    ("3.5", "1.", "2026. 09. 04."), and a period beside another period is an
    ellipsis.  Neither ends a sentence.
    """

    char = text[index]
    if char not in _SENTENCE_ENDERS:
        return False
    if char != ".":
        return True
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    return not (previous.isdigit() or previous == "." or following == ".")


def _holds_a_sentence_break(piece: str) -> bool:
    """Whether ``piece`` finishes a sentence and then keeps writing.

    An arrow chain enumerates steps.  Once a piece has run past a full stop
    into a second sentence it is prose, and the arrows still to come on the
    line are that prose's own punctuation -- "재작업 5회 → 1회." is a phrase,
    not two more steps.  A terminator that merely ends the piece breaks
    nothing, so the closing period of a real chain's last step is left alone.
    """

    index = 0
    while index < len(piece):
        if not _terminates_sentence(piece, index):
            index += 1
            continue
        after = index + 1
        while after < len(piece) and (
            piece[after] in _SENTENCE_ENDERS or piece[after] in _CLOSING_MARKS
        ):
            after += 1
        # Glued to the next character it is a host name or an abbreviation.
        if after < len(piece) and piece[after].isspace() and piece[after:].strip():
            return True
        index = after
    return False


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
