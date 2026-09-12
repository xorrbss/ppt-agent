"""Line/piece segmentation for source-run projection.

Splits one normalized block line into ordered pieces at bullet/arrow/pipe/gap
boundaries, protecting brackets and quoted spans and dropping presentation-
only material. Split out of ``source_runs.py`` (which owns the run-projection
algorithm and offsets that consume this module's output) to keep that file
under the file-size ratchet.
"""

from __future__ import annotations

import re

from services.source_content.source_text_shape import _ITEM_GLYPHS, _MARKER_GLYPHS, normalize_source_text

# The divider alternative is listed first on purpose: its leading
# whitespace also matches the gap alternative's two-or-more spaces, and
# alternation resolves left to right, so gap-first left "승인 가능  |  미통과"
# with a bare "|" as a run of its own -- a divider the template draws, so
# no element can own it. Divider-first makes every spacing of " | " behave
# like the single-space form the projection already got right.
_SEPARATOR = re.compile(
    r"(?P<order_free>\s+\|\s+)"
    r"|\t+"
    r"|[  　]{2,}"
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
# A piece made only of bullet/checkbox/arrow glyphs carries no source
# characters of its own -- the template draws it (a border, a CSS glyph, a
# list-style bullet) -- so it is dropped outright. Digits are deliberately
# excluded: "16" is a page number a verbatim restyle must preserve, not
# decoration. An enumerator or corporate mark (``①``, ``㈜``) is deliberately
# NOT included: a Korean business document actually prints one, so it is
# content, never template furniture, even standing alone on its own line or
# piece (see ``_drop_marker_only_lines`` / ``_drop_marker_only_pieces``).
_MARKER_ONLY = re.compile(rf"^(?:[{re.escape(_MARKER_GLYPHS)}]|[.)\s])+$")


def _drop_marker_only_lines(lines: list[str]) -> list[str]:
    """Drop a line that is only presentation, keeping the block's own text.

    A flow written as ``문제 제시`` / ``↓`` / ``해결·정보`` puts the arrow on its
    own line.  The arrow is drawn by the new template -- as a border, a CSS
    glyph or an empty span -- so a run holding nothing but that arrow can never
    find a DOM owner, and the block failed every slide that used the shape.
    ``_drop_marker_only_pieces`` already folds such a piece into the item after
    it, but only inside one line; a line of its own never reached it.

    ``_MARKER_ONLY`` covers only ``_MARKER_GLYPHS`` (bullets, checkboxes,
    arrows) -- never an enumerator or corporate mark (``①``, ``㈜``), which a
    Korean business document actually prints. A line that is only one of
    those is kept as its own run rather than dropped: ``_split_marker``'s own
    rule against consuming an entire segment already turns a lone enumerator
    line into a body-only run, so ``"㈜\\n아이비즈소프트웨어"`` keeps its mark
    instead of losing it.

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

    ``_MARKER_ONLY`` covers only ``_MARKER_GLYPHS`` -- never an enumerator or
    corporate mark, which is content. A piece split off by a real separator
    (``"㈜ | 아이비즈"`` splits into ``"㈜"`` and ``"아이비즈"``) cannot be glued
    to the next one without also swallowing that separator, so it is kept as
    its own run instead of being merged or dropped; ``_split_marker``'s rule
    against consuming an entire segment already turns it into a body, not a
    marker with no body.
    """

    kept = [
        piece
        for piece in pieces
        if not _MARKER_ONLY.match(normalize_source_text(piece[0]))
    ]
    # A block that is nothing but glyphs has no item to fold them into; keep it
    # as-is rather than projecting an empty block.
    return kept or pieces
