"""Candidate composition builders: text/table/image leaves inside a band, and
the stack/split/sections/semantic layout modes searched by ``plan_content``.
"""

from __future__ import annotations

from services.source_content.raster_redesign_plan_constants import (
    BAND_GAP,
    BODY_TEXT,
    CARD_ACCENT,
    CARD_PAD,
    GRID_GAP_X,
    GRID_GAP_Y,
    NAVY,
)
from services.source_content.raster_redesign_plan_items import _order_key
from services.source_content.raster_redesign_sections import build_sections, xy_tree
from services.source_content.raster_redesign_semantic import build_semantic, natural_columns
from services.source_content.raster_redesign_table import layout_table
from services.source_content.raster_redesign_text import (
    column_height,
    layout_paragraphs,
    split_lines_into_columns,
)
from services.source_content.raster_redesign_tree import height as tree_height
from services.source_content.raster_redesign_tree import (
    Fixed,
    ImageRow,
    Item,
    Placed,
    Row,
    Stack,
    scale_compatible,
)


class _Cache:
    def __init__(self):
        self.text, self.table = {}, {}

    def paragraphs(self, item, width, size):
        key = (id(item), round(width, 2), size)
        if key not in self.text:
            try:
                self.text[key] = layout_paragraphs(
                    list(item.paragraphs), width, size, body_color=BODY_TEXT, heading_color=NAVY,
                )
            except ValueError:
                self.text[key] = None
        return self.text[key]

    def grid(self, item, width, size):
        key = (id(item), round(width, 2), size)
        if key not in self.table:
            self.table[key] = layout_table(item.rows, width, size, item.pixel_cells)
        return self.table[key]


def _bands(items):
    bands: list[tuple[str, list[Item]]] = []
    for item in items:
        if bands and bands[-1][0] == item.kind and item.kind != "table":
            bands[-1][1].append(item)
        else:
            bands.append((item.kind, [item]))
    return bands


def _text_leaf(items, x, w, f, k, cache, *, cards: bool):
    if len(items) == 1 or not cards:
        if len(items) > 1:
            return None
        column_width = (w - GRID_GAP_X * (k - 1)) / k
        laid = cache.paragraphs(items[0], column_width, f)
        if laid is None:
            return None
        parts = split_lines_into_columns(laid[0], k)
        placed = [Placed("text", x + i * (column_width + GRID_GAP_X), 0.0, column_width,
                         column_height(part), items[0], lines=part, part=i)
                  for i, part in enumerate(parts)]
        return Fixed(max(p.h for p in placed), placed)
    if k > len(items):
        return None
    card_width = (w - GRID_GAP_X * (k - 1)) / k
    inner = card_width - CARD_PAD * 2 - CARD_ACCENT
    placed, cursor = [], 0.0
    for start in range(0, len(items), k):
        row = items[start:start + k]
        laid = [cache.paragraphs(item, inner, f) for item in row]
        if any(value is None for value in laid):
            return None
        row_height = max(value[1] for value in laid) + CARD_PAD * 2
        for i, (item, value) in enumerate(zip(row, laid, strict=True)):
            cx = x + i * (card_width + GRID_GAP_X)
            placed.append(Placed("card", cx, cursor, card_width, row_height, item))
            placed.append(Placed("text", cx + CARD_ACCENT + CARD_PAD, cursor + CARD_PAD, inner,
                                 value[1], item, lines=value[0]))
        cursor += row_height + GRID_GAP_Y
    return Fixed(cursor - GRID_GAP_Y, placed)


def _image_rows(group, x, w, per_row):
    """Screens in rows of ``per_row``; runs of icons packed into compact rows."""
    rows, run = [], []
    for is_icon, members in _runs(group):
        if not is_icon:
            rows.extend(ImageRow(members[i:i + per_row], x, w) for i in range(0, len(members), per_row))
            continue
        used = 0.0
        for icon in members:
            need = icon.natural_width + (6.0 if run else 0.0)
            if run and (used + need > w or not scale_compatible([*run, icon])):
                rows.append(ImageRow(run, x, w))
                run, used, need = [], 0.0, icon.natural_width
            run.append(icon)
            used += need
        if run:
            rows.append(ImageRow(run, x, w))
            run = []
    return rows


def _runs(group):
    runs: list[tuple[bool, list]] = []
    for item in group:
        if runs and runs[-1][0] == item.is_icon:
            runs[-1][1].append(item)
        else:
            runs.append((item.is_icon, [item]))
    return runs


def _stack(items, x, w, f, tf, k, per_row, cache, *, cards=True):
    children = []
    for kind, group in _bands(items):
        if kind == "text":
            if not cards and len(group) > 1:
                leaves = [_text_leaf([item], x, w, f, 1, cache, cards=False) for item in group]
                if any(leaf is None for leaf in leaves):
                    return None
                children.append(Stack(leaves, GRID_GAP_Y))
                continue
            leaf = _text_leaf(group, x, w, f, k, cache, cards=cards)
        elif kind == "table":
            grid = cache.grid(group[0], w, tf)
            leaf = None if grid is None else Fixed(
                grid.height, [Placed("table", x, 0.0, w, grid.height, group[0], table=grid)],
            )
        else:
            leaf = Stack(_image_rows(group, x, w, per_row), 10.0)
        if leaf is None:
            return None
        children.append(leaf)
    return Stack(children, BAND_GAP)


def _configs(items):
    images = [item for item in items if item.kind == "image"]
    texts = [item for item in items if item.kind != "image"]
    configs = [("sections", 1, 1, None)]
    for k in (1, 2, 3):
        for per_row in range(1, min(3, max(1, len(images))) + 1):
            configs.append(("stack", k, per_row, None))
    if texts and images:
        for ratio in (0.36, 0.44, 0.52, 0.6):
            for per_row in (1, 2):
                if per_row <= len(images):
                    configs.append(("split", 1, per_row, ratio))
    return configs


def _build(mode, k, per_row, ratio, items, box, f, tf, cache, source_width):
    x, _, w, _ = box
    if mode.startswith("semantic"):
        return build_semantic(mode, k, per_row, ratio, items, x, w, f, tf, cache)
    if mode == "sections":
        tree = xy_tree(items, source_width[0], source_width[1], lambda item: _order_key(item.region))
        return build_sections(
            tree, x, w, lambda group, gx, gw: _stack(group, gx, gw, f, tf, 1, min(3, len(group)), cache, cards=False),
        )
    if mode == "stack":
        return _stack(items, x, w, f, tf, k, per_row, cache)
    texts = [item for item in items if item.kind != "image"]
    images = [item for item in items if item.kind == "image"]
    left_w = (w - 28.0) * ratio
    if left_w / f < 14:
        return None
    left = _stack(texts, x, left_w, f, tf, 1, 1, cache)
    right = _stack(images, x + left_w + 28.0, w - 28.0 - left_w, f, tf, 1, per_row, cache)
    return None if left is None or right is None else Row([left, right])


def _split_imbalance(node, t, available) -> float:
    """A split whose columns end far apart leaves a hole; penalise the gap."""
    left, right = (tree_height(child, t) for child in node.children)
    return 0.8 * abs(left - right) / available


def _grid_penalty(runs, columns) -> float:
    """Prefer each card run's natural grid; penalise orphan cards in a last row."""
    penalty = 0.0
    for count in runs:
        if count < 2:
            continue
        used = min(columns, count)
        penalty += 0.15 * abs(used - natural_columns(count)) + (0.1 if count % used else 0.0)
    return penalty
