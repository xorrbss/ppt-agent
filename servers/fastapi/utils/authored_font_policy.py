"""Typography floor for authored HTML shown in previews and exported to PPTX.

Authored slides use a 1280x720 CSS-pixel canvas. 12px is the screen equivalent
of the exporter's 9pt editable-text floor, so ordinary authored output should
not persist explicit text sizes below that value. Exact reconstruction bypasses
this policy at the call site because source fidelity is its primary contract.
"""

from __future__ import annotations

import re

from services.authored_vnext.capability_registry import (
    AUTHORED_FONT_FLOOR_PT,
    AUTHORED_FONT_FLOOR_PX,
)

MIN_AUTHORED_FONT_SIZE_PX = AUTHORED_FONT_FLOOR_PX
MIN_AUTHORED_FONT_SIZE_PT = AUTHORED_FONT_FLOOR_PT

_SCRIPT_BLOCK_RE = re.compile(
    r"(<script\b[^>]*>.*?</script\s*>)", re.IGNORECASE | re.DOTALL
)
_STYLE_BLOCK_RE = re.compile(
    r"(?P<open><style\b[^>]*>)(?P<css>.*?)(?P<close></style\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_STYLE_RE = re.compile(
    r"(?P<prefix>\bstyle\s*=\s*)(?P<quote>['\"])(?P<css>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_FONT_SIZE_RE = re.compile(
    r"(?P<prefix>(?<![-\w])font-size\s*:\s*)"
    r"(?P<value>(?:\d+(?:\.\d+)?|\.\d+))"
    r"(?P<unit>px|pt)\b",
    re.IGNORECASE,
)


def _clamp_css_font_sizes(css: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = float(match.group("value"))
        unit = match.group("unit")
        floor = (
            MIN_AUTHORED_FONT_SIZE_PX
            if unit.lower() == "px"
            else MIN_AUTHORED_FONT_SIZE_PT
        )
        # Zero is commonly used on non-text layout wrappers. It is not visible text,
        # so changing it could introduce whitespace or disturb icon-only constructs.
        if value <= 0 or value >= floor:
            return match.group(0)
        floor_text = str(int(floor)) if floor.is_integer() else str(floor)
        return f'{match.group("prefix")}{floor_text}{unit}'

    return _FONT_SIZE_RE.sub(replace, css)


def _clamp_markup_fragment(fragment: str) -> str:
    """Clamp style blocks and inline style attributes without parsing HTML text."""
    output: list[str] = []
    cursor = 0
    for match in _STYLE_BLOCK_RE.finditer(fragment):
        before = fragment[cursor : match.start()]
        output.append(
            _INLINE_STYLE_RE.sub(
                lambda item: (
                    f'{item.group("prefix")}{item.group("quote")}'
                    f'{_clamp_css_font_sizes(item.group("css"))}'
                    f'{item.group("quote")}'
                ),
                before,
            )
        )
        output.append(
            f'{match.group("open")}'
            f'{_clamp_css_font_sizes(match.group("css"))}'
            f'{match.group("close")}'
        )
        cursor = match.end()

    output.append(
        _INLINE_STYLE_RE.sub(
            lambda item: (
                f'{item.group("prefix")}{item.group("quote")}'
                f'{_clamp_css_font_sizes(item.group("css"))}'
                f'{item.group("quote")}'
            ),
            fragment[cursor:],
        )
    )
    return "".join(output)


def enforce_authored_font_floor(html: str) -> str:
    """Raise explicit positive CSS font sizes below 12px/9pt.

    Script bodies are preserved byte-for-byte so CSS-looking strings in JavaScript
    are never rewritten. The transformation is deterministic and idempotent.
    """
    if not html:
        return html

    parts = _SCRIPT_BLOCK_RE.split(html)
    return "".join(
        part if index % 2 else _clamp_markup_fragment(part)
        for index, part in enumerate(parts)
    )
