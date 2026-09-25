"""Source-structure composition for redesign pages via recursive XY-cut.

The source slide's whitespace gaps split content into horizontal slabs and
vertical columns (e.g. four "step" columns of heading, capture and caption).
The resulting tree is rebuilt at the target canvas with fresh typography, so
the redesign keeps the source's grouping and order without copying its pixels.
"""

from __future__ import annotations

from services.source_content.raster_redesign_tree import ImageRow, Panel, Row, Stack

PANEL_PAD = 8.0
COLUMN_GAP = 12.0
SLAB_GAP = 10.0
MAX_COLUMNS = 8
MIN_COLUMN_WIDTH = 110.0
# Second, narrower pass (about six Hangul characters at the 9.5 pt floor)
# tried before a row gives up and stacks its columns vertically.
RELAXED_COLUMN_WIDTH = 80.0
ICON_GAP = 6.0


def _groups(items, axis: str, tolerance: float):
    def span(item):
        box = item.region.bbox
        start, size = (box.y, box.height) if axis == "y" else (box.x, box.width)
        shrink = min(tolerance, size / 4)
        return start + shrink, start + size - shrink

    groups: list[list] = []
    end = None
    for item in sorted(items, key=lambda value: span(value)[0]):
        start, stop = span(item)
        if groups and start < end:
            groups[-1].append(item)
            end = max(end, stop)
        else:
            groups.append([item])
            end = stop
    return groups


def xy_tree(items, source_width: float, source_height: float, order):
    """Return ("stack"|"row"|"leaf", children) preserving reading order in leaves."""

    if len(items) == 1:
        return ("leaf", items)
    # First a strict cut; if overlapping OCR boxes chain everything together,
    # retry on each box's central half (soft cut) before giving up.
    for factor in (0.004, 1.0):
        slabs = _groups(items, "y", source_height * factor)
        if len(slabs) > 1:
            return ("stack", [xy_tree(group, source_width, source_height, order) for group in slabs])
        columns = _groups(items, "x", source_width * factor)
        if len(columns) > 1:
            return ("row", [xy_tree(group, source_width, source_height, order) for group in columns])
    return ("leaf", sorted(items, key=order))


def _extent(node):
    kind, children = node
    if kind == "leaf":
        boxes = [item.region.bbox for item in children]
    else:
        spans = [_extent(child) for child in children]
        return min(s[0] for s in spans), max(s[1] for s in spans)
    return min(b.x for b in boxes), max(b.x + b.width for b in boxes)


def _only_images(node) -> bool:
    kind, children = node
    return kind == "leaf" and all(item.kind == "image" for item in children)


def build_sections(tree, x, w, stack_items):
    """Convert an XY tree into layout nodes; ``stack_items(items, x, w)`` builds leaves."""

    kind, children = tree
    if kind == "leaf":
        return stack_items(children, x, w)
    if kind == "stack":
        nodes = [build_sections(child, x, w, stack_items) for child in children]
        return None if any(node is None for node in nodes) else Stack(nodes, SLAB_GAP)
    row = _row(children, x, w, stack_items) or _row(children, x, w, stack_items, min_width=RELAXED_COLUMN_WIDTH)
    if row is not None:
        return row
    # Too many or too narrow columns for the target width: keep the source
    # order but stack the columns vertically instead of failing the page.
    nodes = [build_sections(child, x, w, stack_items) for child in children]
    return None if any(node is None for node in nodes) else Stack(nodes, SLAB_GAP)


def _row(children, x, w, stack_items, min_width=MIN_COLUMN_WIDTH):
    if all(_only_images(child) for child in children):
        images = [item for child in children for item in child[1]]
        if len(images) <= 4 or all(item.is_icon for item in images):
            return ImageRow(images, x, w)
        return None
    # Icon-only columns keep their natural (<=1.25x) width, like an inline
    # glyph beside its label; only content columns share the remaining width.
    fixed = [_icon_column_width(child) for child in children]
    flexible = [index for index, value in enumerate(fixed) if value is None]
    usable = w - COLUMN_GAP * (len(children) - 1) - sum(value for value in fixed if value is not None)
    if len(flexible) > MAX_COLUMNS or usable <= 0 or (flexible and usable / len(flexible) < min_width):
        return None
    extents = [_extent(children[index]) for index in flexible]
    widths = [max(stop - start, 1.0) for start, stop in extents]
    # Every content column gets the readable minimum; the rest of the width
    # follows the source proportions.
    shares = _water_fill(widths, usable, min_width)
    share_of = dict(zip(flexible, shares, strict=True))
    columns, left = [], x
    for index, child in enumerate(children):
        if fixed[index] is not None:
            columns.append(ImageRow(list(child[1]), left, fixed[index]))
            left += fixed[index] + COLUMN_GAP
            continue
        share = share_of[index]
        if child[0] == "row" or len(flexible) == 1 or _item_count(child) < 2:
            node = build_sections(child, left, share, stack_items)
        else:
            inner = build_sections(child, left + PANEL_PAD, share - PANEL_PAD * 2, stack_items)
            node = None if inner is None else Panel(inner, left, share, PANEL_PAD)
        if node is None:
            return None
        columns.append(node)
        left += share + COLUMN_GAP
    return Row(columns)


def _water_fill(widths: list[float], usable: float, minimum: float = MIN_COLUMN_WIDTH) -> list[float]:
    """Source-proportional shares; columns below the minimum are lifted to it."""
    pinned: set[int] = set()
    while True:
        free = [i for i in range(len(widths)) if i not in pinned]
        room = usable - minimum * len(pinned)
        total = sum(widths[i] for i in free)
        shares = [minimum if i in pinned else room * widths[i] / total for i in range(len(widths))]
        low = {i for i in free if shares[i] < minimum}
        if not low or len(pinned | low) == len(widths):
            return [max(share, minimum) for share in shares] if low else shares
        pinned |= low


def _item_count(node) -> int:
    kind, children = node
    return len(children) if kind == "leaf" else sum(_item_count(child) for child in children)


def _icon_column_width(node) -> float | None:
    kind, children = node
    if kind != "leaf" or not all(item.kind == "image" and item.is_icon for item in children):
        return None
    return sum(item.natural_width for item in children) + ICON_GAP * (len(children) - 1)