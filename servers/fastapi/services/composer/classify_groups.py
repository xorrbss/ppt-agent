"""Grouping rules: columns, comparisons, processes, lists and captions.

Everything here reads geometry and markers already on the ``TextBlock`` views;
no rule consults a model. Thresholds are module constants so a change is one
line and one test.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations
import re
from typing import Any, Literal

from services.composer.classify_bindings import TextBlock, block_lines
from services.composer.classify_common import (
    ARROW_GLYPHS,
    Geometry,
    PageBox,
    field,
    mapping_or_empty,
    overlap_ratio,
)

#: Blocks whose x-ranges overlap this share of the narrower one share a column.
COLUMN_OVERLAP = 0.40
#: Columns (or a row of steps) must share this share of the taller extent.
ROW_OVERLAP = 0.50
#: A block at least this wide is full-width copy, not a column member.
WIDE_SHARE = 0.60
MAX_COLUMNS = 3
HEADER_CHARS = 60
CAPTION_CHARS = 80
CAPTION_GAP = 0.05

_ARROW_LINE = re.compile(rf"^[{re.escape(ARROW_GLYPHS)}\s]+$")
_ARROW_SHAPE = re.compile(r"arrow|chevron|homeplate|connector|화살표|연결선", re.IGNORECASE)
_ENUMERATOR = re.compile(r"^(?:(?P<digits>\d{1,2})[.)]|(?P<circled>[①-⑳⑴-⒇⒈-⒛❶-❿]))")
_STEP = re.compile(r"^(?:step|단계)\s*(?P<n>\d{1,2})\b|^(?P<k>\d{1,2})\s*단계\b", re.IGNORECASE)
_CIRCLED_BASES = ((0x2460, 0x2473), (0x2474, 0x2487), (0x2488, 0x249B), (0x2776, 0x277F))

MarkerKind = Literal["enumerator", "arrow", "bullet"]
ListKind = Literal["bullets", "numbered"]


def marker_kind(marker: str) -> MarkerKind | None:
    marker = marker.strip()
    if not marker:
        return None
    if _ENUMERATOR.match(marker):
        return "enumerator"
    if marker[0] in ARROW_GLYPHS:
        return "arrow"
    return "bullet"


# --- Per-block kinds -----------------------------------------------------------


def list_kind(block: TextBlock) -> ListKind | None:
    """Rule 6: a block is a list when at least half of its lines are items.

    Items are paragraphs the source marked as bullets (``bullet``,
    ``bulletKind``) or runs the projection peeled a bullet or enumerator from.
    The two signals describe the same paragraphs, so the larger count wins
    rather than their sum.
    """

    meta_numbered = sum(
        1 for item in block.paragraphs_meta if item.get("bullet") and item.get("bulletKind") == "auto-number"
    )
    meta_bulleted = sum(1 for item in block.paragraphs_meta if item.get("bullet")) - meta_numbered
    kinds = [marker_kind(run.marker) for run in block.projection.runs]
    run_numbered = kinds.count("enumerator")
    run_bulleted = kinds.count("bullet")
    items = max(meta_numbered + meta_bulleted, run_numbered + run_bulleted)
    if items == 0 or items * 2 < block.line_count:
        return None
    numbered = meta_numbered + run_numbered >= meta_bulleted + run_bulleted
    return "numbered" if numbered else "bullets"


def is_arrow_flow(block: TextBlock) -> bool:
    """Rule 5a: runs joined by arrows, or arrow-only lines the projection dropped."""

    projection = block.projection
    if projection.run_count < 2:
        return False
    runs = projection.runs
    for previous, current in zip(runs, runs[1:], strict=False):
        between = projection.text[previous.end : current.begin]
        if any(glyph in between for glyph in ARROW_GLYPHS) or marker_kind(current.marker) == "arrow":
            return True
    return any(_ARROW_LINE.match(line) for line in block_lines(block))


def ordinal_start(block: TextBlock) -> int | None:
    """Rule 5c: the ordinal a block opens with (``①``, ``1.``, ``STEP 1``, ``1단계``)."""

    first = block.projection.runs[0]
    match = _ENUMERATOR.match(first.marker.strip())
    if match is not None:
        if match.group("digits"):
            return int(match.group("digits"))
        return _circled_value(match.group("circled"))
    step = _STEP.match(first.body)
    if step is not None:
        return int(step.group("n") or step.group("k"))
    return None


def _circled_value(glyph: str) -> int | None:
    code = ord(glyph)
    for start, end in _CIRCLED_BASES:
        if start <= code <= end:
            return code - start + 1
    return None


def is_arrow_shape(block: Mapping[str, Any]) -> bool:
    """A connector or an arrow-like preset/name on a ``shape`` block."""

    if str(field(block, "kind", default="") or "") != "shape":
        return False
    if str(field(block, "semantic_role", "semanticRole", default="") or "") == "connector":
        return True
    payload = mapping_or_empty(field(block, "payload", default={}))
    preset = str(payload.get("preset") or "")
    name = str(payload.get("shapeName") or "")
    return bool(_ARROW_SHAPE.search(preset) or _ARROW_SHAPE.search(name))


# --- Geometry clustering ---------------------------------------------------------


def _clusters(blocks: Sequence[TextBlock], connected) -> list[list[TextBlock]]:
    parent = list(range(len(blocks)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in combinations(range(len(blocks)), 2):
        if connected(blocks[left].geometry, blocks[right].geometry):
            parent[root(left)] = root(right)
    groups: dict[int, list[TextBlock]] = {}
    for index, block in enumerate(blocks):
        groups.setdefault(root(index), []).append(block)
    return list(groups.values())


def _x_connected(left: Geometry, right: Geometry) -> bool:
    return overlap_ratio(left.x, left.right, right.x, right.right) >= COLUMN_OVERLAP


def _y_connected(left: Geometry, right: Geometry) -> bool:
    return overlap_ratio(left.y, left.bottom, right.y, right.bottom, by="max") >= ROW_OVERLAP


def column_clusters(blocks: Sequence[TextBlock]) -> list[list[TextBlock]]:
    """Columns left to right, each top to bottom; blocks need geometry."""

    clusters = _clusters(blocks, _x_connected)
    for cluster in clusters:
        cluster.sort(key=lambda block: (block.geometry.y, block.geometry.x, block.order))
    clusters.sort(key=lambda cluster: min(block.geometry.x for block in cluster))
    return clusters


def narrow_blocks(blocks: Sequence[TextBlock], page: PageBox | None) -> list[TextBlock]:
    if page is None:
        return []
    return [
        block for block in blocks if block.geometry is not None and block.geometry.cx < WIDE_SHARE * page.width
    ]


def _y_extent(blocks: Sequence[TextBlock]) -> tuple[float, float]:
    return min(block.geometry.y for block in blocks), max(block.geometry.bottom for block in blocks)


def detect_comparison(blocks: Sequence[TextBlock], page: PageBox | None) -> list[list[TextBlock]] | None:
    """Rule 4: two or three side-by-side columns with matching vertical extents."""

    narrow = narrow_blocks(blocks, page)
    if len(narrow) < 2:
        return None
    columns = column_clusters(narrow)
    if not 2 <= len(columns) <= MAX_COLUMNS:
        return None
    extents = [_y_extent(column) for column in columns]
    for (top_a, bottom_a), (top_b, bottom_b) in combinations(extents, 2):
        if overlap_ratio(top_a, bottom_a, top_b, bottom_b, by="max") < ROW_OVERLAP:
            return None
    return columns


def group_header(column: Sequence[TextBlock]) -> TextBlock | None:
    """The first block of a column is its header when short and set heavier or larger."""

    if len(column) < 2:
        return None
    head, *rest = column
    if head.char_count > HEADER_CHARS or head.projection.run_count != 1:
        return None
    rest_sizes = [block.dominant_size_pt for block in rest if block.dominant_size_pt]
    larger = bool(head.dominant_size_pt and rest_sizes and head.dominant_size_pt > max(rest_sizes))
    not_smaller = not head.dominant_size_pt or not rest_sizes or head.dominant_size_pt >= max(rest_sizes)
    if larger or (head.bold_share >= 0.5 and not_smaller):
        return head
    return None


def detect_process(
    blocks: Sequence[TextBlock], shapes: Sequence[Mapping[str, Any]], page: PageBox | None
) -> tuple[list[list[TextBlock]], list[TextBlock]] | None:
    """Rules 5b/5c: ordinal-led blocks, or aligned blocks joined by arrow shapes.

    Returns the step groups and the blocks left over. Ordinal steps follow
    their numbering; arrow-joined steps follow their position along the flow
    (left to right, or top to bottom), which is the order the source reads.
    """

    numbered = [(ordinal_start(block), block) for block in blocks]
    steps = [(number, block) for number, block in numbered if number is not None]
    if len(steps) >= 2 and len({number for number, _ in steps}) == len(steps):
        ordered = [block for _, block in sorted(steps, key=lambda item: (item[0], item[1].order))]
        return _attach_descriptions(ordered, [block for block in blocks if block not in ordered])
    if page is None or not any(is_arrow_shape(shape) for shape in shapes):
        return None
    chain = _largest_aligned_chain(narrow_blocks(blocks, page))
    if len(chain) < 2:
        return None
    return _attach_descriptions(chain, [block for block in blocks if block not in chain])


def _largest_aligned_chain(blocks: Sequence[TextBlock]) -> list[TextBlock]:
    rows = _clusters(blocks, _y_connected)
    columns = _clusters(blocks, _x_connected)
    best_row = max(rows, key=len, default=[])
    best_column = max(columns, key=len, default=[])
    if len(best_row) >= len(best_column):
        return sorted(best_row, key=lambda block: (block.geometry.x, block.geometry.y, block.order))
    return sorted(best_column, key=lambda block: (block.geometry.y, block.geometry.x, block.order))


def _attach_descriptions(
    steps: Sequence[TextBlock], others: Sequence[TextBlock]
) -> tuple[list[list[TextBlock]], list[TextBlock]]:
    """Attach a block to the step above it in the same column; the rest stay loose."""

    groups: list[list[TextBlock]] = [[step] for step in steps]
    leftovers: list[TextBlock] = []
    for block in others:
        home: int | None = None
        if block.geometry is not None:
            candidates = [
                (step.geometry.y, index)
                for index, step in enumerate(steps)
                if step.geometry is not None
                and step.geometry.y <= block.geometry.y
                and _x_connected(step.geometry, block.geometry)
            ]
            home = max(candidates)[1] if candidates else None
        if home is None:
            leftovers.append(block)
        else:
            groups[home].append(block)
    for group in groups:
        group[1:] = sorted(group[1:], key=lambda block: (block.geometry.y, block.geometry.x, block.order))
    return groups, leftovers


def detect_captions(
    blocks: Sequence[TextBlock], images: Sequence[Geometry], page: PageBox | None
) -> dict[str, Geometry]:
    """Short one-line blocks directly under an image, keyed by block id."""

    captions: dict[str, Geometry] = {}
    if page is None:
        return captions
    for block in blocks:
        if block.geometry is None or block.char_count > CAPTION_CHARS or block.line_count != 1:
            continue
        for image in images:
            gap = (block.geometry.y - image.bottom) / page.height
            beside = overlap_ratio(block.geometry.x, block.geometry.right, image.x, image.right) >= 0.5
            if 0 <= gap <= CAPTION_GAP and beside:
                captions[block.block_id] = image
                break
    return captions


__all__ = [
    "COLUMN_OVERLAP",
    "ROW_OVERLAP",
    "WIDE_SHARE",
    "column_clusters",
    "detect_captions",
    "detect_comparison",
    "detect_process",
    "group_header",
    "is_arrow_flow",
    "is_arrow_shape",
    "list_kind",
    "marker_kind",
    "narrow_blocks",
    "ordinal_start",
]
