"""Coherence of a reflowed redesign page against its source structure.

A reflow may move blocks (columns become rows, cards form a grid), but it
must not change what belongs together or the order it is read in. The check
works on any analysis: source boxes of the placed regions, their groups and
the output placements. A page that fails keeps the source arrangement
(``raster_layout_geometry``) instead.

Units are groups (a content group, or a group crop) and ungrouped regions.

1. ``group_split``: a group's output box does not take in another unit's
   placement (a section stays contiguous).
2. ``order_inverted``: two units stacked in the source (one above the other
   with horizontal overlap, or side by side with vertical overlap) are not
   stacked the opposite way in the output (lone icons excepted; a standalone
   capture may move into a side column beside the text).
3. ``label_detached``: a label outside any section (a short text, or a small
   picture such as a badge) stays next to the item it touches in the source.
   A label inside a section stays with it through ``group_split``.
4. ``composite_torn``: pictures that touch or overlap in the source (one
   composite, e.g. screenshot tiles) keep one scale and their relative
   arrangement in the output, unless one crop shows both.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.source_content.raster_redesign_fallback import NAMED_GROUPS
from services.source_content.raster_redesign_plan import _text_boxes

LABEL_MAX_CHARS = 40
LABEL_MAX_LINES = 2
LABEL_MAX_HEIGHT = 0.1  # of the source page height (a small picture: badge, icon, tag)
LABEL_MAX_WIDTH = 0.35  # of the source page width
TILE_MIN_AREA = 0.02  # of the source page area: a picture large enough to be a composite piece
TOUCH = 0.01  # of the source page width: pictures this close are one composite
SCALE_SPREAD = 1.2  # composite pieces keep one scale within this ratio
OFFSET_TOLERANCE = 0.1  # of the larger piece dimension, plus OFFSET_SLACK units
OFFSET_SLACK = 6.0
LABEL_TOUCH = 0.5  # a label touches what lies within half its own height
GROUP_TAKE = 0.25  # share of another unit's box inside a group's box that splits the group


@dataclass
class _Placed:
    region_id: str
    unit: str
    source: tuple[float, float, float, float]  # x0, y0, x1, y1 (EMU)
    output: tuple[float, float, float, float]  # x0, y0, x1, y1 (units)
    item: object
    members: frozenset = frozenset()  # region ids a crop shows


def _box(b) -> tuple[float, float, float, float]:
    return b.x, b.y, b.x + b.width, b.y + b.height


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _gap(a, b) -> float:
    """Chebyshev distance between two boxes (0 when they touch or overlap)."""
    return max(0.0, max(a[0], b[0]) - min(a[2], b[2]), max(a[1], b[1]) - min(a[3], b[3]))


def _overlap(a, b, axis: int) -> float:
    return min(a[axis + 2], b[axis + 2]) - max(a[axis], b[axis])


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersection(a, b) -> float:
    return max(0.0, _overlap(a, b, 0)) * max(0.0, _overlap(a, b, 1))


def _placed(page, placements, chosen) -> list[_Placed]:
    crops = {fallback.region.region_id: fallback for fallback in chosen}
    by_id = {region.region_id: region for region in page.regions}
    boxes: dict[str, list] = {}
    items: dict[str, object] = {}
    for placed in placements:
        if placed.item is None or placed.kind in {"card", "panel"}:
            continue
        rid = placed.item.region.region_id
        if placed.kind == "text":
            parts = list(_text_boxes(placed)) or [(placed.x, placed.y, placed.x + placed.w, placed.y + placed.h)]
        else:
            parts = [(placed.x, placed.y, placed.x + placed.w, placed.y + placed.h)]
        boxes.setdefault(rid, []).extend(parts)
        items[rid] = placed.item
    result = []
    for rid, parts in boxes.items():
        item = items[rid]
        fallback = crops.get(rid)
        if fallback is not None:
            members = frozenset(fallback.member_ids)
            groups = {getattr(by_id.get(m), "group_id", None) for m in members} - {None}
            unit = f"g:{fallback.group_id}" if len(groups) == 1 or not groups else f"r:{rid}"
        else:
            members = frozenset({rid})
            group = getattr(item.region, "group_id", None)
            unit = f"g:{group}" if group and group not in NAMED_GROUPS else f"r:{rid}"
        result.append(_Placed(rid, unit, _box(item.region.bbox), _union(parts), item, members))
    return result


def _group_split(placed) -> str | None:
    units: dict[str, list[_Placed]] = {}
    for entry in placed:
        units.setdefault(entry.unit, []).append(entry)
    for unit, entries in units.items():
        if not unit.startswith("g:") or len(entries) < 2:
            continue
        box = _union([entry.output for entry in entries])
        for other in placed:
            if other.unit != unit and _intersection(box, other.output) > GROUP_TAKE * max(_area(other.output), 1e-6):
                return "group_split"
    return None


def _stacked(a, b) -> int:
    """+1 when ``a`` directly precedes ``b`` (above with horizontal overlap, or left with vertical
    overlap), -1 when ``b`` precedes ``a``, 0 otherwise."""
    if _overlap(a, b, 0) > 0:
        if a[3] <= b[1]:
            return 1
        if b[3] <= a[1]:
            return -1
    if _overlap(a, b, 1) > 0:
        if a[2] <= b[0]:
            return 1
        if b[2] <= a[0]:
            return -1
    return 0


def _order_inverted(placed) -> str | None:
    units: dict[str, list[_Placed]] = {}
    for entry in placed:
        units.setdefault(entry.unit, []).append(entry)
    # Lone icons carry no reading order of their own; ``label_detached`` keeps them by their label.
    boxes = [(_union([e.source for e in entries]), _union([e.output for e in entries]), _capture(unit, entries))
             for unit, entries in units.items() if not all(getattr(e.item, "is_icon", False) for e in entries)]
    for i, (source_a, output_a, capture_a) in enumerate(boxes):
        for source_b, output_b, capture_b in boxes[i + 1:]:
            before, after = _stacked(source_a, source_b), _stacked(output_a, output_b)
            if not before or not after or before == after:
                continue
            if (capture_a or capture_b) and _overlap(output_a, output_b, 0) <= 0:
                continue  # a capture moved into a side column beside the text (a split layout)
            return "order_inverted"
    return None


def _capture(unit, entries) -> bool:
    """A standalone picture (not part of a section): a split layout may set it beside the text."""
    return unit.startswith("r:") and all(e.item.kind == "image" for e in entries)


def _is_label(entry, page_w, page_h) -> bool:
    item = entry.item
    if len(entry.members) > 1:
        return False
    if item.kind == "text":
        lines = [line for line in (item.region.text or "").splitlines() if line.strip()]
        return 1 <= len(lines) <= LABEL_MAX_LINES and len("".join(lines).strip()) <= LABEL_MAX_CHARS
    if item.kind == "image":
        width, height = entry.source[2] - entry.source[0], entry.source[3] - entry.source[1]
        return height <= LABEL_MAX_HEIGHT * page_h and width <= LABEL_MAX_WIDTH * page_w
    return False


def _label_detached(placed, page_w, page_h) -> str | None:
    for label in placed:
        if not label.unit.startswith("r:") or not _is_label(label, page_w, page_h):
            continue  # a label inside a section stays with it through ``group_split``
        others = [e for e in placed if e is not label]
        partner = min(others, key=lambda e: _gap(label.source, e.source))
        height = label.source[3] - label.source[1]
        if _gap(label.source, partner.source) > LABEL_TOUCH * height:
            continue  # the label touches nothing in the source
        out_height = label.output[3] - label.output[1]
        if _gap(label.output, partner.output) > 2 * out_height + 12.0:
            return "label_detached"
    return None


def _composite_torn(placed, page_w, page_h) -> str | None:
    pictures = [e for e in placed if e.item.kind == "image" and not getattr(e.item, "inline", False)]
    for i, a in enumerate(pictures):
        for b in pictures[i + 1:]:
            if max(_area(a.source), _area(b.source)) < TILE_MIN_AREA * page_w * page_h:
                continue
            if a.members & b.members or _gap(a.source, b.source) > TOUCH * page_w:
                continue
            scale_a = (a.output[2] - a.output[0]) / max(a.source[2] - a.source[0], 1e-6)
            scale_b = (b.output[2] - b.output[0]) / max(b.source[2] - b.source[0], 1e-6)
            if max(scale_a, scale_b) > SCALE_SPREAD * min(scale_a, scale_b):
                return "composite_torn"
            scale = (scale_a + scale_b) / 2
            size = max(a.output[2] - a.output[0], a.output[3] - a.output[1],
                       b.output[2] - b.output[0], b.output[3] - b.output[1])
            for axis in (0, 1):
                expected = (b.source[axis] - a.source[axis]) * scale
                actual = b.output[axis] - a.output[axis]
                if abs(expected - actual) > OFFSET_TOLERANCE * size + OFFSET_SLACK:
                    return "composite_torn"
    return None


def coherence_issue(analysis, page, placements, chosen=()) -> str | None:
    """The first violated coherence rule of a reflowed page, or None (see module docstring)."""
    placed = _placed(page, placements, chosen)
    if len(placed) < 2:
        return None
    page_w, page_h = analysis.source.canvas_width_emu, analysis.source.canvas_height_emu
    return (_group_split(placed) or _order_inverted(placed) or _label_detached(placed, page_w, page_h)
            or _composite_torn(placed, page_w, page_h))
