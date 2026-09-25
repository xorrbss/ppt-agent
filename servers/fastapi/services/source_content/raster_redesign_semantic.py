"""Semantic redesign composition for structured raster pages.

When the analysis groups regions (``RasterRegion.group_id``), each group is a
card: optional icon + bold heading + body paragraphs + optional capture or
table. Cards flow in a responsive grid chosen by count; standalone screenshots
go to a gallery band or a text/capture split; standalone tables and text are
full-width bands. Source reading order is kept. The source geometry is not
replayed (that is the XY-cut fallback for ungrouped analyses).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from services.source_content.raster_redesign_dedupe import same_line
from services.source_content.raster_redesign_pixel_lines import group_same_line, pixel_line
from services.source_content.raster_redesign_tree import Fixed, ImageRow, Item, Panel, Placed, Row, Stack, height

CARD_ACCENT = 4.0
CARD_GAP_X, CARD_GAP_Y, BAND_GAP, INNER_GAP = 12.0, 10.0, 10.0, 5.0
ICON_GAP = 8.0
SPLIT_GAP = 24.0


@dataclass(frozen=True)
class Block:
    kind: str  # "card" | "text" | "table" | "image"
    items: tuple[Item, ...]


def group_of(item: Item):
    return getattr(item.region, "group_id", None)


def has_groups(items) -> bool:
    return any(group_of(item) for item in items)


def blocks(items, headings: bool = False) -> list[Block]:
    """Group items into cards (by group_id, first-occurrence order) and standalone blocks.

    With ``headings``, a heading-only group whose neighbours are not also
    heading-only becomes a plain section heading instead of a card.
    """
    order: list = []
    grouped: dict = {}
    for item in items:
        key = group_of(item)
        if key is None:
            order.append(item)
        elif key not in grouped:
            grouped[key] = [item]
            order.append(key)
        else:
            grouped[key].append(item)
    result = []
    candidates = [not isinstance(entry, Item) and _section_heading(grouped[entry]) for entry in order]
    short = [not isinstance(entry, Item) and _line_count(grouped[entry]) <= 3 for entry in order]
    for index, entry in enumerate(order):
        if isinstance(entry, Item):
            result.append(Block("inline" if entry.inline else entry.kind, (entry,)))
            continue
        members = tuple(grouped[entry])
        peers = (index > 0 and short[index - 1]) or (index + 1 < len(order) and short[index + 1])
        if headings and candidates[index] and not peers:
            # A heading-only group introduces the next cards: a plain heading band.
            result.extend(Block("text", (member,)) for member in members)
        elif any(member.kind == "text" or member.inline for member in members):  # pixel lines are text too
            result.append(Block("card", _visual_order(members)))
        else:  # a group without text is a set of captures/tables
            result.extend(Block("inline" if member.inline else member.kind, (member,)) for member in members)
    return _merge_line_blocks(result)


def _merge_line_blocks(parts):
    """Consecutive standalone pixel pieces on one source line form one inline block."""
    merged = []
    for part in parts:
        previous = merged[-1] if merged else None
        if (part.kind == "inline" and previous is not None and previous.kind == "inline"
                and same_line(previous.items[-1].region.source_pixel_bbox, part.items[0].region.source_pixel_bbox)):
            merged[-1] = Block("inline", (*previous.items, *part.items))
        else:
            merged.append(part)
    return merged


def signature(block: Block) -> tuple:
    """Cards of the same composition share one grid; a change starts a new run."""
    kinds = {item.kind for item in block.items if not (item.kind == "image" and item.is_icon)}
    return (block.kind, "image" in kinds, "table" in kinds)


def card_runs(parts) -> list[int]:
    runs, previous = [], None
    for part in parts:
        if part.kind == "card" and previous is not None and previous.kind == "card" \
                and signature(previous) == signature(part):
            runs[-1] += 1
        elif part.kind == "card":
            runs.append(1)
        previous = part
    return runs


def _line_count(members) -> int:
    return sum(len([line for line in (m.region.text or "").splitlines() if line.strip()])
               for m in members if m.kind == "text")


def _visual_order(members) -> tuple:
    """Inside one card: the analysis reading order (pixel lines sit between their text lines).

    Members without a reading order follow by source position.
    """
    def key(member):
        order = getattr(member.region, "reading_order", None)
        return (order is None, order if order is not None else 0, member.region.bbox.y, member.region.bbox.x)

    return tuple(sorted(members, key=key))


def _section_heading(members) -> bool:
    if any(member.kind != "text" for member in members):
        return False
    return _line_count(members) <= 2 and bool(members[0].paragraphs) and members[0].paragraphs[0].heading


def natural_columns(count: int) -> int:
    return count if count <= 4 else (3 if count in (5, 6, 9) else 4)


def semantic_configs(items) -> list[tuple]:
    parts = blocks(items, headings=True)
    cards = sum(part.kind == "card" for part in parts)
    captures = sum(part.kind == "image" and not part.items[0].is_icon for part in parts)
    columns = sorted({natural_columns(cards), 1, 2, 3, 4} if cards > 1 else {1})
    configs = [(mode, cols, pad, None) for mode in ("semantic-flow", "semantic-flow-h")
               for cols in columns for pad in (14.0, 8.0, 5.0)]
    configs += [("semantic-masonry", cols, pad, None) for cols in columns if cols > 1 for pad in (8.0, 5.0)]
    if captures > 2:
        configs += [(mode, cols, 5.0, None) for mode in ("semantic-dense", "semantic-dense-m") for cols in columns]
    if captures and len(parts) > captures:
        configs += [(mode, cols, 8.0, ratio) for mode in ("semantic-split", "semantic-split-m")
                    for cols in (1, 2) for ratio in (0.46, 0.56, 0.64)]
    if len(sections(parts)[1]) >= 2:
        configs += [("semantic-sections", cols, pad, None) for cols in (1, 2) for pad in (14.0, 8.0)]
    return configs


def sections(parts):
    """(preamble, [section parts...]): each heading band starts a section."""
    preamble, found = [], []
    for part in parts:
        if part.kind == "text" and part.items[0].paragraphs and part.items[0].paragraphs[0].heading \
                and group_of(part.items[0]) is not None:
            found.append([part])
        elif found:
            found[-1].append(part)
        else:
            preamble.append(part)
    return preamble, found


def _sections_layout(parts, x, w, f, tf, pad, columns, cache):
    """Sections side by side (up to 3 per row), each a heading over its own cards."""
    preamble, found = sections(parts)
    bands = []
    if preamble:
        node = _flow(preamble, x, w, f, tf, pad, 4, cache)
        if node is None:
            return None
        bands.append(node)
    per_row = min(3, len(found))
    width = (w - CARD_GAP_X * 2 * (per_row - 1)) / per_row
    for start in range(0, len(found), per_row):
        row = []
        for index, section in enumerate(found[start:start + per_row]):
            node = _flow(section, x + index * (width + CARD_GAP_X * 2), width, f, tf, pad, columns, cache)
            if node is None:
                return None
            row.append(node)
        bands.append(Row(row))
    return Stack(bands, BAND_GAP)


def _text_fixed(item, x, w, f, cache, part):
    laid = cache.paragraphs(item, w, f)
    if laid is None:
        return None
    lines, height = laid
    return Fixed(height, [Placed("text", x, 0.0, w, height, item, lines=lines, part=part)])


def _capture_floor(item: Item, width: float) -> float:
    """Legibility floor: shown at >=0.5x native pixels OR >=30% of the available width.

    Either condition keeps a capture legible, so the floor is the smaller one.
    """
    if item.min_scale:  # pixel-carried text: its own legibility minimum, never stretched to width
        return item.min_scale * (item.native_height or 0.0)
    # A real screenshot/photo is a visual reference, not text: it may shrink to
    # 0.35x native pixels or 30% of the available width, whichever is smaller.
    native = (item.native_height or 0.0) * SCREENSHOT_MIN_SCALE
    return min(native, 0.3 * width * item.aspect) if native else 0.3 * width * item.aspect


PIXEL_LINE_TEXT_RATIO = 1.36
SCREENSHOT_MIN_SCALE = 0.35  # crop height per unit of font size (a text line box with its padding)


def _split_heading(item: Item, cache):
    """(heading item, body item) for a text item whose first paragraphs are headings."""
    store = cache.__dict__.setdefault("derived", {})
    key = item.region.region_id
    if key not in store:
        paragraphs = item.paragraphs
        split = next((i for i, p in enumerate(paragraphs) if not p.heading), len(paragraphs))
        heading = replace(item, paragraphs=paragraphs[:split]) if split else None
        body = replace(item, paragraphs=paragraphs[split:]) if split < len(paragraphs) else None
        store[key] = (heading, body)
    return store[key]


def _pack_icons(icons, width):
    """Icons in reading order, wrapped so each row can reach every member's min_scale."""
    rows, run = [], []
    for icon in icons:
        trial = [*run, icon]
        if run and not _row_reaches_minimum(trial, width):
            rows.append(run)
            trial = [icon]
        run = trial
    if run:
        rows.append(run)
    return rows


def _card(block: Block, x, w, f, tf, pad, cache):
    inner_x, inner_w = x + CARD_ACCENT + pad, w - CARD_ACCENT - 2 * pad
    if inner_w < 60:
        return None
    # Only true icons sit beside the heading; pixel-carried text (ink, tables) flows as content.
    icons = [item for item in block.items if item.kind == "image" and item.is_icon and not item.min_scale
             and not item.inline
             and item.aspect >= 0.5]  # roughly square glyphs only; wide text strips are content
    rest = [item for item in block.items if item not in icons]
    first_text = next((item for item in rest if item.kind == "text"), None)
    heading, body = _split_heading(first_text, cache) if first_text is not None else (None, None)
    children, part = [], 0
    icon_rows = _pack_icons(icons, inner_w)
    if len(icon_rows) > 1:  # icons that cannot share one legible row go on their own rows
        children.extend(ImageRow(row, inner_x, inner_w) for row in icon_rows)
        icons = []
    if icons:
        icon_w = sum(icon.natural_width for icon in icons) + ICON_GAP * (len(icons) - 1)
        icon_row = ImageRow(icons, inner_x, min(icon_w, inner_w))
        if heading is not None and inner_w - icon_w - ICON_GAP >= 60:
            head = _text_fixed(heading, inner_x + icon_w + ICON_GAP, inner_w - icon_w - ICON_GAP, f, cache, part)
            if head is None:
                return None
            children.append(Row([icon_row, head]))
            part, heading = part + 1, None
        else:
            children.append(icon_row)
    for item in group_same_line(rest):
        nodes = []
        if isinstance(item, tuple):  # pixel pieces of one source line
            node = pixel_line(list(item), inner_x, inner_w, f)
            if node is None:
                return None
            children.append(node)
            continue
        if item is first_text:
            for piece in (heading, body):
                if piece is not None:
                    nodes.append(_text_fixed(piece, inner_x, inner_w, f, cache, part))
                    part += 1
        elif item.kind == "text":
            nodes.append(_text_fixed(item, inner_x, inner_w, f, cache, part))
            part += 1
        elif item.kind == "table":
            grid = cache.grid(item, inner_w, tf)
            nodes.append(None if grid is None else Fixed(
                grid.height, [Placed("table", inner_x, 0.0, inner_w, grid.height, item, table=grid)]))

        else:
            nodes.append(ImageRow([item], inner_x, inner_w, floor=_capture_floor(item, inner_w)))
        if any(node is None for node in nodes):
            return None
        children.extend(nodes)
    return Panel(Stack(children, INNER_GAP), x, w, pad, style="card")

def _card_grid(cards, x, w, f, tf, pad, columns, cache):
    """Cards in reading order, ``columns`` per row.

    A card that cannot be laid out legibly at the column width (a wide pixel
    line or image table) spans the full row; the other cards keep the grid.
    """
    columns = max(1, min(columns, len(cards)))
    width = (w - CARD_GAP_X * (columns - 1)) / columns
    rows, run = [], []

    def flush():
        if run:
            rows.append(Row([_card(block, x + i * (width + CARD_GAP_X), width, f, tf, pad, cache)
                             for i, block in enumerate(run)]))
            run.clear()

    for block in cards:
        if columns > 1 and _card(block, x, width, f, tf, pad, cache) is None:
            flush()
            wide = _card(block, x, w, f, tf, pad, cache)
            if wide is None:
                return None
            rows.append(wide)
            continue
        run.append(block)
        if len(run) == columns:
            flush()
    flush()
    if any(isinstance(row, Row) and any(node is None for node in row.children) for row in rows):
        return None
    return Stack(rows, CARD_GAP_Y)


def _masonry(cards, x, w, f, tf, pad, columns, cache):
    """Cards flow column-major into ``columns`` balanced columns (contiguous runs keep reading order).

    A card too wide for a column breaks the flow and spans the full width.
    """
    columns = max(1, min(columns, len(cards)))
    width = (w - CARD_GAP_X * (columns - 1)) / columns
    bands, segment = [], []

    def flush():
        if not segment:
            return True
        nodes = [_card(block, 0.0, width, f, tf, pad, cache) for block in segment]
        if any(node is None for node in nodes):
            return False
        heights = [height(node, 0.0) + CARD_GAP_Y for node in nodes]
        cuts = _balanced_cuts(heights, min(columns, len(segment)))
        stacks, start = [], 0
        for index, stop in enumerate(cuts):
            left = x + index * (width + CARD_GAP_X)
            stacks.append(Stack([_card(block, left, width, f, tf, pad, cache) for block in segment[start:stop]],
                                CARD_GAP_Y))
            start = stop
        bands.append(Row(stacks))
        segment.clear()
        return True

    for block in cards:
        if columns > 1 and _card(block, x, width, f, tf, pad, cache) is None:
            if not flush():
                return None
            wide = _card(block, x, w, f, tf, pad, cache)
            if wide is None:
                return None
            bands.append(wide)
            continue
        segment.append(block)
    if not flush():
        return None
    return Stack(bands, CARD_GAP_Y)


def _balanced_cuts(heights, parts):
    """End indices of ``parts`` contiguous runs minimising the tallest run (small DP)."""
    count = len(heights)
    prefix = [0.0]
    for value in heights:
        prefix.append(prefix[-1] + value)
    best = {(0, 0): (0.0, [])}
    for used in range(1, parts + 1):
        for end in range(used, count - (parts - used) + 1):
            options = []
            for start in range(used - 1, end):
                if (used - 1, start) in best:
                    worst, cuts = best[(used - 1, start)]
                    options.append((max(worst, prefix[end] - prefix[start]), cuts + [end]))
            if options:
                best[(used, end)] = min(options, key=lambda option: option[0])
    return best[(parts, count)][1]


def _similar_scale(members) -> bool:
    """A shared row height is capped by its smallest member: keep native heights within 2x."""
    natives = [m.native_height for m in members if m.native_height]
    return not natives or max(natives) <= 2.0 * min(natives)


def _row_reaches_minimum(members, w) -> bool:
    """Whether a shared row height can satisfy every member's min_scale."""
    required = max((m.min_scale * (m.native_height or 0.0) for m in members), default=0.0)
    if not required:
        return True
    width_needed = sum(required / m.aspect for m in members) + 10.0 * (len(members) - 1)
    return width_needed <= w

def _gallery(images, x, w, dense=False):
    screens = [image for image in images if not image.is_icon]
    icons = [image for image in images if image.is_icon]
    per_row = 1 if len(screens) == 1 else 2 if len(screens) in (2, 4) else 3
    if dense:  # denser gallery for full pages: more, smaller (still legible) screens per row
        per_row = min(4, max(per_row + 1, len(screens)))
    if any(screen.min_scale for screen in screens):
        per_row = max(per_row, 5)  # width, not count, limits rows of minimum-scale crops
    # Pack in reading order, at most ``per_row`` per row; a crop with a hard
    # legibility minimum (table, dense group) only shares a row while every
    # member can still reach its minimum width.
    chunks, run = [], []
    for screen in screens:
        trial = [*run, screen]
        if run and (len(trial) > per_row or not _row_reaches_minimum(trial, w) or not _similar_scale(trial)):
            chunks.append(run)
            trial = [screen]
        run = trial
    if run:
        chunks.append(run)
    rows = []
    for members in chunks:
        slot = (w - 10.0 * (len(members) - 1)) / len(members)
        rows.append(ImageRow(members, x, w, floor=max(_capture_floor(item, slot) for item in members)))
    if icons:
        rows.append(ImageRow(icons, x, w))
    return Stack(rows, 10.0)


def _flow(parts, x, w, f, tf, pad, columns, cache, masonry=False, dense=False):
    """Bands in reading order: card runs as grids, capture runs as galleries."""
    bands, index = [], 0
    while index < len(parts):
        part = parts[index]
        run = [part]
        while index + len(run) < len(parts) and part.kind in ("card", "image") and (
                parts[index + len(run)].kind == part.kind
                and signature(parts[index + len(run)]) == signature(part)):
            run.append(parts[index + len(run)])
        index += len(run)
        if part.kind == "card":
            grid = _masonry if masonry else _card_grid
            node = grid(run, x, w, f, tf, pad, columns if len(run) > 1 else 1, cache)
        elif part.kind == "image":
            node = _gallery([item for block in run for item in block.items], x, w, dense)
        elif part.kind == "inline":
            node = pixel_line(list(part.items), x, w, f)
        elif part.kind == "table":
            grid = cache.grid(part.items[0], w, tf)
            node = None if grid is None else Fixed(grid.height, [Placed("table", x, 0.0, w, grid.height,
                                                                        part.items[0], table=grid)])
        else:
            node = _text_fixed(part.items[0], x, w, f, cache, 0)
        if node is None:
            return None
        bands.append(node)
    return Stack(bands, BAND_GAP)


def build_semantic(mode, columns, pad, ratio, items, x, w, f, tf, cache):
    parts = blocks(items, headings=mode != "semantic-flow")
    if mode in ("semantic-flow", "semantic-flow-h"):
        return _flow(parts, x, w, f, tf, pad, columns, cache)
    if mode in ("semantic-dense", "semantic-dense-m"):
        return _flow(parts, x, w, f, tf, pad, columns, cache, masonry=mode.endswith("-m"), dense=True)
    if mode == "semantic-masonry":
        return _flow(parts, x, w, f, tf, pad, columns, cache, masonry=True)
    if mode == "semantic-sections":
        return _sections_layout(parts, x, w, f, tf, pad, columns, cache)
    captures = [part for part in parts if part.kind == "image" and not part.items[0].is_icon]
    others = [part for part in parts if part not in captures]
    left_w = (w - SPLIT_GAP) * ratio
    left = _flow(others, x, left_w, f, tf, pad, columns, cache, masonry=mode == "semantic-split-m")
    right = _gallery([part.items[0] for part in captures], x + left_w + SPLIT_GAP, w - SPLIT_GAP - left_w)
    return None if left is None else Row([left, right])
