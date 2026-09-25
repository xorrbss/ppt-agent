"""The deterministic page an SVG deck publishes when a page is out of attempts.

This is the SVG driver's counterpart to the authored driver's placeholder page
(``services/authored_author_stage_placeholder.py``): the page the terminal
policy ``fallback`` (``services/generation_core/quality/terminal_policy.py``)
selects once a slide's SVG has failed the contract on both ordinary attempts.
The deck still has to publish, so this page reuses the authored placeholder's
outline split (``_split_content``) rather than re-inventing how a bullet list
is derived from a slide's own content -- the two drivers should show the same
words for the same slide, only in a different markup family.

Nothing here calls a model provider or a browser: the same inputs always
produce the same bytes, so a flagged deck is reproducible from its receipt.
Layout is computed directly against the SVG contract's static subset (no DOM,
no font metrics service) using a fixed, deterministic character-width
estimate, because that subset has no text wrapping or reflow of its own.

The page carries no failure reason or error code -- that lives in receipts,
not in customer-facing content. It only ever shows the slide's own outline
text, truncated the same way the authored placeholder truncates it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from services.authored_author_stage_placeholder import (
    MAX_BODY_BULLETS,
    MAX_BULLET_CHARS,
    _split_content,
)
from services.svg_contract import SVG_NS, finalize_svg

FALLBACK_GROUP_ID = "svg-fallback-page"

_MARGIN_X = 64
_TOP_MARGIN = 64
_BOTTOM_LIMIT_PX = 72
_ACCENT_BAR_WIDTH = 56
_ACCENT_BAR_HEIGHT = 6
_EYEBROW_FONT_PX = 14
_EYEBROW_MAX_CHARS = 24
_TITLE_FONT_PX = 34
_BODY_FONT_PX = 20
#: The authored placeholder's 1.6 line height at 20px. Tighter lines (28px was
#: tried) make adjacent glyph boxes intersect, which the renderer measures as
#: ``text_overlap`` on every body line.
_BODY_LINE_HEIGHT_PX = 32
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


def _title_budget(*, available_width_px: float, font_px: float) -> int:
    """Characters a title keeps, using the conservative (all-wide-char) width.

    This never overestimates how much fits: even an all-Hangul/CJK title is
    guaranteed to fit the available width at this length.
    """

    return max(12, int(available_width_px / (font_px * _WIDE_EM)))


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


def fallback_page_svg(*, content: str, role: str, index: int, count: int,
                       width: int, height: int, style_snapshot: dict | None) -> str:
    """Deterministic contract-valid SVG for one page, finalized (page number added).

    Same inputs always produce the same bytes: no randomness, no clock, no
    provider or browser call. Raises nothing for any ``content`` -- including
    empty content, very long lines, CJK text, or ``<``/``&``/quote characters
    -- because the whole point of a fallback page is that it always publishes.
    """

    tokens = (style_snapshot or {}).get("tokens", {})
    accent = tokens.get("primary") or "#2563EB"
    body_font = tokens.get("body_font") or "Noto Sans KR"
    title_font = tokens.get("heading_font") or body_font

    available_width = max(1, width - 2 * _MARGIN_X)
    title_budget = _title_budget(available_width_px=available_width, font_px=_TITLE_FONT_PX)
    title, bullets = _split_content(content, title_budget=title_budget)
    if not title:
        title = _EMPTY_TITLE_PLACEHOLDER

    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": FALLBACK_GROUP_ID})
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#FFFFFF",
    })
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": str(_MARGIN_X), "y": str(_TOP_MARGIN),
        "width": str(_ACCENT_BAR_WIDTH), "height": str(_ACCENT_BAR_HEIGHT), "fill": accent,
    })

    cursor = _TOP_MARGIN + _ACCENT_BAR_HEIGHT + _GAP_BAR_TO_LABEL_PX
    eyebrow = str(role or "").strip().upper()[:_EYEBROW_MAX_CHARS]
    if eyebrow:
        _text_node(group, x=_MARGIN_X, y=cursor + _EYEBROW_FONT_PX, text=eyebrow,
                   font_px=_EYEBROW_FONT_PX, font_family=body_font, fill=accent)
        cursor += _EYEBROW_FONT_PX + _GAP_EYEBROW_TO_TITLE_PX

    _text_node(group, x=_MARGIN_X, y=cursor + _TITLE_FONT_PX, text=title,
               font_px=_TITLE_FONT_PX, font_family=title_font, fill=_BODY_COLOR, weight="700")
    cursor += _TITLE_FONT_PX + _GAP_TITLE_TO_BODY_PX

    prefix_width_px = _text_em(_BULLET_PREFIX) * _BODY_FONT_PX
    content_width_px = max(1.0, available_width - prefix_width_px)
    max_em = content_width_px / _BODY_FONT_PX
    continuation_x = _MARGIN_X + prefix_width_px

    max_y = height - _BOTTOM_LIMIT_PX
    emitted: list[tuple[float, float, str]] = []
    truncated = False
    y = cursor + _BODY_FONT_PX
    for bullet in bullets:
        for position, physical_line in enumerate(_wrap(bullet, max_em=max_em)):
            if y > max_y:
                truncated = True
                break
            text = (_BULLET_PREFIX if position == 0 else "") + physical_line
            x = _MARGIN_X if position == 0 else continuation_x
            emitted.append((x, y, text))
            y += _BODY_LINE_HEIGHT_PX
        if truncated:
            break
    if truncated and emitted:
        x, y_last, text = emitted[-1]
        emitted[-1] = (x, y_last, text.rstrip("…").rstrip() + "…")

    for x, y_pos, text in emitted:
        _text_node(group, x=x, y=y_pos, text=text, font_px=_BODY_FONT_PX,
                   font_family=body_font, fill=_BODY_COLOR)

    raw = ET.tostring(root, encoding="unicode")
    return finalize_svg(raw, index=index, n=count, width=width, height=height,
                        style_snapshot=style_snapshot)


__all__ = [
    "FALLBACK_GROUP_ID",
    "MAX_BODY_BULLETS",
    "MAX_BULLET_CHARS",
    "fallback_page_svg",
]
