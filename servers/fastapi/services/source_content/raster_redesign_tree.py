"""Layout tree for raster redesign pages.

Leaves have a fixed height (text, cards, tables) or a height that grows with
one shared image scale ``t`` (image rows). Stacks and rows combine them, so
one monotone bisection on ``t`` gives the largest captures that still fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.source_content.raster_redesign_table import TableLayout
from services.source_content.raster_redesign_text import TextLine

MIN_IMAGE_HEIGHT = 90.0
ICON_MAX_PX = 64  # crops at most this tall (source px) are treated as icons
MAX_UPSCALE = 1.25  # target units per source pixel (1 unit = 0.25 mm), avoids blurry crops
IMAGE_GAP = 10.0


@dataclass(frozen=True)
class Item:
    kind: str  # "text" | "table" | "image"
    region: object
    paragraphs: tuple = ()
    rows: tuple = ()
    aspect: float = 1.0  # height / width
    native_height: float | None = None  # source crop height in pixels
    # Hard legibility minimum as a fraction of native pixels (e.g. 0.6 for an
    # unresolved table or a dense-group fallback crop); 0 means none.
    min_scale: float = 0.0
    # A pixel line (one source text line kept as pixels): placed inline in the
    # text flow at a text-matched height, left-aligned, never in a gallery.
    inline: bool = False
    text_px: float | None = None  # measured glyph height of the line's text (source px)
    # Largest scale (target units per source pixel); None means MAX_UPSCALE. Pixels
    # standing in for text are never magnified beyond their size on the source page.
    max_scale: float | None = None
    # Table only: its pixel cells (raster_redesign_table.CellImage), drawn inside its grid.
    pixel_cells: tuple = ()

    @property
    def is_icon(self) -> bool:
        """A small glyph/icon crop: shown at natural size, never as a screen."""
        return self.kind == "image" and self.native_height is not None and self.native_height <= ICON_MAX_PX

    @property
    def upscale(self) -> float:
        return MAX_UPSCALE if self.max_scale is None else self.max_scale

    @property
    def natural_width(self) -> float:
        return (self.native_height or MIN_IMAGE_HEIGHT) * self.upscale / self.aspect


def scale_compatible(members) -> bool:
    """Whether members can share one row height: every legibility minimum under every cap."""
    required = max((m.min_scale * (m.native_height or 0.0) for m in members), default=0.0)
    caps = [m.native_height * m.upscale for m in members if m.native_height]
    return not caps or required <= min(caps) + 0.01


def compatible_runs(members) -> list[list]:
    """Consecutive runs of ``members`` that can each share one row height (reading order kept)."""
    runs: list[list] = []
    for member in members:
        if runs and scale_compatible([*runs[-1], member]):
            runs[-1].append(member)
        else:
            runs.append([member])
    return runs


@dataclass
class Placed:
    kind: str  # "text" | "card" | "table" | "image"
    x: float
    y: float
    w: float
    h: float
    item: Item | None = None
    lines: list[TextLine] = field(default_factory=list)
    table: TableLayout | None = None
    part: int = 0


@dataclass
class Fixed:
    height: float
    placements: list[Placed]  # y relative to the leaf top


@dataclass
class ImageRow:
    items: list[Item]
    x: float
    w: float
    # Smallest legible height for this row; None means MIN_IMAGE_HEIGHT.
    floor: float | None = None

    def row_height(self, t: float) -> float:
        cap = self.cap
        required = max((item.min_scale * (item.native_height or 0.0) for item in self.items), default=0.0)
        if required > cap + 0.01:
            return float("inf")  # cannot be shown legibly at this width: infeasible
        floor = min(cap, max(required, MIN_IMAGE_HEIGHT if self.floor is None else self.floor))
        return min(max(t, floor), cap)

    @property
    def inverse(self) -> float:
        return sum(1.0 / item.aspect for item in self.items)

    @property
    def cap(self) -> float:
        width_cap = (self.w - IMAGE_GAP * (len(self.items) - 1)) / self.inverse
        natives = [item.native_height * item.upscale for item in self.items if item.native_height]
        return min([width_cap, *natives])


@dataclass
class Stack:
    children: list
    gap: float


@dataclass
class Row:
    children: list


@dataclass
class Panel:
    """Card background around a child; stretched to its row's height."""

    child: object
    x: float
    w: float
    pad: float
    style: str = "panel"  # "panel" (outlined group) or "card" (semantic card)


def height(node, t: float) -> float:
    if isinstance(node, Fixed):
        return node.height
    if isinstance(node, Panel):
        return height(node.child, t) + node.pad * 2
    if isinstance(node, ImageRow):
        return node.row_height(t)
    if isinstance(node, Stack):
        return sum(height(child, t) for child in node.children) + node.gap * max(0, len(node.children) - 1)
    return max((height(child, t) for child in node.children), default=0.0)


def image_rows(node) -> list[ImageRow]:
    if isinstance(node, ImageRow):
        return [node]
    if isinstance(node, Panel):
        return image_rows(node.child)
    if isinstance(node, (Stack, Row)):
        return [row for child in node.children for row in image_rows(child)]
    return []


def solve(node, available: float) -> float | None:
    """Largest image scale that fits, or None when even the minimum overflows."""

    rows = image_rows(node)
    if height(node, 0.0) > available + 0.01:  # every capture at its legibility floor
        return None
    if not rows:
        return 0.0
    low, high = 0.0, max(row.cap for row in rows)
    for _ in range(40):
        middle = (low + high) / 2
        if height(node, middle) <= available:
            low = middle
        else:
            high = middle
    return low


def place(node, y: float, t: float, stretch: float | None = None) -> list[Placed]:
    """Resolve absolute positions for scale ``t``."""

    if isinstance(node, Panel):
        natural = height(node, t)
        own = max(stretch, natural) if stretch is not None else natural
        # Image-only panels stretched to a taller row keep their capture centred.
        offset = (own - natural) / 2 if not _has_fixed(node.child) else 0.0
        return [Placed(node.style, node.x, y, node.w, own)] + place(node.child, y + node.pad + offset, t)
    if isinstance(node, Fixed):
        result = []
        for item in node.placements:
            result.append(Placed(item.kind, item.x, item.y + y, item.w, item.h, item.item,
                                 item.lines, item.table, item.part))
        return result
    if isinstance(node, ImageRow):
        row_height = node.row_height(t)
        row_width = row_height * node.inverse + IMAGE_GAP * (len(node.items) - 1)
        left = node.x + (node.w - row_width) / 2
        result = []
        for item in node.items:
            width = row_height / item.aspect
            result.append(Placed("image", left, y, width, row_height, item))
            left += width + IMAGE_GAP
        return result
    if isinstance(node, Stack):
        result, cursor = [], y
        for child in node.children:
            result.extend(place(child, cursor, t))
            cursor += height(child, t) + node.gap
        return result
    row_height = height(node, t)
    return [placed for child in node.children for placed in place(child, y, t, row_height)]


def _has_fixed(node) -> bool:
    if isinstance(node, Fixed):
        return True
    if isinstance(node, Panel):
        return _has_fixed(node.child)
    if isinstance(node, (Stack, Row)):
        return any(_has_fixed(child) for child in node.children)
    return False


def image_area(node, t: float) -> float:
    return sum(row.row_height(t) ** 2 * row.inverse for row in image_rows(node))
