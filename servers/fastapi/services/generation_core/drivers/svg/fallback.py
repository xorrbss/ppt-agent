"""The deterministic page an SVG deck publishes when a page is out of attempts.

This is the SVG driver's counterpart to the authored driver's placeholder page
(``services/authored_author_stage_placeholder.py``): the page the terminal
rescue (``generation_core/drivers/svg/terminal.py``) publishes, flagged, once a
slide's SVG failed the contract on every attempt and its last render either
does not exist or lost content. The outline lines are read the way the
authored placeholder reads them (``_lines``, with markdown syntax shown as
plain text), but nothing is cut: every line
is published, whole. Content that does not fit at the regular size is set
smaller and, if needed, in more columns -- a flagged page that is hard to read
is reviewable; a page that silently dropped part of the slide is not.

Nothing here calls a model provider or a browser: the same inputs always
produce the same bytes, so a flagged deck is reproducible from its receipt.
Layout is computed directly against the SVG contract's static subset (no DOM,
no font metrics service) using a fixed, deterministic character-width
estimate, because that subset has no text wrapping or reflow of its own.

The page carries no failure reason or error code -- that lives in receipts,
not in customer-facing content. It only ever shows the slide's own outline
text. A title longer than one line wraps at word boundaries (two lines at the
regular size, smaller if it still does not fit); it is drawn once, whole, and
never cut. When the deck's common header is applied the header owns the
title and the page does not draw it again.
"""

from __future__ import annotations

import copy
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
import xml.etree.ElementTree as ET

from utils.color_contrast import readable_ink
from services.svg_contract import SVG_NS, SVG_CONTRACT_VERSION, finalize_svg

FALLBACK_GROUP_ID = "svg-fallback-page"

_MARGIN_X = 64
_TOP_MARGIN = 64
_BOTTOM_LIMIT_PX = 72
_ACCENT_BAR_WIDTH = 56
_ACCENT_BAR_HEIGHT = 6
_EYEBROW_FONT_PX = 14
_EYEBROW_MAX_CHARS = 24
_TITLE_FONT_PX = 34
#: Body sizes tried in order, regular first. Without a ``minimum_font_px`` the
#: tail is below any readability floor on purpose: the page is flagged either
#: way, and a smaller complete page is the rescue's whole point.
#: ``minimum_font_px`` (the deck's readability floor) removes the sizes below it.
_BODY_FONT_STEPS_PX = (20, 18, 16, 14, 12, 11, 10, 9, 8, 7, 6, 5, 4)
_BODY_COLUMNS = (1, 2, 3)
_COLUMN_GAP_PX = 32
#: The authored placeholder's 1.6 line height. Tighter lines (1.4 was tried)
#: make adjacent glyph boxes intersect, which the renderer measures as
#: ``text_overlap`` on every body line.
_BODY_LINE_HEIGHT = 1.6
_GAP_BAR_TO_LABEL_PX = 24
_GAP_EYEBROW_TO_TITLE_PX = 16
_GAP_TITLE_TO_BODY_PX = 30
_BODY_COLOR = "#0F172A"
_BULLET_PREFIX = "• "
#: What an empty slide shows instead of no visible text at all (the SVG
#: contract rejects a page with none). Not a failure reason: an untitled page
#: shows this the same way an empty document shows a blank-title marker.
_EMPTY_TITLE_PLACEHOLDER = "—"

# CJK/Hangul/fullwidth blocks: treated as one full em wide. Everything else
# (Latin, punctuation, digits) is treated as a narrower ~0.55em average. This
# is the same two-bucket estimate used for both the title budget and the
# bullet-wrapping width below.
_WIDE_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7A3),  # Hangul Syllables
    (0xD7B0, 0xD7FF),  # Hangul Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Halfwidth / Fullwidth Forms
)
_NARROW_EM = 0.55
_WIDE_EM = 1.0


def _char_em(ch: str) -> float:
    code = ord(ch)
    for lo, hi in _WIDE_RANGES:
        if lo <= code <= hi:
            return _WIDE_EM
    return _NARROW_EM


def _text_em(text: str) -> float:
    return sum(_char_em(ch) for ch in text)


def _sizes_at_or_above(steps: Sequence[int], floor: float | None) -> tuple[int, ...]:
    """``steps`` without the sizes below ``floor``; the floor itself ends the list.

    ``None`` keeps every step. A floor above every step leaves just the floor,
    so a complete page is still drawn (it overflows and is flagged, never cut).
    """

    if floor is None:
        return tuple(steps)
    minimum = max(1, math.ceil(floor))
    kept = [size for size in steps if size >= minimum]
    if not kept or kept[-1] > minimum:
        kept.append(minimum)
    return tuple(kept)


_TITLE_LINE_HEIGHT = 1.25
_TITLE_PREFERRED_LINES = 2
_TITLE_SIZE_STEP_PX = 4
_TITLE_MIN_PX = 16


def _title_layout(title: str, *, width_px: float, max_px: int, floor: float | None = None) -> tuple[int, list[str]]:
    """``(font size, lines)`` for a title: two lines at most while a smaller size can do it.

    The title is wrapped at spaces, never cut. The largest size (down by
    ``_TITLE_SIZE_STEP_PX``, no lower than the readability floor) that holds it
    in two lines wins; when none does, the smallest size with every line.
    """

    smallest = max(_TITLE_MIN_PX if max_px >= _TITLE_MIN_PX else max_px, math.ceil(floor or 0))
    smallest = min(smallest, max_px)
    size = max_px
    while True:
        lines = _wrap(title, max_em=max(1.0, width_px / size))
        if len(lines) <= _TITLE_PREFERRED_LINES or size <= smallest:
            return size, lines
        size = max(smallest, size - _TITLE_SIZE_STEP_PX)


def _wrap(text: str, *, max_em: float) -> list[str]:
    """Greedy wrap against a fixed em budget per line, breaking at spaces.

    A word wider than a whole line (a long URL, an unspaced run) is the only
    thing broken mid-word; Korean words are never split across lines otherwise.
    """

    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if _text_em(candidate) <= max_em:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for ch in word:
            if current and _text_em(current + ch) > max_em:
                lines.append(current)
                current = ch
            else:
                current += ch
    lines.append(current)
    return lines


def _text_node(parent: ET.Element, *, x: float, y: float, text: str, font_px: int,
                font_family: str, fill: str, weight: str | None = None) -> None:
    attrib = {
        "x": str(int(round(x))), "y": str(int(round(y))),
        "font-family": font_family, "font-size": str(font_px), "fill": fill,
    }
    if weight is not None:
        attrib["font-weight"] = weight
    node = ET.SubElement(parent, f"{{{SVG_NS}}}text", attrib)
    node.text = text


_HEADING_MARKER = re.compile(r"^#{1,6}(?:\s+|$)")
_QUOTE_MARKER = re.compile(r"^>+\s*")
#: A list marker is a bullet glyph, or ``-``/``*``/``+`` followed by a space, so
#: ``**bold**`` and a leading ``-5%`` keep their characters.
_LIST_MARKER = re.compile(r"^(?:[•·▪●◦]\s*|[-*+]\s+)")
#: Lines that carry no words at all: thematic breaks and code fences.
_DECORATION_LINE = re.compile(r"^(?:[-*_=]{3,}|`{3,}[\w-]*|~{3,}[\w-]*)$")
_LINK = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD_STAR = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDERSCORE = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_HTML_BREAK = re.compile(r"(?i)<br\s*/?>")


def _plain_line(raw: str) -> str:
    """One outline line as the plain text a reader should see.

    Markdown syntax that would render literally on an SVG page is dropped:
    heading and quote markers, list markers (the page draws its own bullet),
    emphasis and code markers, and link syntax (``[label](url)`` keeps both the
    label and the address). Only syntax characters are removed, never words.
    """

    line = _HTML_BREAK.sub(" ", raw).strip()
    if _DECORATION_LINE.match(line):
        return ""
    line = _HEADING_MARKER.sub("", line, count=1).strip()
    line = _QUOTE_MARKER.sub("", line, count=1)
    while True:
        stripped = _LIST_MARKER.sub("", line, count=1)
        if stripped == line:
            break
        line = stripped
    line = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(1).strip() else m.group(2), line)
    for pattern in (_BOLD_STAR, _BOLD_UNDERSCORE, _STRIKE, _ITALIC_STAR, _INLINE_CODE):
        line = pattern.sub(r"\1", line)
    return " ".join(line.split())


def _lines(content: str) -> list[str]:
    """The outline's own lines as plain text, blank and decoration-only lines removed."""

    output: list[str] = []
    for raw in str(content or "").splitlines():
        line = _plain_line(raw)
        if line:
            output.append(line)
    return output


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _complete_split(content: str) -> tuple[str, list[str]]:
    """``(title, body lines)``: every outline line, none shortened or dropped.

    The one line that is not repeated is an exact duplicate of the title
    directly under it: the title already shows those words. The title is never
    cut here; the page wraps it.
    """

    lines = _lines(content)
    if not lines:
        return "", []
    head, rest = lines[0], lines[1:]
    if rest and _same_text(head, rest[0]):
        rest = rest[1:]
    return head, rest


def _body_layout(bullets: Sequence[str], *, available_width: float, available_height: float,
                 minimum_font_px: float | None = None) -> tuple[int, int, list[list[tuple[bool, str]]]]:
    """``(font_px, columns, per-column [(is_first_line, text)])`` holding every line.

    The first configuration (largest size, then fewest columns) whose lines fit
    the body box wins; when none fits, the last one is used and its lines run
    past the box -- still every word, measured by the renderer and flagged.
    """

    choice = None
    for font_px in _sizes_at_or_above(_BODY_FONT_STEPS_PX, minimum_font_px):
        line_height = font_px * _BODY_LINE_HEIGHT
        for columns in _BODY_COLUMNS:
            column_width = (available_width - (columns - 1) * _COLUMN_GAP_PX) / columns
            prefix_width_px = _text_em(_BULLET_PREFIX) * font_px
            max_em = max(1.0, column_width - prefix_width_px) / font_px
            physical = [(position == 0, line) for bullet in bullets
                        for position, line in enumerate(_wrap(bullet, max_em=max_em))]
            per_column = max(1, int((available_height - font_px) // line_height) + 1)
            choice = (font_px, columns, physical, per_column)
            if len(physical) <= per_column * columns:
                break
        else:
            continue
        break
    font_px, columns, physical, per_column = choice
    # Overflowing lines stay in the last column rather than being dropped.
    split = [physical[i * per_column:(i + 1) * per_column] for i in range(columns - 1)]
    split.append(physical[(columns - 1) * per_column:])
    return font_px, columns, split


def _body_top(style_snapshot: dict | None, header_page: dict | None) -> int:
    """Where a rescue page's own content may start.

    A deck with a common header reserves the band above ``content_top_px``
    for the server-rendered header; a rescue page drawn from the canvas top
    would sit under it.
    """

    contract = (style_snapshot or {}).get("common_header")
    if not isinstance(contract, dict) or not isinstance(header_page, dict) or not header_page.get("applied"):
        return _TOP_MARGIN
    content_top = contract.get("content_top_px")
    if isinstance(content_top, (int, float)) and not isinstance(content_top, bool):
        return max(_TOP_MARGIN, int(content_top) + _GAP_TITLE_TO_BODY_PX)
    return _TOP_MARGIN


def _header_owns_title(style_snapshot: dict | None, header_page: dict | None) -> bool:
    """Whether the server-rendered common header already draws this page's title."""

    return (isinstance((style_snapshot or {}).get("common_header"), dict)
            and isinstance(header_page, dict) and header_page.get("applied") is True)


_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
#: Minimum contrast for the accent colour to carry text or the accent bar.
_ACCENT_CONTRAST = 3.0
_COVER_TITLE_FONT_PX = 44
_COVER_SUBTITLE_FONT_PX = 22
_COVER_SUBTITLE_LINES = 3


def _luminance(color: str) -> float:
    """WCAG relative luminance of ``#RRGGBB``."""
    channels = []
    for i in (1, 3, 5):
        value = int(color[i:i + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _page_colors(tokens: Mapping) -> tuple[str, str, str]:
    """(background, text, accent) for a fallback page in the deck's own colours.

    The background is the style's canvas colour; text takes the deck palette
    colour (or near-black / white) with the strongest contrast against it; the
    accent is the style's primary when it is legible there, else the text colour.
    """
    background = tokens.get("background")
    background = background.upper() if isinstance(background, str) and _HEX.fullmatch(background) else "#FFFFFF"
    palette = [color.upper() for color in tokens.get("palette") or () if isinstance(color, str) and _HEX.fullmatch(color)]
    text = max([*palette, _BODY_COLOR, "#FFFFFF"], key=lambda color: (_contrast(color, background), color))
    accent = tokens.get("primary")
    accent = accent.upper() if isinstance(accent, str) and _HEX.fullmatch(accent) else text
    if _contrast(accent, background) < _ACCENT_CONTRAST:
        accent = text
    return background, text, accent


_TABLE_LINE = re.compile(r"^\|.*\|$")
_TABLE_RULE_CELL = re.compile(r"^:?-{2,}:?$")
_TABLE_FONT_STEPS_PX = (18, 16, 14, 12, 11, 10, 9, 8, 7, 6, 5, 4)
_TABLE_MIN_COLUMN_EM = 3.0
#: Share of the text colour mixed into the page colour for the table's row bands.
_TABLE_BAND_MIX = (0.06, 0.12)
_TEXT_CONTRAST = 4.5


def _mix(base: str, over: str, share: float) -> str:
    """``base`` moved ``share`` of the way to ``over`` (both ``#RRGGBB``)."""
    channels = [round(int(base[i:i + 2], 16) * (1 - share) + int(over[i:i + 2], 16) * share) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(channels)


def _ink_on(fill: str, preferred: str) -> str:
    """Text colour for ``fill``: ``preferred`` while it reads (4.5:1), else the stronger of white and near-black."""
    return readable_ink(fill, preferred, dark=_BODY_COLOR)


def _table_rows(lines: Sequence[str]) -> list[list[str]] | None:
    """The cell rows of a run of markdown table lines, or ``None`` when it is not a table.

    The rule row (``| --- | --- |``) is syntax, not text, and is dropped; a
    table needs a header and at least one more row.
    """
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if all(_TABLE_RULE_CELL.match(cell) for cell in cells if cell) and any(cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def _flow_items(bullets: Sequence[str]) -> list:
    """Body lines in order, each run of markdown table lines folded into one row list."""
    items: list = []
    run: list[str] = []

    def close() -> None:
        if not run:
            return
        rows = _table_rows(run)
        items.extend([rows] if rows else run)
        run.clear()

    for line in bullets:
        if _TABLE_LINE.match(line):
            run.append(line)
        else:
            close()
            items.append(line)
    close()
    return items


def _table_columns(rows: Sequence[Sequence[str]], width: float, font_px: int) -> list[float]:
    natural = [max(_TABLE_MIN_COLUMN_EM, *(_text_em(row[column]) for row in rows)) for column in range(len(rows[0]))]
    total = sum(natural)
    return [width * value / total for value in natural]


def _flow_place(items: Sequence, *, width: float, font_px: int) -> tuple[list[tuple], float]:
    """Operations (relative y) and total height for ``items`` set at ``font_px`` in one column."""
    line_height = font_px * _BODY_LINE_HEIGHT
    prefix = _text_em(_BULLET_PREFIX) * font_px
    ops: list[tuple] = []
    cursor = 0.0
    for item in items:
        if isinstance(item, str):
            for position, line in enumerate(_wrap(item, max_em=max(1.0, width - prefix) / font_px)):
                ops.append(("line", cursor + font_px, position == 0, line))
                cursor += line_height
            continue
        cursor += font_px * 0.4
        columns = _table_columns(item, width, font_px)
        pad = max(2.0, font_px * 0.5)
        cells = []
        for index, row in enumerate(item):
            wrapped = [_wrap(text, max_em=max(1.0, column - 2 * pad) / font_px) for text, column in zip(row, columns, strict=True)]
            height = 2 * pad * 0.6 + (max(len(lines) for lines in wrapped) - 1) * line_height + font_px * 1.25
            cells.append((index, cursor, height, wrapped))
            cursor += height
        ops.append(("table", columns, pad, cells))
        cursor += font_px * 0.4
    return ops, cursor


def _draw_flow(group: ET.Element, ops: Sequence[tuple], *, left: float, top: float, width: float, font_px: int,
               font_family: str, text_color: str, accent: str, background: str, weight: str | None,
               bold_weight: str) -> None:
    line_height = font_px * _BODY_LINE_HEIGHT
    prefix = _text_em(_BULLET_PREFIX) * font_px
    for op in ops:
        if op[0] == "line":
            _kind, y, first, line = op
            _text_node(group, x=left if first else left + prefix, y=top + y,
                       text=(_BULLET_PREFIX if first else "") + line, font_px=font_px,
                       font_family=font_family, fill=text_color, weight=weight)
            continue
        _kind, columns, pad, cells = op
        for index, row_top, height, wrapped in cells:
            fill = accent if index == 0 else _mix(background, text_color, _TABLE_BAND_MIX[index % 2])
            ink = _ink_on(fill, text_color)
            ET.SubElement(group, f"{{{SVG_NS}}}rect", {
                "x": _fmt_px(left), "y": _fmt_px(top + row_top), "width": _fmt_px(width),
                "height": _fmt_px(height), "fill": fill})
            x = left
            for lines, column in zip(wrapped, columns, strict=True):
                for number, line in enumerate(lines):
                    _text_node(group, x=x + pad, y=top + row_top + pad * 0.6 + font_px + number * line_height,
                               text=line, font_px=font_px, font_family=font_family, fill=ink,
                               weight=bold_weight if index == 0 else weight)
                x += column


def _draw_body(group: ET.Element, bullets: Sequence[str], *, left: float, top: float, width: float,
               bottom: float, font_family: str, text_color: str, accent: str, background: str,
               weight: str | None = None, bold_weight: str = "700",
               minimum_font_px: float | None = None) -> None:
    """Every body line below ``top`` within ``width``: bullets, or a grid for a markdown table.

    Without a table the lines take the largest size and fewest columns that
    hold them; with one, a single column whose size is the largest that fits.
    """
    items = _flow_items(bullets)
    if any(not isinstance(item, str) for item in items):
        available = max(1.0, bottom - top)
        for font_px in _sizes_at_or_above(_TABLE_FONT_STEPS_PX, minimum_font_px):
            ops, height = _flow_place(items, width=width, font_px=font_px)
            if height <= available:
                break
        _draw_flow(group, ops, left=left, top=top, width=width, font_px=font_px, font_family=font_family,
                   text_color=text_color, accent=accent, background=background, weight=weight,
                   bold_weight=bold_weight)
        return
    font_px, columns, split = _body_layout(bullets, available_width=width, available_height=max(1.0, bottom - top),
                                           minimum_font_px=minimum_font_px)
    column_width = (width - (columns - 1) * _COLUMN_GAP_PX) / columns
    prefix_width_px = _text_em(_BULLET_PREFIX) * font_px
    for column, lines in enumerate(split):
        column_left = left + column * (column_width + _COLUMN_GAP_PX)
        y = top + font_px
        for first, line in lines:
            _text_node(group, x=column_left if first else column_left + prefix_width_px, y=y,
                       text=(_BULLET_PREFIX if first else "") + line, font_px=font_px,
                       font_family=font_family, fill=text_color, weight=weight)
            y += font_px * _BODY_LINE_HEIGHT


def _draw_title(group: ET.Element, title: str, *, left: float, cursor: float, width: float, max_px: int,
                font_family: str, fill: str, weight: str, minimum_font_px: float | None) -> float:
    """The title wrapped at word boundaries, drawn once; returns where the body may start."""

    size, lines = _title_layout(title, width_px=width, max_px=max_px, floor=minimum_font_px)
    for number, line in enumerate(lines):
        _text_node(group, x=left, y=cursor + size + number * size * _TITLE_LINE_HEIGHT, text=line,
                   font_px=size, font_family=font_family, fill=fill, weight=weight)
    return cursor + size + (len(lines) - 1) * size * _TITLE_LINE_HEIGHT + _GAP_TITLE_TO_BODY_PX


def fallback_page_svg(*, content: str, role: str, index: int, count: int,
                       width: int, height: int, style_snapshot: dict | None,
                       header_page: dict | None = None, minimum_font_px: float | None = None) -> str:
    """Deterministic contract-valid SVG for one page, finalized (page number added).

    Same inputs always produce the same bytes: no randomness, no clock, no
    provider or browser call. Raises nothing for any ``content`` -- including
    empty content, very long lines, CJK text, or ``<``/``&``/quote characters
    -- because the whole point of a fallback page is that it always publishes.
    Every outline line appears in full (see the module docstring).
    ``minimum_font_px`` is the deck's readability floor: no text is set below it.
    """

    tokens = (style_snapshot or {}).get("tokens", {})
    background, text_color, accent = _page_colors(tokens)
    from services.svg_trusted_fonts import DEFAULT_FONT_FAMILY
    body_font = tokens.get("body_font") or DEFAULT_FONT_FAMILY
    title_font = tokens.get("heading_font") or body_font

    available_width = max(1, width - 2 * _MARGIN_X)
    title, bullets = _complete_split(content)
    if not title:
        title = _EMPTY_TITLE_PLACEHOLDER
    header_title = _header_owns_title(style_snapshot, header_page)

    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": FALLBACK_GROUP_ID})
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": background,
    })
    top = _body_top(style_snapshot, header_page)
    if str(role or "").strip().lower() == "cover":
        _cover_body(group, title=title, bullets=bullets, top=top, bottom=height - _BOTTOM_LIMIT_PX, left=_MARGIN_X,
                    available_width=available_width, title_font=title_font, body_font=body_font,
                    text_color=text_color, accent=accent, background=background,
                    draw_title=not header_title, minimum_font_px=minimum_font_px)
        raw = ET.tostring(root, encoding="unicode")
        return finalize_svg(raw, index=index, n=count, width=width, height=height,
                            style_snapshot=style_snapshot, header_page=header_page)
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": str(_MARGIN_X), "y": str(top),
        "width": str(_ACCENT_BAR_WIDTH), "height": str(_ACCENT_BAR_HEIGHT), "fill": accent,
    })

    cursor = top + _ACCENT_BAR_HEIGHT + _GAP_BAR_TO_LABEL_PX
    eyebrow = str(role or "").strip().upper()[:_EYEBROW_MAX_CHARS]
    # "content" names the page type, not anything a reader needs.
    if eyebrow and eyebrow != "CONTENT":
        eyebrow_px = max(_EYEBROW_FONT_PX, math.ceil(minimum_font_px or 0))
        _text_node(group, x=_MARGIN_X, y=cursor + eyebrow_px, text=eyebrow,
                   font_px=eyebrow_px, font_family=body_font, fill=accent)
        cursor += eyebrow_px + _GAP_EYEBROW_TO_TITLE_PX

    if header_title:
        cursor += _GAP_EYEBROW_TO_TITLE_PX
    else:
        cursor = _draw_title(group, title, left=_MARGIN_X, cursor=cursor, width=available_width,
                             max_px=_TITLE_FONT_PX, font_family=title_font, fill=text_color, weight="700",
                             minimum_font_px=minimum_font_px)

    if bullets:
        _draw_body(group, bullets, left=_MARGIN_X, top=cursor, width=available_width,
                   bottom=height - _BOTTOM_LIMIT_PX, font_family=body_font, text_color=text_color,
                   accent=accent, background=background, minimum_font_px=minimum_font_px)

    raw = ET.tostring(root, encoding="unicode")
    return finalize_svg(raw, index=index, n=count, width=width, height=height,
                        style_snapshot=style_snapshot, header_page=header_page)


#: Inset from the template's body region so stroke and glyph ink stay inside it.
_TEMPLATE_BODY_INSET_PX = 8


def template_fallback_body_svg(*, content: str, width: int, height: int, body_region: Mapping,
                               title_in_frame: bool, title_face: Mapping, body_face: Mapping,
                               style_snapshot: dict | None, body_background: Mapping | None = None,
                               role: str = "", body_photo: bool = False,
                               minimum_font_px: float | None = None) -> str:
    """Deterministic body for a template-redesign page whose authored body failed.

    A full-canvas, transparent SVG whose only paint lies inside ``body_region``
    (the template frame owns everything outside it). Every outline line is
    kept, like :func:`fallback_page_svg`; the title is left to the frame when
    the frame shows it. ``title_face``/``body_face`` are faces of the frame's
    own font manifest (``family`` and ``weight``), so the composed page keeps
    the template's fonts. Element IDs start with ``body-`` as authored bodies do.

    A cover keeps its cover shape (large title, subtitle lines) inside the
    region. ``body_photo`` says the template paints a picture under the body:
    the text then sits on a translucent panel whose colour is the opposite of
    the text colour, so it reads over the brightest part of the picture.
    """

    tokens = (style_snapshot or {}).get("tokens", {})
    if body_background:
        # The text sits on the template's own paint (a cover photo), not on tokens.background.
        tokens = {**tokens, "background": body_background["mean_color"]}
    _background, text_color, accent = _page_colors(tokens)
    inset = _TEMPLATE_BODY_INSET_PX
    left = float(body_region["x"]) + inset
    top = float(body_region["y"]) + inset
    available_width = max(1.0, float(body_region["width"]) - 2 * inset)
    bottom = float(body_region["y"]) + float(body_region["height"]) - inset
    title_px = min(_TITLE_FONT_PX, max(16, int(float(body_region["height"]) / 8)))
    title, bullets = _complete_split(content)
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "body-fallback"})
    cursor = top
    if str(role or "").strip().lower() == "cover" and not title_in_frame and title:
        _cover_body(group, title=title, bullets=bullets, top=top, bottom=bottom, left=left,
                    available_width=available_width, title_font=str(title_face["family"]),
                    body_font=str(body_face["family"]), text_color=text_color, accent=accent,
                    title_weight=str(title_face["weight"]), body_weight=str(body_face["weight"]),
                    background=tokens.get("background") or "#FFFFFF", minimum_font_px=minimum_font_px)
    else:
        if not title_in_frame and title:
            ET.SubElement(group, f"{{{SVG_NS}}}rect", {
                "id": "body-fallback-accent", "x": _fmt_px(left), "y": _fmt_px(cursor),
                "width": str(_ACCENT_BAR_WIDTH), "height": str(_ACCENT_BAR_HEIGHT), "fill": accent,
            })
            cursor += _ACCENT_BAR_HEIGHT + _GAP_EYEBROW_TO_TITLE_PX
            cursor = _draw_title(group, title, left=left, cursor=cursor, width=available_width, max_px=title_px,
                                 font_family=str(title_face["family"]), fill=text_color,
                                 weight=str(title_face["weight"]), minimum_font_px=minimum_font_px)
        if bullets:
            _draw_body(group, bullets, left=left, top=cursor, width=available_width, bottom=bottom,
                       font_family=str(body_face["family"]), text_color=text_color, accent=accent,
                       background=tokens.get("background") or "#FFFFFF", weight=str(body_face["weight"]),
                       bold_weight=str(title_face["weight"]), minimum_font_px=minimum_font_px)
    if body_photo:
        _scrim(group, text_color=text_color, region=body_region, left=left, width=available_width)
    for number, node in enumerate(node for node in group.iter() if node.tag.endswith("}rect") and not node.get("id")):
        node.set("id", f"body-fallback-shape-{number}")
    for number, node in enumerate(node for node in group.iter() if node.tag.endswith("}text")):
        node.set("id", f"body-fallback-text-{number}")
    return ET.tostring(root, encoding="unicode")


_SCRIM_PAD_PX = 20
_SCRIM_OPACITY_FOR_LIGHT_TEXT = 0.62
_SCRIM_OPACITY_FOR_DARK_TEXT = 0.72


def _scrim(group: ET.Element, *, text_color: str, region: Mapping, left: float, width: float) -> None:
    """A translucent panel behind the text already drawn in ``group``, opposite in tone to the text.

    Even the brightest (or darkest) pixel of a picture under it stays at about
    4.5:1 or better against the text. Bounded to the body region.
    """
    nodes = [node for node in group.iter() if node.tag.endswith("}text")]
    if not nodes:
        return
    ink_light = _contrast(text_color, "#000000") > _contrast(text_color, "#FFFFFF")
    first = min(float(node.get("y")) - float(node.get("font-size")) for node in nodes)
    last = max(float(node.get("y")) + float(node.get("font-size")) * 0.4 for node in nodes)
    box_top, box_bottom = float(region["y"]), float(region["y"]) + float(region["height"])
    box_left, box_right = float(region["x"]), float(region["x"]) + float(region["width"])
    y = max(box_top, first - _SCRIM_PAD_PX)
    x = max(box_left, left - _SCRIM_PAD_PX / 2)
    panel = ET.Element(f"{{{SVG_NS}}}rect", {
        "id": "body-fallback-scrim", "x": _fmt_px(x), "y": _fmt_px(y),
        "width": _fmt_px(max(1.0, min(box_right, left + width + _SCRIM_PAD_PX / 2) - x)),
        "height": _fmt_px(max(1.0, min(box_bottom, last + _SCRIM_PAD_PX) - y)),
        "fill": "#000000" if ink_light else "#FFFFFF",
        "fill-opacity": str(_SCRIM_OPACITY_FOR_LIGHT_TEXT if ink_light else _SCRIM_OPACITY_FOR_DARK_TEXT)})
    group.insert(0, panel)


def _cover_body(group: ET.Element, *, title: str, bullets: Sequence[str], top: float, bottom: float, left: float,
                available_width: float, title_font: str, body_font: str, text_color: str, accent: str,
                title_weight: str = "700", body_weight: str | None = None, background: str = "#FFFFFF",
                draw_title: bool = True, minimum_font_px: float | None = None) -> None:
    """A cover-shaped fallback: a large title in the middle band, the first lines as subtitle.

    Every outline line still appears: lines past the subtitle allowance are set
    smaller below it, so the page never drops content. The block is centred
    between ``top`` and ``bottom`` and starts no higher than ``top``.
    """
    title_px = max(16, min(_COVER_TITLE_FONT_PX, int((bottom - top) / 6)))
    floor_px = math.ceil(minimum_font_px or 0)
    subtitle_px = max(12, floor_px, min(_COVER_SUBTITLE_FONT_PX, int(title_px / 2)))
    if draw_title:
        title_px, title_lines = _title_layout(title, width_px=available_width, max_px=title_px, floor=minimum_font_px)
    else:
        title_lines = []
    subtitle = list(bullets[:_COVER_SUBTITLE_LINES])
    rest = list(bullets[_COVER_SUBTITLE_LINES:])
    block = (len(title_lines) * title_px * _TITLE_LINE_HEIGHT + _ACCENT_BAR_HEIGHT + _GAP_BAR_TO_LABEL_PX
             + len(subtitle) * subtitle_px * 1.5)
    cursor = max(top, top + (bottom - top - block) / 2 - title_px)
    for line in title_lines:
        cursor += title_px * _TITLE_LINE_HEIGHT
        _text_node(group, x=left, y=cursor, text=line, font_px=title_px,
                   font_family=title_font, fill=text_color, weight=title_weight)
    cursor += _GAP_BAR_TO_LABEL_PX
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": _fmt_px(left), "y": _fmt_px(cursor), "width": str(_ACCENT_BAR_WIDTH * 2),
        "height": str(_ACCENT_BAR_HEIGHT), "fill": accent,
    })
    cursor += _ACCENT_BAR_HEIGHT
    for line in subtitle:
        for part in _wrap(line, max_em=max(1.0, available_width / subtitle_px)):
            cursor += subtitle_px * 1.5
            _text_node(group, x=left, y=cursor, text=part, font_px=subtitle_px,
                       font_family=body_font, fill=text_color, weight=body_weight)
    if rest:
        _draw_body(group, rest, left=left, top=cursor + _GAP_TITLE_TO_BODY_PX, width=available_width,
                   bottom=bottom, font_family=body_font, text_color=text_color, accent=accent,
                   background=background, weight=body_weight, minimum_font_px=minimum_font_px)


def _fmt_px(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def placeholder_page_svg(*, title: str, width: int, height: int, font_family: str,
                         index: int | None = None, count: int | None = None,
                         style_snapshot: dict | None = None, header_page: dict | None = None,
                         minimum_font_px: float | None = None) -> str:
    """The last-resort page: the slide's title (or "—") on the deck's own page colour.

    Published only when no complete-content page could be rendered; the flag
    on the page, not its bytes, tells the reader what is missing. With
    ``index``/``count`` the page is finalized (page number) like
    :func:`fallback_page_svg`; without them it is validated as-is (a source
    deck carries no system page number).
    """

    from services.svg_contract import validate_svg

    available_width = max(1, width - 2 * _MARGIN_X)
    lines = _lines(title)
    text = lines[0] if lines else _EMPTY_TITLE_PLACEHOLDER
    tokens = (style_snapshot or {}).get("tokens", {})
    background, text_color, _accent = _page_colors(tokens)
    font_family = tokens.get("heading_font") or tokens.get("body_font") or font_family
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": FALLBACK_GROUP_ID})
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": background,
    })
    top = _body_top(style_snapshot, header_page)
    # The placeholder's only content is this title, so it is drawn even under an
    # applied header: the page keeps a visible body text (and the author-facing
    # repair view, which strips the header, still shows what the slide was).
    _draw_title(group, text, left=_MARGIN_X, cursor=top, width=available_width, max_px=_TITLE_FONT_PX,
                font_family=font_family, fill=text_color, weight="700", minimum_font_px=minimum_font_px)
    raw = ET.tostring(root, encoding="unicode")
    if index is not None and count is not None:
        return finalize_svg(raw, index=index, n=count, width=width, height=height,
                            style_snapshot=style_snapshot, header_page=header_page)
    validate_svg(raw, width=width, height=height, allow_system_page_number=False, source_manifest={})
    return raw


def _recovery_content_profile(content: str | Mapping) -> dict | None:
    """Describe existing content only; this is not a layout or page classifier."""
    from services.generation_core.drivers.svg.native_requirements import requested_native_data

    # Source members are not explanatory groups. A plain outline cannot prove
    # whether its asset offer has images, so unknown membership is excluded.
    if not isinstance(content, Mapping):
        return None
    text = content.get("text")
    image_members, groups = content.get("image_member_count"), content.get("image_groups")
    if (type(image_members) is not int or image_members < 0 or not isinstance(groups, list)
            or any(not isinstance(group, list) or not group
                   or any(not isinstance(member, str) or not member for member in group) for group in groups)):
        return None
    membership = [member for group in groups for member in group]
    if len(membership) != image_members or len(set(membership)) != image_members:
        return None
    image_groups = len(groups)
    if "image_group_count" in content and content["image_group_count"] != image_groups:
        return None
    if not isinstance(text, str):
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    requirements = requested_native_data(text)
    tables, charts = requirements.get("tables", []), requirements.get("charts", [])
    if any(chart.get("unsupportedReason") for chart in charts):
        return None
    return {
        "content_chars": len(text), "nonempty_lines": len(lines),
        "title_chars": len(lines[0]) if lines else 0, "image_group_count": image_groups,
        "image_member_count": image_members, "image_group_shapes": sorted(len(group) for group in groups),
        "table_shapes": [[len(table["rows"]), len(table["rows"][0])] for table in tables],
        "chart_shapes": [[len(chart["categories"]), len(chart.get("series", [chart]))] for chart in charts],
    }


def validated_recovery_candidates(snapshot: Mapping | None, content: str | Mapping, canvas: object, *,
                                  current_validation_context: Mapping | None = None,
                                  allowed_source_evidence=None) -> list[dict]:
    """Return only current-condition, content-compatible, quality-qualified SVGs.

    The snapshot is the server's trusted request pin, not a public payload. Missing
    real material or independently captured runtime conditions means no recovery.
    The adapted result still needs all normal source, render and quality checks.
    """
    from services.svg_authoring_profile import validate_quality_recovery_policy
    from utils.authored_style_svg_recovery import (recovery_candidate_valid, recovery_style_identity,
        recovery_source_authorized, recovery_digest)

    if not isinstance(snapshot, Mapping):
        return []
    try:
        policy = validate_quality_recovery_policy(snapshot.get("quality_recovery_policy"))
    except ValueError:
        return []
    if policy is None:
        return []
    # The server captures these current fingerprints independently of candidates.
    # An enrolled receipt cannot supply its own runtime compatibility witness.
    conditions = current_validation_context
    if not isinstance(conditions, Mapping) or conditions.get("contract_version") != SVG_CONTRACT_VERSION:
        return []
    if isinstance(canvas, Mapping):
        width, height = canvas.get("width_px", canvas.get("width")), canvas.get("height_px", canvas.get("height"))
    else:
        width, height = getattr(canvas, "width_px", None), getattr(canvas, "height_px", None)
    if type(width) is not int or type(height) is not int:
        return []
    profile = _recovery_content_profile(content)
    if profile is None:
        return []
    from services.generation_core.drivers.svg.native_requirements import same_requirements_as_v2
    # Enrolled scopes digest the v2 requirements; a deck on a later revision may reuse a
    # candidate only for content whose requirements that revision reads identically.
    if not same_requirements_as_v2(content["text"], snapshot):
        return []
    raw = snapshot.get("validated_recovery_candidates", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    identity, candidates = recovery_style_identity(snapshot), []
    for candidate in raw:
        if not recovery_source_authorized(candidate, allowed_source_evidence) or not recovery_candidate_valid(candidate):
            continue
        if (candidate["style_id"] != snapshot.get("style_id")
                or candidate["style_identity_digest"] != identity
                or candidate["canvas"] != {"width": width, "height": height}):
            continue
        evidence = candidate["verification"]
        if any(not conditions.get(key) or evidence.get(key) != conditions[key]
               for key in ("renderer_digest", "fonts_digest", "runtime_digest")):
            continue
        fit = candidate["content_fit"]
        if any(not fit[key][0] <= profile[key] <= fit[key][1]
               for key in ("content_chars", "nonempty_lines", "title_chars", "image_group_count", "image_member_count")):
            continue
        if any(profile[key] != fit[key] for key in ("table_shapes", "chart_shapes", "image_group_shapes")):
            continue
        from services.generation_core.drivers.svg.native_requirements import requested_native_data
        from services.svg_contract import source_digest
        scope = {"content_digest": source_digest(content["text"]), "profile_digest": recovery_digest(profile),
            "native_requirements_digest": recovery_digest(requested_native_data(content["text"])),
            "image_groups_digest": recovery_digest(sorted(sorted(group) for group in content["image_groups"]))}
        if scope not in candidate["tested_content_scopes"]:
            continue
        value = copy.deepcopy(candidate)
        value["current_content_profile"] = profile
        candidates.append(value)
    return sorted(candidates, key=lambda candidate: (candidate["id"], candidate["revision"]))


def select_validated_recovery_candidate(candidates: Sequence[Mapping], *, recent_pages: Sequence = (),
                                      recent_candidate_ids: Sequence = (), allowed_source_evidence=None) -> dict | None:
    """Prefer less repeated actual geometry inside the already qualified pool.

    Reservations are made by the caller in page-index order before provider I/O.
    Geometry similarity is an observation, never a design-quality pass score.
    """
    from services.generation_core.drivers.svg.composition_observation import _geometry
    from utils.authored_style_svg_recovery import recovery_candidate_valid, recovery_source_authorized

    geometries = []
    for page in recent_pages:
        if isinstance(page, tuple) and page:
            page = page[0]
        svg = page.get("svg_content", "") if isinstance(page, Mapping) else page
        if isinstance(svg, str):
            geometry = _geometry(svg)
            if geometry is not None and geometry[1] >= 3:
                geometries.append(geometry[0])
    uses = Counter(item for item in recent_candidate_ids if isinstance(item, str))
    ranked = []
    for candidate in candidates:
        if not recovery_source_authorized(candidate, allowed_source_evidence) or not recovery_candidate_valid(candidate):
            continue
        geometry = _geometry(candidate["svg_content"])
        if geometry is None:
            continue
        features, similarities = geometry[0], []
        for other in geometries:
            keys = features.keys() | other.keys()
            denominator = sum(max(features[key], other[key]) for key in keys)
            similarities.append(sum(min(features[key], other[key]) for key in keys) / max(1, denominator))
        similarity = max(similarities, default=0.0)
        count = uses[candidate["id"]]
        ranked.append(((similarity, count, candidate["id"], candidate["revision"]), candidate, geometry[2]))
    if not ranked:
        return None
    rank, selected, geometry_digest = min(ranked, key=lambda item: item[0])
    result = copy.deepcopy(selected)
    result["selection_evidence"] = {
        "candidate_id": result["id"], "candidate_revision": result["revision"],
        "qualified_pool_size": len(ranked), "single_qualified_candidate": len(ranked) == 1,
        "geometry_digest": geometry_digest, "maximum_observed_similarity": rank[0],
        "recent_use_count": rank[1], "compared_page_count": len(geometries),
        "selection_order": "page-index-reservation",
    }
    return result


__all__ = [
    "FALLBACK_GROUP_ID",
    "fallback_page_svg",
    "placeholder_page_svg",
    "validated_recovery_candidates",
    "select_validated_recovery_candidate",
]
