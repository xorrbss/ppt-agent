"""Typography floor for authored HTML shown in previews and exported to PPTX.

Authored slides use a 1280x720 CSS-pixel canvas. 12px is the screen equivalent
of the exporter's 9pt editable-text floor, so ordinary authored output should
not persist explicit text sizes below that value. Exact reconstruction bypasses
this policy at the call site because source fidelity is its primary contract.
"""

from __future__ import annotations

import re
import math

from models.authored_vnext.capability_registry import (
    AUTHORED_FONT_FLOOR_PT,
    AUTHORED_FONT_FLOOR_PX,
)

MIN_AUTHORED_FONT_SIZE_PX = AUTHORED_FONT_FLOOR_PX
MIN_AUTHORED_FONT_SIZE_PT = AUTHORED_FONT_FLOOR_PT


def requested_font_floor_px(instructions: str | None, *, point_per_unit: float = 0.75) -> float | None:
    """Read explicit, unit-bearing typography values from the user's request only.

    This is permission to preserve a requested size, not a replacement for the
    author's interpretation of which typography role the request addresses.
    Never pass extracted source documents or generated HTML here.
    """
    if not math.isfinite(point_per_unit) or point_per_unit <= 0:
        raise ValueError("document_canvas_point_scale_invalid")
    values = []
    pattern = (
        r"(?:본문|기본|전체|글자|글씨|글짜|폰트|body|base|default|font)"
        r"(?:\s|글자|글씨|글짜|폰트|크기|사이즈|기본|본문|size|font|text|"
        r"를|을|은|는|의|로|:|=|-)*"
        r"(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>px|pt|픽셀|포인트)"
    )
    for match in re.finditer(pattern, instructions or "", re.IGNORECASE):
        # A rejected alternative is not an explicit size selection.
        prefix = (instructions or "")[max(0, match.start() - 24):match.start()]
        if re.search(r"(?:do\s+not|don't|avoid|not)\s+(?:use\s+)?$", prefix, re.IGNORECASE):
            continue
        tail = (instructions or "")[match.end():match.end() + 20]
        if re.match(r"\s*(?:가?\s*아니|말고|대신|로?\s*하지|로?\s*하지마)", tail):
            continue
        value = float(match.group("size"))
        if match.group("unit").lower() in {"pt", "포인트"}:
            value /= point_per_unit
        if math.isfinite(value) and value > 0:
            values.append(value)
    return min(values) if values else None

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


def _clamp_css_font_sizes(
    css: str,
    *,
    minimum_px: float = MIN_AUTHORED_FONT_SIZE_PX,
    minimum_pt: float = MIN_AUTHORED_FONT_SIZE_PT,
) -> str:
    def replace(match: re.Match[str]) -> str:
        value = float(match.group("value"))
        unit = match.group("unit")
        floor = minimum_px if unit.lower() == "px" else minimum_pt
        # Zero is commonly used on non-text layout wrappers. It is not visible text,
        # so changing it could introduce whitespace or disturb icon-only constructs.
        if value <= 0 or value >= floor:
            return match.group(0)
        floor_text = str(int(floor)) if floor.is_integer() else str(floor)
        return f'{match.group("prefix")}{floor_text}{unit}'

    return _FONT_SIZE_RE.sub(replace, css)


def _clamp_markup_fragment(
    fragment: str,
    *,
    minimum_px: float,
    minimum_pt: float,
) -> str:
    """Clamp style blocks and inline style attributes without parsing HTML text."""
    output: list[str] = []
    cursor = 0
    for match in _STYLE_BLOCK_RE.finditer(fragment):
        before = fragment[cursor : match.start()]
        output.append(
            _INLINE_STYLE_RE.sub(
                lambda item: (
                    f'{item.group("prefix")}{item.group("quote")}'
                    f'{_clamp_css_font_sizes(item.group("css"), minimum_px=minimum_px, minimum_pt=minimum_pt)}'
                    f'{item.group("quote")}'
                ),
                before,
            )
        )
        output.append(
            f'{match.group("open")}'
            f'{_clamp_css_font_sizes(match.group("css"), minimum_px=minimum_px, minimum_pt=minimum_pt)}'
            f'{match.group("close")}'
        )
        cursor = match.end()

    output.append(
        _INLINE_STYLE_RE.sub(
            lambda item: (
                f'{item.group("prefix")}{item.group("quote")}'
                f'{_clamp_css_font_sizes(item.group("css"), minimum_px=minimum_px, minimum_pt=minimum_pt)}'
                f'{item.group("quote")}'
            ),
            fragment[cursor:],
        )
    )
    return "".join(output)


def enforce_authored_font_floor(
    html: str,
    *,
    minimum_px: float = MIN_AUTHORED_FONT_SIZE_PX,
    minimum_pt: float | None = None,
    instructions: str | None = None,
) -> str:
    """Raise explicit positive CSS font sizes below the requested floor.

    Script bodies are preserved byte-for-byte so CSS-looking strings in JavaScript
    are never rewritten. The default remains the 12px/9pt capability floor;
    generation may pass a stricter frozen slide-capacity floor before Chromium.
    An explicit user typography size takes priority over those default floors.
    The transformation is deterministic and idempotent.
    """
    if not html:
        return html

    requested_px = requested_font_floor_px(instructions)
    if requested_px is not None:
        minimum_px = min(float(minimum_px), requested_px)
        minimum_pt = min(
            minimum_px * 0.75 if minimum_pt is None else float(minimum_pt),
            requested_px * 0.75,
        )
    else:
        minimum_px = max(float(minimum_px), MIN_AUTHORED_FONT_SIZE_PX)
        minimum_pt = max(
            MIN_AUTHORED_FONT_SIZE_PT,
            minimum_px * 0.75 if minimum_pt is None else float(minimum_pt),
        )

    parts = _SCRIPT_BLOCK_RE.split(html)
    return "".join(
        part
        if index % 2
        else _clamp_markup_fragment(
            part,
            minimum_px=minimum_px,
            minimum_pt=minimum_pt,
        )
        for index, part in enumerate(parts)
    )
