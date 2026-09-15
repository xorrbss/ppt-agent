"""Text printed outside the card that owns it, measured from its line rects.

Bench run 29 (A-med, 2026-09-05) published a slide whose right column had two
paragraphs printed *outside* their cards: ``s18-accept-evidence``'s last line
ran 29 px below ``s18-accept-card`` and under the next card, and
``s18-stable-scope``'s ran 17 px past its own card's bottom edge.  Every
existing detector had a principled reason to stay silent.  ``overflow``: the
card's ``scrollHeight`` (249) does exceed its ``clientHeight`` (218), but it is
``overflow: visible`` and owns no text itself, so both
``overflow_axis_is_actionable`` and ``browser_proves_owned_content`` correctly
decline -- and the paragraphs report ``scrollHeight == clientHeight``.
``clipping``: nothing is clipped; ``overflow: visible`` is exactly why the
glyphs are visible in the wrong place (the one element marked clipped is an
unrelated container in the other column).  ``off_canvas``: the escaped lines
end at y=655 on a 720 px canvas.  ``overlap``: the pairwise check does find the
paragraph over the next card at 0.19 intersection, but
``canonical_overlap_pair`` collapses the pair to the sibling branch roots
(``s18-accept-card``, ``s18-stable-card``), which do not overlap, so the
re-check on the canonical pair drops it.

The blind spot they share is that nothing compares a text owner's own line
boxes against the card that should contain them.  That is what this module
measures -- a pure function over normalized browser metrics: the gate decides,
this module only reports what it measured.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from utils.authored_dom_footer_zone import FooterZoneIntrusion

#: Category name for the issue this module feeds.  Deliberately not
#: ``overflow``/``overlap``: the A4 text-scale rung refuses to run on an
#: ``overlap`` slide, and this defect is precisely the one shrinking type fixes.
TEXT_ESCAPE_CATEGORY = "text_escapes_card"

# A detector names its category; it does not decide whether that category
# blocks publication.  ``utils.authored_dom_categories`` states that once, for
# every consumer, deriving ``BLOCKING_SAFETY_CATEGORIES`` from the detected
# geometry categories minus the ones demoted to advisory.

#: Sub-pixel layout noise routinely puts a line box a fraction past its box.
ESCAPE_TOLERANCE_PX = 2.0

_TRANSPARENT = {"", "transparent", "rgba(0,0,0,0)"}
_PAINTED_TAGS = {"img", "picture", "svg", "video", "canvas"}


@dataclass(frozen=True)
class TextEscape:
    """One text owner whose glyphs print outside the card that should hold it."""

    qid: str
    card_qid: str
    escape_px_right: float
    escape_px_bottom: float
    ratio_x: float
    ratio_y: float
    collides_with: tuple[str, ...] = ()
    #: The card's own bottom edge, which decides whether returning the copy to
    #: it is enough (``reconcile_footer_zone_reports``).
    card_bottom_px: float = 0.0


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return default if result != result else result


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


def _css_px(value: object) -> float:
    text = str(value or "").strip().lower()
    return _number(text[:-2] if text.endswith("px") else text)


def _is_card_like(item: Mapping[str, Any]) -> bool:
    """A painted or clipping panel: the box a reader sees the text as being in.

    A single border is deliberately not enough: authored slides use a 1px top
    rule as a paragraph divider, and treating that as a card would make a
    paragraph its own container.  Four borders is a frame, and a frame is a card.
    """

    style = item.get("computed_style", item.get("style", {}))
    if not isinstance(style, Mapping):
        return False
    if str(item.get("tag", "")).strip().lower() in _PAINTED_TAGS:
        return True
    # A measured, unpadded two-pixel connector is a line, not the panel
    # containing its arrow. Keep walking to the real enclosing panel. This
    # does not exempt the arrow from clipping, overlap or source readback.
    edges = _edges(item.get("bbox"))
    if edges is not None:
        width, height = edges[2] - edges[0], edges[3] - edges[1]
        if (
            item.get("semantic_role") == "decoration"
            and item.get("has_owned_text") is False
            and not item.get("text_fragments")
            and min(width, height) <= 2.0
            and max(width, height) >= 12.0
            and all(style.get(axis) == "visible" for axis in ("overflowX", "overflowY"))
            and all(_css_px(style.get(f"padding{side}")) == 0 for side in ("Top", "Right", "Bottom", "Left"))
            and all(_css_px(style.get(f"border{side}Width")) == 0 for side in ("Top", "Right", "Bottom", "Left"))
            and style.get("backgroundImage") == "none"
            and style.get("boxShadow") == "none"
            and style.get("clipPath") == "none"
            and style.get("maskImage") == "none"
            and style.get("transform") == "none"
        ):
            return False
    background = str(style.get("backgroundColor", "")).replace(" ", "").lower()
    if background and background not in _TRANSPARENT:
        return True
    if str(style.get("backgroundImage", "none")).strip().lower() not in {"", "none"}:
        return True
    for axis in ("overflowY", "overflowX", "overflow"):
        mode = str(style.get(axis, "") or "").strip().lower()
        if mode and mode != "visible":
            return True
    if any(
        _css_px(style.get(f"border{corner}Radius")) > 0
        for corner in ("TopLeft", "TopRight", "BottomLeft", "BottomRight")
    ):
        return True
    if str(style.get("boxShadow", "none")).strip().lower() not in {"", "none"}:
        return True
    return all(
        _css_px(style.get(f"border{side}Width")) > 0
        for side in ("Top", "Right", "Bottom", "Left")
    )


def _ink_extent(item: Mapping[str, Any]) -> tuple[float, float] | None:
    """``(right, bottom)`` of the owner's glyph ink, not of its line boxes.

    A line rect is as tall as the line height, so its bottom sits half the
    leading below the last descender.  Measuring the em box instead is what
    separates run 29's slide 20 (a paragraph ending flush with its card: fine)
    from its slide 17 (a line printed outside the card).
    """

    fragments = item.get("text_fragments")
    if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
        return None
    font_size = _number(item.get("font_size"))
    rights: list[float] = []
    bottoms: list[float] = []
    for fragment in fragments:
        edges = _edges(fragment)
        if edges is None:
            continue
        height = edges[3] - edges[1]
        rights.append(edges[2])
        bottoms.append(edges[3] - max(0.0, (height - font_size) / 2.0))
    if not rights:
        return None
    return max(rights), max(bottoms)


def _ancestors(qid: str, parent_by_qid: Mapping[str, str]) -> list[str]:
    result: list[str] = []
    current = parent_by_qid.get(qid, "")
    seen = {qid}
    while current and current not in seen:
        seen.add(current)
        result.append(current)
        current = parent_by_qid.get(current, "")
    return result


def _collision_qids(
    item: Mapping[str, Any],
    ancestors: Sequence[str],
    item_by_qid: Mapping[str, Mapping[str, Any]],
    ink_bottom: float,
) -> tuple[str, ...]:
    """Painted non-ancestors the browser itself attributes this occlusion to.

    Reusing the owned-paint probe's attribution keeps this from becoming a
    second, weaker opinion about z-order.
    """

    raw = item.get("occlusion_occluder_qids", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    ancestor_set = set(ancestors)
    hits: list[str] = []
    for value in raw:
        qid = str(value)
        if qid in ancestor_set or qid in hits:
            continue
        other = item_by_qid.get(qid)
        edges = _edges(other.get("bbox")) if isinstance(other, Mapping) else None
        if edges is not None and ink_bottom - edges[1] > ESCAPE_TOLERANCE_PX:
            hits.append(qid)
    return tuple(hits)


def find_text_escapes(elements: Iterable[Mapping[str, Any]]) -> tuple[TextEscape, ...]:
    """Every text owner whose glyph ink prints past its owning card-like box.

    Nested painted panels are common.  A child panel can contain its text while
    the same text still prints past an enclosing content card, so walk outward
    until the nearest violated boundary.  Explicitly intentional or decorative
    overlays stop that walk; badges may intentionally cross the card behind them.
    """

    items = [item for item in elements if isinstance(item, Mapping)]
    item_by_qid = {str(item.get("qid")): item for item in items}
    parent_by_qid = {
        str(item.get("qid")): str(item.get("parent_qid") or "") for item in items
    }
    escapes: list[TextEscape] = []
    for item in items:
        if item.get("intentional") or not item.get("css_visible", True):
            continue
        extent = _ink_extent(item)
        if extent is None:
            continue
        qid = str(item.get("qid"))
        ancestors = _ancestors(qid, parent_by_qid)
        right, bottom = extent
        selected: tuple[Mapping[str, Any], tuple[float, float, float, float], float, float] | None = None
        for ancestor in ancestors:
            card = item_by_qid.get(ancestor)
            if card is None or not _is_card_like(card):
                continue
            stops_outer_walk = bool(
                card.get("intentional") or card.get("semantic_role") == "decoration"
            )
            card_edges = _edges(card.get("bbox"))
            if card_edges is None:
                continue
            escape_right = max(0.0, right - card_edges[2])
            escape_bottom = max(0.0, bottom - card_edges[3])
            if max(escape_right, escape_bottom) > ESCAPE_TOLERANCE_PX:
                selected = card, card_edges, escape_right, escape_bottom
                break
            if stops_outer_walk:
                break
        if selected is None:
            continue
        card, card_edges, escape_right, escape_bottom = selected
        card_left, card_top, card_right, card_bottom = card_edges
        escapes.append(
            TextEscape(
                qid=qid,
                card_qid=str(card.get("qid")),
                escape_px_right=round(escape_right, 2),
                escape_px_bottom=round(escape_bottom, 2),
                ratio_x=round(max(1.0, (right - card_left) / (card_right - card_left)), 4),
                ratio_y=round(max(1.0, (bottom - card_top) / (card_bottom - card_top)), 4),
                collides_with=_collision_qids(item, ancestors, item_by_qid, bottom),
                card_bottom_px=round(card_bottom, 2),
            )
        )
    return tuple(escapes)


def reconcile_footer_zone_reports(
    escapes: Sequence[TextEscape], intrusions: Sequence[FooterZoneIntrusion]
) -> tuple[tuple[TextEscape, ...], tuple[FooterZoneIntrusion, ...]]:
    """One element, one report: keep only the boundary that actually binds it.

    A paragraph that runs out of the bottom of its card usually crosses the
    footer safe line on the way, so both detectors describe the same defect.
    Bench run 36 measured that on 6 owners across 3 of 20 slides (``s7-auth-
    check``, ``s10-integration``, ``s14-proof``/``s14-devax``/``s14-network``),
    which spends two of the remake brief's eight issue slots on one problem and
    counts it twice in every receipt.

    The binding boundary is the card's when the card itself is legal -- putting
    the copy back inside it also puts the copy above the footer line -- and the
    footer line's when the card hangs below it, because then a copy obediently
    fitted into its card is still printed under the page number.
    """

    escape_by_qid = {escape.qid: escape for escape in escapes}
    dropped_escapes: set[str] = set()
    kept_intrusions: list[FooterZoneIntrusion] = []
    for intrusion in intrusions:
        escape = escape_by_qid.get(intrusion.qid)
        if escape is None:
            kept_intrusions.append(intrusion)
            continue
        if escape.card_bottom_px > intrusion.content_bottom_px:
            dropped_escapes.add(escape.qid)
            kept_intrusions.append(intrusion)
    return (
        tuple(escape for escape in escapes if escape.qid not in dropped_escapes),
        tuple(kept_intrusions),
    )


def text_escape_detail(escape: TextEscape) -> str:
    """The sentence the remake brief and the receipts carry, naming both qids."""

    axis = (
        f"{escape.escape_px_bottom:.0f}px below the bottom of"
        if escape.escape_px_bottom >= escape.escape_px_right
        else f"{escape.escape_px_right:.0f}px past the right edge of"
    )
    detail = f"text of {escape.qid} prints {axis} its card {escape.card_qid}"
    if escape.collides_with:
        detail += f", over {', '.join(escape.collides_with[:2])}"
    return detail


def text_escape_magnitude(escape: TextEscape, *, kind: str) -> dict[str, float | str]:
    """Fit magnitude in the shape ``build_fit_summary`` reads for overflow."""

    return {
        "ratio_x": escape.ratio_x,
        "ratio_y": escape.ratio_y,
        "excess_px_x": escape.escape_px_right,
        "excess_px_y": escape.escape_px_bottom,
        "card_qid": escape.card_qid,
        "element_kind": kind,
    }


__all__ = [
    "ESCAPE_TOLERANCE_PX",
    "TEXT_ESCAPE_CATEGORY",
    "TextEscape",
    "find_text_escapes",
    "reconcile_footer_zone_reports",
    "text_escape_detail",
    "text_escape_magnitude",
]
