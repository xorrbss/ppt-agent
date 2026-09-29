"""Copy that runs into the page-number footer safe zone.

Bench run 33 (P0, ``c907f094a``, 2026-09-05, slide 2/page 3) published a
"검증" callout whose text printed under the page number "03": every existing
check compares an element to its own painted card or the canvas edges, never
to the footer safe line ``author_slide_rules`` states (content ends at
y<=648 numbered, y<=688 unnumbered). Both text owners' ink stayed *inside*
their own card, which itself ran past that line, so ``text_escapes_card``
missed it too.

Nothing threads ``page_number_mode`` into ``inspect_authored_dom`` today, so
this module finds the page-number element by its own rendered geometry --
a full-width box pinned near the bottom, ~12px type, a short digit run --
and falls back to the deeper unnumbered line when none is found, which also
covers the page-number-less cover slide for free.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from utils.coerce import finite_float

#: Registered as a blocking safety category (``authored_dom_text_escape``)
#: and a magnitude category (``authored_dom_fit_magnitude``) elsewhere.
FOOTER_ZONE_CATEGORY = "text_intrudes_footer_zone"

#: Same sub-pixel allowance ``authored_dom_text_escape`` grants a line box.
FOOTER_ZONE_TOLERANCE_PX = 2.0

#: The two bottom content lines ``author_slide_rules`` states: 72px footer on
#: a numbered deck, 32px margin on an unnumbered one.
NUMBERED_CONTENT_BOTTOM_PX = 648.0
UNNUMBERED_CONTENT_BOTTOM_PX = 688.0

_PAGE_NUMBER_TEXT = re.compile(r"^\d{1,3}$")
_PAGE_NUMBER_MAX_FONT_PX = 14.0
_PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX = 40.0
_PAGE_NUMBER_MIN_TOP_FRACTION = 0.75


@dataclass(frozen=True)
class FooterZoneIntrusion:
    """One text owner whose glyph ink prints past the deck's footer safe line."""

    qid: str
    page_number_qid: str
    content_bottom_px: float
    ink_bottom_px: float
    intrusion_px: float
    intersects_page_number: bool


def _number(value: object, default: float = 0.0) -> float:
    return finite_float(value, default)


def _edges(box: object) -> tuple[float, float, float, float] | None:
    """``(left, top, right, bottom)`` of a metrics bbox, or ``None``."""
    if not isinstance(box, Mapping):
        return None
    left = _number(box.get("x", box.get("left")))
    top = _number(box.get("y", box.get("top")))
    width = _number(box.get("width"))
    height = _number(box.get("height"))
    if width <= 0 or height <= 0:
        return None
    return left, top, left + width, top + height


def _looks_like_page_number(
    item: Mapping[str, Any], *, viewport_width: float, viewport_height: float, max_font_px: float | None = None
) -> bool:
    """Full-width box pinned near the canvas bottom with short digit text.

    No qid/class names this role consistently (run 33 alone carries
    ``s2-page``, ``s4.page``, ``s7-page``, ``s18-page``, ``s06.page``).
    """
    if max_font_px is None:
        # Lazy: the gate module imports this one. The clamp raises the 12px
        # page number to the gate floor, so the threshold must follow it.
        from utils.authored_fragmentation_gate import page_number_max_font_px

        max_font_px = page_number_max_font_px()
    edges = _edges(item.get("bbox"))
    if edges is None:
        return False
    left, top, right, bottom = edges
    keys = ("rendered_text", "text", "text_content")
    text = next((str(item.get(k)).strip() for k in keys if item.get(k)), "")
    return (
        left <= 2.0
        and right >= viewport_width - 4.0
        and 0.0 <= viewport_height - bottom <= _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX
        and top >= viewport_height * _PAGE_NUMBER_MIN_TOP_FRACTION
        and 0.0 < _number(item.get("font_size")) <= max_font_px
        and bool(_PAGE_NUMBER_TEXT.match(text))
    )


def _ink_bottom(item: Mapping[str, Any]) -> float | None:
    """Lowest glyph ink of a text owner, half-leading removed (a line rect's
    raw bottom sits half the leading below the last descender)."""
    fragments = item.get("text_fragments")
    if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
        return None
    font_size = _number(item.get("font_size"))
    bottoms: list[float] = []
    for fragment in fragments:
        edges = _edges(fragment)
        if edges is None:
            continue
        height = edges[3] - edges[1]
        bottoms.append(edges[3] - max(0.0, (height - font_size) / 2.0))
    return max(bottoms) if bottoms else None


def _boxes_intersect(first: object, second: object) -> bool:
    a, b = _edges(first), _edges(second)
    if a is None or b is None:
        return False
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def find_footer_zone_intrusions(
    elements: Iterable[Mapping[str, Any]], *, viewport_width: float, viewport_height: float
) -> tuple[FooterZoneIntrusion, ...]:
    """Every text owner whose glyph ink prints past the deck's footer line."""
    items = [item for item in elements if isinstance(item, Mapping)]
    vw, vh = viewport_width, viewport_height
    page_number = next(
        (i for i in items if _looks_like_page_number(i, viewport_width=vw, viewport_height=vh)), None
    )
    content_bottom = vh - (
        720.0 - (
            NUMBERED_CONTENT_BOTTOM_PX
            if page_number is not None
            else UNNUMBERED_CONTENT_BOTTOM_PX
        )
    )
    page_number_qid = str(page_number.get("qid") or "") if page_number is not None else ""
    page_number_box = page_number.get("bbox") if page_number is not None else None

    intrusions: list[FooterZoneIntrusion] = []
    for item in items:
        qid = str(item.get("qid"))
        if page_number_qid and qid == page_number_qid:
            continue
        if item.get("intentional") or not item.get("css_visible", True):
            continue
        ink_bottom = _ink_bottom(item)
        if ink_bottom is None:
            continue
        intrusion = ink_bottom - content_bottom
        if intrusion <= FOOTER_ZONE_TOLERANCE_PX:
            continue
        intrusions.append(
            FooterZoneIntrusion(
                qid=qid,
                page_number_qid=page_number_qid,
                content_bottom_px=content_bottom,
                ink_bottom_px=round(ink_bottom, 2),
                intrusion_px=round(intrusion, 2),
                intersects_page_number=(
                    page_number_box is not None
                    and _boxes_intersect(item.get("bbox"), page_number_box)
                ),
            )
        )
    return tuple(intrusions)


def footer_zone_detail(intrusion: FooterZoneIntrusion) -> str:
    """The sentence the remake brief and receipts carry, naming both qids."""
    detail = (
        f"text of {intrusion.qid} prints {intrusion.intrusion_px:.0f}px past the "
        f"{intrusion.content_bottom_px:.0f}px footer safe line"
    )
    if intrusion.intersects_page_number and intrusion.page_number_qid:
        detail += f", under the page number {intrusion.page_number_qid}"
    return detail


def footer_zone_magnitude(intrusion: FooterZoneIntrusion, *, kind: str) -> dict[str, float | str]:
    """Fit magnitude in the shape ``build_fit_summary`` reads for overflow."""
    ratio_y = (
        intrusion.ink_bottom_px / intrusion.content_bottom_px
        if intrusion.content_bottom_px > 0
        else 1.0
    )
    return {
        "ratio_x": 1.0,
        "ratio_y": round(max(1.0, ratio_y), 4),
        "excess_px_x": 0.0,
        "excess_px_y": intrusion.intrusion_px,
        "card_qid": intrusion.page_number_qid,
        "element_kind": kind,
    }


__all__ = [
    "FOOTER_ZONE_CATEGORY", "FOOTER_ZONE_TOLERANCE_PX",
    "NUMBERED_CONTENT_BOTTOM_PX", "UNNUMBERED_CONTENT_BOTTOM_PX",
    "FooterZoneIntrusion", "find_footer_zone_intrusions",
    "footer_zone_detail", "footer_zone_magnitude",
]
