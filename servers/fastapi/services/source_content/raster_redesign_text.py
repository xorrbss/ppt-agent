"""Korean-aware paragraph grouping, wrapping and measurement for redesign pages.

Every non-whitespace source character is emitted exactly once and in order.
Lines are only ever joined with a single space or split at spaces; a token is
split between characters only when it alone is wider than the column.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

LINE_HEIGHT = 1.36
GLYPH_ASCENT, GLYPH_DESCENT = 1.18, 0.30  # browser fragment box around the baseline (em)
PARAGRAPH_GAP = 0.42
HEADING_SCALE = 1.12
# A kept source line break gets this much extra line spacing: invisible, but
# above the PPTX converter's 0.5-unit soft-wrap tolerance, so it stays a hard
# paragraph break instead of being re-flowed into the previous line.
TIGHT_BREAK = 0.6

_MARKER = re.compile(
    r"^\s*(?:[•·▪■□◦○●◆◇▶►▷※✓✔√☞➢➤\-–—*]|\d{1,2}[.)]|\(\d{1,2}\)|[①-⑳]|[가-힣A-Za-z][.)]\s)"
)
_TERMINAL = re.compile(r"(?:[.。!?！？:：;]|다|음|함|됨|임)\s*$")
# Korean connective endings / particles and punctuation that continue a sentence.
_CONNECTIVE = re.compile(r"(?:[,，、·\-–(]|하고|하여|하며|하면|되어|있어|으로|에서|및|등|을|를|는|은|며)\s*$")


SAFETY = 1.06  # headroom for renderer/font substitution differences


@lru_cache(maxsize=1)
def _font_advances() -> dict[int, float] | None:
    """Advance widths (em) of the packaged template font, or None if unreadable.

    The fallback table in ``char_width`` is wider than the real font, so a
    missing font only makes layout more conservative, never overflowing.
    """
    try:
        from fontTools.ttLib import TTFont

        from utils.svg_template_fonts import FONT_PATH

        font = TTFont(str(FONT_PATH), lazy=True)
        metrics, scale = font["hmtx"], 1.0 / font["head"].unitsPerEm
        return {code: metrics[name][0] * scale for code, name in font.getBestCmap().items()}
    except (OSError, ImportError, KeyError):
        return None


def char_width(char: str) -> float:
    """Advance of one character in em for Noto Sans KR, with safety headroom."""
    advances = _font_advances()
    if advances is not None and ord(char) in advances:
        return advances[ord(char)] * SAFETY
    if char == " ":
        return 0.3
    if unicodedata.east_asian_width(char) in {"F", "W", "A"}:
        return 1.0
    if char.isupper() or char.isdigit():
        return 0.64
    return 0.58


def visual_width(value: str) -> float:
    return sum(char_width(char) for char in value)


@dataclass(frozen=True)
class Paragraph:
    text: str
    heading: bool = False
    marker: bool = False
    tight: bool = False  # continues the previous paragraph's sentence: no extra gap


@dataclass(frozen=True)
class TextLine:
    text: str
    size: float
    bold: bool
    color: str
    baseline: float  # relative to the block top
    indent: float = 0.0
    paragraph_start: bool = True


def split_paragraphs(text: str, heading_lines: int | None = None) -> list[Paragraph]:
    """Group source lines into paragraphs without changing any character.

    A source line is joined to the previous one only when the previous line
    looks wrapped (long and non-terminal) and the next line does not start a
    list item. Short standalone lines remain separate items.
    """

    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    count = max(0, min(heading_lines, len(lines))) if heading_lines is not None else 0
    if count:
        # Structured source: the first N lines are the block heading (no guessing).
        head = [Paragraph(line, heading=True, tight=index > 0) for index, line in enumerate(lines[:count])]
        rest = split_paragraphs("\n".join(lines[count:]), heading_lines=0) if lines[count:] else []
        if rest:
            rest[0] = Paragraph(rest[0].text, marker=rest[0].marker, tight=False)
        return head + rest
    heading = None
    if heading_lines is None and len(lines) >= 2 and _looks_like_heading(lines[0]):
        heading, lines = lines[0], lines[1:]
    widest = max(visual_width(line) for line in lines)
    groups: list[tuple[list[str], bool]] = []
    for line in lines:
        previous = groups[-1][0][-1] if groups else None
        continues = previous is not None and not _TERMINAL.search(previous) and not _MARKER.match(line)
        if continues and widest >= 10 and visual_width(previous) >= widest * 0.6:
            groups[-1][0].append(line)
        else:
            groups.append(([line], continues))
    paragraphs = [
        Paragraph(" ".join(group), marker=bool(_MARKER.match(group[0])), tight=tight)
        for group, tight in groups
    ]
    return ([Paragraph(heading, heading=True)] if heading else []) + paragraphs


def _looks_like_heading(line: str) -> bool:
    """A short noun phrase leading a multi-line block (styling only; never drops text)."""
    return (
        visual_width(line) <= 22
        and not _TERMINAL.search(line)
        and not _CONNECTIVE.search(line)
    )


def _break_token(token: str, width_em: float) -> list[str]:
    pieces, chunk = [], ""
    for char in token:
        if chunk and visual_width(chunk + char) > width_em:
            pieces.append(chunk)
            chunk = char
        else:
            chunk += char
    if chunk:
        pieces.append(chunk)
    return pieces


def wrap(text: str, width: float, size: float) -> list[str]:
    """Wrap at spaces; break inside a token only when it cannot fit alone."""

    width_em = width / size
    if width_em < 2.0:
        raise ValueError("raster_redesign_column_too_narrow")
    lines: list[str] = []
    current = ""
    for token in text.split(" "):
        if not token:
            continue
        candidate = f"{current} {token}" if current else token
        if visual_width(candidate) <= width_em:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if visual_width(token) <= width_em:
            current = token
            continue
        pieces = _break_token(token, width_em)
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        lines.append(current)
    return lines


def _wrap_hanging(text: str, width: float, size: float, indent: float) -> list[tuple[str, float]]:
    first = wrap(text, width, size)
    if not indent or len(first) == 1:
        return [(value, 0.0) for value in first]
    head = first[0]
    rest = text[len(head):].strip()
    return [(head, 0.0)] + [(value, indent) for value in wrap(rest, width - indent, size)]


def layout_paragraphs(
    paragraphs: list[Paragraph], width: float, size: float, *,
    body_color: str, heading_color: str,
) -> tuple[list[TextLine], float]:
    """Return positioned lines and total block height for one text column.

    Continuation lines of a list item get a hanging indent under the item
    text, so the marker stays visually attached to its paragraph.
    """

    lines: list[TextLine] = []
    cursor = 0.0
    for paragraph in paragraphs:
        line_size = size * HEADING_SCALE if paragraph.heading else size
        indent = 0.0
        if paragraph.marker:
            match = _MARKER.match(paragraph.text)
            if match:
                indent = min(visual_width(match.group(0)) + 0.3, 2.5) * line_size
        color = heading_color if paragraph.heading else body_color
        for line_index, (value, offset) in enumerate(_wrap_hanging(paragraph.text, width, line_size, indent)):
            if not lines:
                cursor += line_size * GLYPH_ASCENT
            elif line_index == 0:
                cursor += (TIGHT_BREAK if paragraph.tight else size * PARAGRAPH_GAP) + line_pitch(
                    lines[-1].size, line_size)
            else:
                cursor += line_pitch(lines[-1].size, line_size)
            lines.append(TextLine(value, line_size, paragraph.heading, color, cursor, offset, line_index == 0))
    height = cursor + size * (LINE_HEIGHT - 1.0) if lines else 0.0
    return lines, height


def line_pitch(previous_size: float, size: float) -> float:
    """Baseline-to-baseline advance whose glyph boxes never touch in Chrome.

    Chrome's text fragment box for Noto Sans KR spans ~1.16em above and
    ~0.29em below the baseline; the browser gate flags any >0.5px overlap
    between fragments, so the pitch is derived from those metrics.
    """
    return max(size * LINE_HEIGHT, previous_size * GLYPH_DESCENT + size * GLYPH_ASCENT + 1.0)


def split_lines_into_columns(lines: list[TextLine], columns: int) -> list[list[TextLine]]:
    """Balance an ordered line list across columns without reordering it.

    Breaks happen only at paragraph starts (indent 0) when possible so a list
    item is not torn between columns.
    """

    if columns <= 1 or len(lines) < 2:
        return [lines]
    total = lines[-1].baseline
    parts: list[list[TextLine]] = [[]]
    for line in lines:
        target = total * len(parts) / columns
        if (
            parts[-1] and len(parts) < columns and line.indent == 0.0
            and line.baseline - line.size > target - line.size * 0.5
        ):
            parts.append([])
        parts[-1].append(line)
    rebased = []
    for part in parts:
        offset = part[0].baseline - part[0].size * GLYPH_ASCENT
        rebased.append([
            TextLine(line.text, line.size, line.bold, line.color, line.baseline - offset, line.indent,
                     line.paragraph_start)
            for line in part
        ])
    return rebased


def column_height(lines: list[TextLine]) -> float:
    if not lines:
        return 0.0
    return lines[-1].baseline + lines[-1].size * (LINE_HEIGHT - 1.0)
