"""Deterministic geometry repairs measured from one slide's own capture.

Both repairs here fix arithmetic the author got wrong, cost no provider call,
and change nothing but the CSS of the elements Chromium reported broken. The
gate re-measures the result, so a repair that does not help is rejected like
any other candidate.

Pull an element that overhangs the slide back inside the canvas.

The frame is exactly 1280x720 and clips. The recurring authoring mistake is
arithmetic rather than design: a wrapper inset from the edge is still given the
full canvas width, so it and everything inside it run past the right edge by
exactly the inset. Operation 34c290c6 lost three slides to it and 02aaadec one,
each after the bounded repair round had already tried and failed.

The repair is deterministic and measured: only the elements Chromium reported
outside the canvas are touched, and each is told to take its container's width
instead of the width it was given. Nothing else about the slide changes -- no
text, no QID, no semantic role, no other rule -- and the gate re-measures the
result, so a clamp that does not help is simply rejected like any other
candidate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

#: The authored frame. A slide is authored against this exact box.
CANVAS_WIDTH = 1280.0
CANVAS_HEIGHT = 720.0

#: Sub-pixel layout noise, not an overhang.
_TOLERANCE = 0.5

_STYLE_CLOSE = re.compile(r"</style\s*>", re.IGNORECASE)
_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_QID = re.compile(r'^[A-Za-z][\w.\-]*$')


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _elements(metrics: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(metrics, Mapping):
        return ()
    raw = metrics.get("elements")
    return tuple(item for item in raw or () if isinstance(item, Mapping))


def overhanging_qids(browser_metrics: Any) -> tuple[str, ...]:
    """QIDs whose measured box runs past the right edge of the canvas.

    Only the horizontal overhang is reported: a too-wide box is the mistake
    this repair can undo, while a block that starts below the fold needs the
    slide re-composed and belongs to the provider repair.
    """

    found: list[str] = []
    for element in _elements(browser_metrics):
        qid = str(element.get("qid") or "").strip()
        if not qid or not _QID.match(qid) or qid in found:
            continue
        box = element.get("bbox")
        if not isinstance(box, Mapping):
            continue
        left = _number(box.get("x"))
        width = _number(box.get("width"))
        if left is None or width is None or width <= 0:
            continue
        if left + width > CANVAS_WIDTH + _TOLERANCE and left > _TOLERANCE:
            found.append(qid)
    return tuple(found)


def repair_authored_offcanvas_width(html: str, browser_metrics: Any) -> str:
    """Return ``html`` with the overhanging elements clamped to their container.

    ``width:auto`` on a block element is its container's content width, which
    is the width the author meant. ``max-width:100%`` keeps a flex or grid item
    from insisting on its own basis, and ``box-sizing:border-box`` stops padding
    from re-creating the overhang. The rule is appended last and marked
    important so it wins over the authored declaration without editing it.
    """

    document = str(html or "")
    qids = overhanging_qids(browser_metrics)
    if not document or not qids:
        return document
    selector = ", ".join(f'[data-qid="{qid}"]' for qid in qids)
    rule = (
        "\n/* deterministic off-canvas containment: measured overhang */\n"
        f"{selector} {{ width: auto !important; max-width: 100% !important;"
        " box-sizing: border-box !important; }\n"
    )
    return _append_style_rule(document, rule)


def _append_style_rule(document: str, rule: str) -> str:
    """Put ``rule`` last in the document's own stylesheet, or add one."""

    match = None
    for candidate in _STYLE_CLOSE.finditer(document):
        match = candidate
    if match is not None:
        return document[: match.start()] + rule + document[match.start() :]
    head = _HEAD_CLOSE.search(document)
    block = f"<style>{rule}</style>"
    if head is not None:
        return document[: head.start()] + block + document[head.start() :]
    return document + block


#: The authored design contract treats 15px as the inclusive 11pt floor, which
#: is the same number the capacity gate enforces.
MIN_FONT_PX = 15.0

#: A source blank field is a run of underscores the deck must reproduce
#: literally: it cannot be shortened, only set smaller.
_BLANK_FIELD = re.compile(r"^[_\s–—]+$")


def _clipped(element: Mapping[str, Any]) -> bool:
    return bool(
        element.get("clipped")
        or element.get("glyph_clipped")
        or element.get("line_box_clipped")
    )


def clipped_blank_field_qids(browser_metrics: Any) -> tuple[str, ...]:
    """QIDs of clipped elements whose text is a source blank field.

    A worksheet line is thirty underscores the restyle must keep, and at the
    author's chosen size it is wider than the box drawn for it.  The
    characters are not negotiable and the box is the template's, so the size
    is the one thing left to give.
    """

    found: list[str] = []
    for element in _elements(browser_metrics):
        qid = str(element.get("qid") or "").strip()
        if not qid or not _QID.match(qid) or qid in found or not _clipped(element):
            continue
        text = str(element.get("rendered_text") or "")
        size = _number(element.get("font_size"))
        if text and _BLANK_FIELD.match(text) and size is not None and size > MIN_FONT_PX:
            found.append(qid)
    return tuple(found)


def repair_authored_blank_field_clipping(html: str, browser_metrics: Any) -> str:
    """Set a clipped blank field at the design floor so its line fits.

    Thirty underscores at 20px overflowed a 347px box on slide 10 of operation
    7787af14 and survived the bounded repair round.  At the floor the same run
    fits, the characters are untouched, and no other element is affected.
    """

    document = str(html or "")
    qids = clipped_blank_field_qids(browser_metrics)
    if not document or not qids:
        return document
    selector = ", ".join(f'[data-qid="{qid}"]' for qid in qids)
    rule = (
        "\n/* deterministic blank-field containment: measured clipping */\n"
        f"{selector} {{ font-size: {MIN_FONT_PX:.0f}px !important;"
        " white-space: nowrap !important; }\n"
    )
    return _append_style_rule(document, rule)


__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "MIN_FONT_PX",
    "clipped_blank_field_qids",
    "overhanging_qids",
    "repair_authored_blank_field_clipping",
    "repair_authored_offcanvas_width",
]
