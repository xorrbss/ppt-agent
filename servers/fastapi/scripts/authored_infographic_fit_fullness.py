"""Does a rendered figure use the box it was given, or float in the middle of it?

The fit sweep answers "does anything leave the box". That check passes for a
figure that draws 250px of content in the middle of a 500px box and leaves two
white bands around it, which is exactly the defect this module measures. Three
readings, all taken from the same ``browser_metrics["elements"]`` the overflow
check reads:

``card_slack``
    per ``.ig-node`` (qid ``ig.sN.nK``), the card's own height over the union
    height of the content it carries (qids ``ig.sN.nK.*`` that own text, or are
    one of its graphic slots -- icon, dot, badge, track -- or an image). A card
    twice as tall as its content is a hollow box whatever the box it sits in,
    so the bar is 1.3.

``box_use``
    the union height of every content element of the figure over the height of
    the box the figure was given. A figure that centres its intrinsic height in
    a tall box reads about 0.5 here; the bar is 0.7.

``ink_fill``
    the union *area* of the same content boxes over the area of the figure box
    — the richness harness's ``content_bbox_fill`` notion (owned-content boxes,
    unioned, over a rectangle) measured against the figure's box rather than
    the whole canvas, so per-archetype numbers are comparable across boxes. It
    is reported, not gated: a cycle is legitimately mostly whitespace.

Nothing here renders: it is arithmetic over one capture, so the thresholds can
be tested without a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: A card of the library: the element the ``nK`` qid names, with no suffix.
NODE_QID = re.compile(r"^ig\.s\d+\.n\d+$")
#: Tags that are content on their own, without owning any text.
CONTENT_TAGS = frozenset({"img", "svg", "table"})
#: Qid suffixes that name paint, not content: the backplate behind a card.
DECOR_SUFFIXES = ("bg",)
#: Qid suffixes that name a graphic slot of a card: an icon frame (its ``<svg>``
#: carries no qid of its own, so the frame stands for it), a milestone dot, a
#: checklist badge, a roadmap phase track and its fill. These count only inside
#: a node -- ``ig.sN.nK.track`` is a row's own bar, while ``ig.sN.track`` is the
#: wrapper the whole timeline is laid out in, which is furniture.
GRAPHIC_SUFFIXES = ("ic", "dot", "check", "track", "fill")
_NODE_SEGMENT = re.compile(r"\.n\d+\.")

CARD_SLACK_LIMIT = 1.3
BOX_USE_FLOOR = 0.7


def _bbox(element: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    box = element.get("bbox") or {}
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def is_content(element: Mapping[str, Any]) -> bool:
    """Whether an element carries what the reader came for.

    Text it owns itself, an icon frame, an image, or a native table. A card,
    a tier wrapper, a rail and a backplate are not content: they are the
    furniture the content is arranged in, and counting them would let an empty
    card prove itself full.
    """
    if _bbox(element) is None:
        return False
    qid = str(element.get("qid") or "")
    suffix = qid.rsplit(".", 1)[-1]
    if suffix in DECOR_SUFFIXES or qid.endswith(".root"):
        return False
    if str(element.get("owned_text") or "").strip():
        return True
    if str(element.get("tag") or "").lower() in CONTENT_TAGS:
        return True
    return suffix in GRAPHIC_SUFFIXES and _NODE_SEGMENT.search(qid) is not None


def _union_height(boxes: Sequence[tuple[float, float, float, float]]) -> float:
    if not boxes:
        return 0.0
    return max(y + h for _, y, _, h in boxes) - min(y for _, y, _, _ in boxes)


def union_area(boxes: Sequence[tuple[float, float, float, float]]) -> float:
    """Area covered by a set of rectangles, counting an overlap once.

    Coordinate compression over a few dozen boxes: exact, and cheap enough to
    run on every rendered combination.
    """
    if not boxes:
        return 0.0
    xs = sorted({x for x, _, _, _ in boxes} | {x + w for x, _, w, _ in boxes})
    ys = sorted({y for _, y, _, _ in boxes} | {y + h for _, y, _, h in boxes})
    total = 0.0
    for i, x0 in enumerate(xs[:-1]):
        x1 = xs[i + 1]
        for j, y0 in enumerate(ys[:-1]):
            y1 = ys[j + 1]
            if any(
                bx <= x0 and x1 <= bx + bw and by <= y0 and y1 <= by + bh
                for bx, by, bw, bh in boxes
            ):
                total += (x1 - x0) * (y1 - y0)
    return total


@dataclass
class Fullness:
    """How much of its box one rendered figure actually occupies."""

    card_slack: float = 0.0
    slack_qid: str = ""
    box_use: float = 0.0
    ink_fill: float = 0.0

    @property
    def flags(self) -> tuple[str, ...]:
        out = []
        if self.card_slack > CARD_SLACK_LIMIT:
            out.append("slack")
        if self.box_use < BOX_USE_FLOOR:
            out.append("box_use")
        return tuple(out)

    @property
    def failed(self) -> bool:
        return bool(self.flags)


def measure(
    elements: Iterable[Mapping[str, Any]], qid_prefix: str, box_height: float, box_area: float
) -> Fullness:
    """Read the three fullness numbers off one capture's element list."""
    own = [e for e in elements if str(e.get("qid") or "").startswith(qid_prefix)]
    content = {str(e["qid"]): _bbox(e) for e in own if is_content(e)}
    boxes = [b for b in content.values() if b is not None]
    result = Fullness(
        box_use=round(_union_height(boxes) / box_height, 3) if box_height else 0.0,
        ink_fill=round(union_area(boxes) / box_area, 3) if box_area else 0.0,
    )
    for element in own:
        qid = str(element.get("qid") or "")
        card = _bbox(element)
        if card is None or not NODE_QID.match(qid):
            continue
        inner = [b for q, b in content.items() if q.startswith(qid + ".") and b is not None]
        filled = _union_height(inner)
        if filled <= 0:
            continue
        slack = round(card[3] / filled, 3)
        if slack > result.card_slack:
            result.card_slack, result.slack_qid = slack, qid
    return result


def columns(fullness: Fullness) -> dict[str, Any]:
    """The fullness half of one sweep row."""
    return {
        "card_slack": fullness.card_slack,
        "slack_qid": fullness.slack_qid,
        "box_use": fullness.box_use,
        "ink_fill": fullness.ink_fill,
        "full": not fullness.failed,
        "fullness_flags": " ".join(fullness.flags),
    }


def worst_per_archetype(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per archetype x node count x box, the worst reading of the sweep."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row["archetype"], row["nodes"], row["box"])
        seen = grouped.get(key)
        if seen is None:
            seen = dict(zip(("archetype", "nodes", "box"), key, strict=True))
            seen.update(card_slack=0.0, slack_qid="", box_use=9.9, ink_fill=9.9, flagged=0)
            grouped[key] = seen
        if row["card_slack"] > seen["card_slack"]:
            seen["card_slack"], seen["slack_qid"] = row["card_slack"], row["slack_qid"]
        seen["box_use"] = min(seen["box_use"], row["box_use"])
        seen["ink_fill"] = min(seen["ink_fill"], row["ink_fill"])
        seen["flagged"] += 0 if row["full"] else 1
    return sorted(grouped.values(), key=lambda r: (r["archetype"], r["nodes"], r["box"]))


__all__ = [
    "BOX_USE_FLOOR",
    "CARD_SLACK_LIMIT",
    "Fullness",
    "GRAPHIC_SUFFIXES",
    "columns",
    "is_content",
    "measure",
    "union_area",
    "worst_per_archetype",
]
