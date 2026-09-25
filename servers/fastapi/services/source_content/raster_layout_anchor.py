"""Anchor ungrouped content to its source section and reading position before a reflow.

The vision structurer groups text into sections (``group_id``) and orders
them, but pictures, badges and pixel crops it did not group get the reading
order *after* everything else. A reflow that follows that order moves a
step's screenshot below the last card and a section's badge away from its
heading. Generic rules, from source geometry only:

* **membership**: an ungrouped item joins the one content group it touches
  (a label within half its own height of a member; a picture within ``TOUCH``
  of the page width, sharing most of the shorter side), else the one group
  whose source box holds most (``INSIDE``) of it. An item touching two groups (e.g. a
  banner under a row of cards) stays ungrouped. Inside the group it takes its visual position (before the first
  member it sits above, or left of on one line).
* **composites**: touching loose pictures (``raster_layout_composite``) move
  as one unit, so a mock-up is never split between sections.
* **labels**: an ungrouped short text touching another loose unit forms a
  small section with it.
* **position**: every remaining ungrouped unit is inserted into the reading
  sequence before the first unit it sits directly above (or left of, on one
  line), else after the last unit directly above or left of it.

It never removes, duplicates or rewrites anything: it only regroups and
reorders the content items.
"""

from __future__ import annotations

import copy
import functools
from dataclasses import replace

from services.source_content.raster_layout_coherence import (
    LABEL_MAX_CHARS,
    LABEL_MAX_HEIGHT,
    LABEL_MAX_LINES,
    LABEL_MAX_WIDTH,
    LABEL_TOUCH,
    TOUCH,
    _gap,
    _intersection,
    _overlap,
    _stacked,
    _union,
)
from services.source_content.raster_layout_composite import composite_clusters
from services.source_content.raster_redesign_fallback import NAMED_GROUPS

ANCHOR_PREFIX = "anchor-"
INSIDE = 0.85  # share of a unit's area within a section's source box that makes it part of the section


def _box(item):
    b = item.region.bbox
    return b.x, b.y, b.x + b.width, b.y + b.height


def _group(item):
    group = getattr(item.region, "group_id", None)
    return group if group and group not in NAMED_GROUPS else None


def _is_label(item, page_w, page_h) -> bool:
    if item.kind == "text":
        lines = [line for line in (item.region.text or "").splitlines() if line.strip()]
        return 1 <= len(lines) <= LABEL_MAX_LINES and len("".join(lines).strip()) <= LABEL_MAX_CHARS
    x0, y0, x1, y1 = _box(item)
    return item.kind == "image" and y1 - y0 <= LABEL_MAX_HEIGHT * page_h and x1 - x0 <= LABEL_MAX_WIDTH * page_w


def _with(item, **fields):
    region = item.region
    if hasattr(region, "model_copy"):
        region = region.model_copy(update=fields)
    else:
        region = copy.copy(region)
        for key, value in fields.items():
            setattr(region, key, value)
    return replace(item, region=region)


def _touches(box, label, member, page_w) -> bool:
    b = _box(member)
    gap = _gap(box, b)
    if label:
        return gap <= LABEL_TOUCH * (box[3] - box[1])
    # Side by side or stacked: the shorter of the two shares most of its side with the other.
    shared = max(_overlap(box, b, 0) / max(min(box[2] - box[0], b[2] - b[0]), 1e-6),
                 _overlap(box, b, 1) / max(min(box[3] - box[1], b[3] - b[1]), 1e-6))
    return gap <= TOUCH * page_w and shared >= 0.5


def _membership(box, label, groups, page_w):
    """The one section a unit touches, else the one whose box holds its centre; None when ambiguous."""
    touching = [g for g, members in groups.items() if any(_touches(box, label, m, page_w) for m in members)]
    if touching:
        return touching[0] if len(touching) == 1 else None  # touching two sections: neither owns it
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
    inside = [g for g, members in groups.items()
              if _intersection(box, _union([_box(m) for m in members])) >= INSIDE * area]
    return inside[0] if len(inside) == 1 else None


def _insert_member(sequence, item, group):
    """Insert ``item`` into ``group``'s members at its visual position; give it that reading order."""
    members = [index for index, entry in enumerate(sequence) if _group(entry) == group]
    box = _box(item)
    for index in members:
        if _stacked(box, _box(sequence[index])) == 1:
            order = getattr(sequence[index].region, "reading_order", None)
            placed = _with(item, group_id=group, reading_order=(order - 0.5) if order is not None else None)
            return [*sequence[:index], placed, *sequence[index:]]
    last = members[-1]
    order = getattr(sequence[last].region, "reading_order", None)
    placed = _with(item, group_id=group, reading_order=(order + 0.5) if order is not None else None)
    return [*sequence[:last + 1], placed, *sequence[last + 1:]]


def _visual_members(sequence):
    """Pictures and pixel lines of a group take their visual place among its members.

    Text members keep the structurer's order; a picture it appended after them
    moves before the first member it sits above (or left of, on one line).
    """
    for item in [entry for entry in sequence if _group(entry) and entry.kind == "image"]:
        group = _group(item)
        others = [entry for entry in sequence if entry is not item and _group(entry) == group]
        if not others:
            continue
        rest = [entry for entry in sequence if entry is not item]
        sequence = _insert_member(rest, item, group)
    return sequence


def _units(sequence):
    units, index = [], {}
    for item in sequence:
        key = _group(item) or f"item:{id(item)}"
        if key not in index:
            index[key] = len(units)
            units.append([])
        units[index[key]].append(item)
    return units


def _place_unit(units, unit):
    box = _union([_box(item) for item in unit])
    boxes = [_union([_box(item) for item in other]) for other in units]
    for index, other in enumerate(boxes):
        if _stacked(box, other) == 1:
            return [*units[:index], unit, *units[index:]]
    after = [index for index, other in enumerate(boxes) if _stacked(other, box) == 1]
    if after:
        return [*units[:after[-1] + 1], unit, *units[after[-1] + 1:]]
    return [*units, unit]


def _loose_units(loose, source_size) -> list[list]:
    """Loose items as units: a composite of touching pictures moves as one."""
    clusters = composite_clusters(loose, source_size)
    owner = {id(member): cluster for cluster in clusters for member in cluster}
    units, seen = [], set()
    for item in loose:
        cluster = owner.get(id(item))
        if cluster is None:
            units.append([item])
        elif id(cluster) not in seen:
            seen.add(id(cluster))
            units.append(list(cluster))
    return units


def _text_label(unit, page_w, page_h) -> bool:
    return len(unit) == 1 and unit[0].kind == "text" and _is_label(unit[0], page_w, page_h)


def anchor_items(items, source_size) -> list:
    """Content items regrouped and reordered by their source position (see module docstring)."""
    page_w, page_h = source_size
    groups: dict[str, list] = {}
    for item in items:
        if _group(item):
            groups.setdefault(_group(item), []).append(item)
    if not groups:
        return list(items)  # an ungrouped page is composed by source geometry (sections) already
    sequence = [item for item in items if getattr(item.region, "group_id", None)]
    rest = []
    for unit in _loose_units([item for item in items if not getattr(item.region, "group_id", None)], source_size):
        box = _union([_box(item) for item in unit])
        label = len(unit) == 1 and _is_label(unit[0], page_w, page_h)
        group = _membership(box, label, groups, page_w)
        if group is None:
            rest.append(unit)
            continue
        for item in unit:
            sequence = _insert_member(sequence, item, group)
    # A text label touching another loose unit forms a small section with it.
    paired: dict[int, str] = {}
    for label in rest:
        if id(label) in paired or not _text_label(label, page_w, page_h):
            continue
        lbox = _box(label[0])
        others = [o for o in rest if o is not label and id(o) not in paired and not o[0].inline]
        partner = min(others, key=lambda o: _gap(lbox, _union([_box(i) for i in o])), default=None)
        if partner is not None and _gap(lbox, _union([_box(i) for i in partner])) <= LABEL_TOUCH * (lbox[3] - lbox[1]):
            paired[id(label)] = paired[id(partner)] = f"{ANCHOR_PREFIX}{label[0].region.region_id}"
    sequence = _visual_members(sequence)
    units = _units(sequence)
    pending: dict[str, list] = {}
    for unit in rest:
        key = paired.get(id(unit))
        if key is None:
            units = _place_unit(units, unit)
        else:
            pending.setdefault(key, []).extend(_with(item, group_id=key) for item in unit)
    for unit in pending.values():
        unit.sort(key=functools.cmp_to_key(lambda a, b: -_stacked(_box(a), _box(b))))
        units = _place_unit(units, unit)
    return [item for unit in units for item in unit]
