"""Reading order and the lossless content ledger for redesigned raster pages.

The ledger is a pure function of a sealed analysis. It proves that every
promoted OCR token on a redesign page is carried exactly once: either as
editable text (title/body/table cell) or by the pixels of a preserved crop
(screenshot/logo). Consumers use it to check that nothing was dropped,
duplicated, reordered, or had its numbers rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from services.source_content.raster_contracts import RasterAnalysis, RasterRegion

TEXT_CARRIER_ROLES = frozenset({"title", "body"})
PIXEL_CARRIER_ROLES = frozenset({"screenshot", "logo"})
_NUMBER = re.compile(r"\d[\d,.]*%?")

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class LedgerEntry:
    region_id: str
    role: str
    # "text" (title/body), "table_cells" (structured table), or "pixels"
    # (screenshot/logo crop, or a table kept as an image fallback).
    carrier: str
    reading_order: int | None
    token_indices: tuple[int, ...]
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RedesignContentLedger:
    page_number: int
    entries: tuple[LedgerEntry, ...]
    unpromoted_indices: tuple[int, ...]
    # Promoted inventory indices that no region carries (content loss).
    missing_indices: tuple[int, ...]
    # Indices carried by more than one region (duplication).
    duplicated_indices: tuple[int, ...]
    # Human-readable violations; empty means the page is lossless.
    violations: tuple[str, ...]

    @property
    def lossless(self) -> bool:
        return not self.violations


def _compact(value: str) -> str:
    return "".join(value.split())


def _carrier(region: RasterRegion) -> str:
    if region.editable_fallback:
        return "pixels"
    if region.role in TEXT_CARRIER_ROLES:
        return "text"
    if region.role == "table" and region.table_cells is not None:
        return "table_cells"
    return "pixels"


def _rendered_text(region: RasterRegion) -> str | None:
    if region.editable_fallback:
        return None  # rendered from source pixels; text is only a suggestion
    if region.role in TEXT_CARRIER_ROLES:
        return region.text or ""
    if region.role == "table" and region.table_cells is not None:
        return "".join(cell for row in region.table_cells for cell in row)
    return None


def redesign_content_ledger(
    analysis: RasterAnalysis, page_number: int, pixel_region_ids: Iterable[str] = ()
) -> RedesignContentLedger:
    """Account for every promoted OCR token on one redesign page.

    ``pixel_region_ids`` names regions a renderer replaced by their source
    pixels (e.g. ``replacedRegionIds`` of a dense-group pixel fallback); their
    tokens then count as carried by pixels, so the final output can be checked.
    """

    page = analysis.pages[page_number - 1]
    replaced = set(pixel_region_ids)
    page = page.model_copy(
        update={
            "regions": tuple(
                region.model_copy(update={"editable_fallback": True}) if region.region_id in replaced else region
                for region in page.regions
            )
        }
    )
    inventory = page.ocr_inventory
    unpromoted = set(page.unpromoted_ocr_indices)
    seen: dict[int, int] = {}
    entries: list[LedgerEntry] = []
    violations: list[str] = []
    ordered = sorted(page.regions, key=lambda r: (r.reading_order is None, r.reading_order or 0, r.region_id))
    for region in ordered:
        indices = tuple(region.ocr_token_indices or ())
        if indices and region.role not in TEXT_CARRIER_ROLES | PIXEL_CARRIER_ROLES | {"table"}:
            violations.append(f"{region.region_id}:role_cannot_carry_text")
        for index in indices:
            if index >= len(inventory):
                violations.append(f"{region.region_id}:token_index_out_of_range")
                continue
            seen[index] = seen.get(index, 0) + 1
        tokens = tuple(inventory[i].text for i in indices if i < len(inventory))
        rendered = _rendered_text(region)
        if rendered is not None:
            if _compact(rendered) != "".join(_compact(token) for token in tokens):
                violations.append(f"{region.region_id}:text_does_not_match_source_tokens")
            if _NUMBER.findall(_compact(rendered)) != _NUMBER.findall("".join(_compact(t) for t in tokens)):
                violations.append(f"{region.region_id}:numbers_not_verbatim")
        entries.append(
            LedgerEntry(region.region_id, region.role, _carrier(region), region.reading_order, indices, tokens)
        )
    missing = tuple(i for i in range(len(inventory)) if i not in unpromoted and i not in seen)
    duplicated = tuple(sorted(i for i, count in seen.items() if count > 1))
    carried_unpromoted = tuple(sorted(i for i in unpromoted if i in seen))
    if missing:
        violations.append(f"page_{page_number}:promoted_tokens_missing:{len(missing)}")
    if duplicated:
        violations.append(f"page_{page_number}:tokens_duplicated:{len(duplicated)}")
    if carried_unpromoted:
        violations.append(f"page_{page_number}:unpromoted_tokens_carried:{len(carried_unpromoted)}")
    items = [
        (
            is_pixel_region(region.role, region.table_cells, region.editable_fallback),
            (region.bbox.x, region.bbox.y, region.bbox.width, region.bbox.height),
            region.role != "table"
            and region.text is not None
            and "\n" not in region.text
            and any(
                ch.isdigit() for i in region.ocr_token_indices or () if i < len(inventory) for ch in inventory[i].text
            ),
        )
        for region in page.regions
    ]
    for pixel, text in boundary_conflicts(items):
        violations.append(f"{page.regions[pixel].region_id}:{page.regions[text].region_id}:crop_text_boundary")
    from services.source_content.raster_crop_edges import page_edge_conflicts
    from services.source_content.raster_exactly_once import page_exactly_once

    for pixel, other, _kind in page_exactly_once(page, analysis.source.canvas_width_emu):
        violations.append(f"{page.regions[pixel].region_id}:{page.regions[other].region_id}:shown_twice")

    # Editable text inside a crop, or a crop cutting its own words, loses or duplicates
    # content; crops cutting other pixels are measured by ``redesign_crop_edges``.
    for conflict in page_edge_conflicts(page, analysis.source.canvas_width_emu):
        if conflict.kind in ("text_word", "own_word"):
            violations.append(f"{page.regions[conflict.crop].region_id}:crop_edge_{conflict.kind}")
    return RedesignContentLedger(
        page_number,
        tuple(entries),
        tuple(sorted(unpromoted)),
        missing,
        duplicated,
        tuple(violations),
    )


PIXEL_ROLES = frozenset({"screenshot", "logo", "decoration"})
OVERLAP_SHARE = 0.05


def _area(box: Box) -> float:
    return max(0.0, box[2]) * max(0.0, box[3])


def _intersection(a: Box, b: Box) -> float:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, width) * max(0.0, height)


def _touching_row(a: Box, b: Box) -> bool:
    low = min(a[3], b[3])
    overlap = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    gap = max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2])
    return overlap >= 0.5 * low and gap < low


def boundary_conflicts(items: Sequence[tuple[bool, Box, bool]]) -> list[tuple[int, int]]:
    """``items`` are ``(is_pixels, box, single_line_with_digits)``; returns (pixel, text) pairs.

    The digit rule compares single text lines only (a numeral OCR split into a
    crop and a text line); multi-line blocks and tables are checked by overlap.
    """

    conflicts = []
    for p, (pixels, pixel_box, pixel_digits) in enumerate(items):
        if not pixels:
            continue
        for t, (is_pixels, text_box, text_digits) in enumerate(items):
            if is_pixels:
                continue
            overlap = _intersection(pixel_box, text_box)
            if overlap > OVERLAP_SHARE * min(_area(pixel_box), _area(text_box)) or (
                pixel_digits and text_digits and _touching_row(pixel_box, text_box)
            ):
                conflicts.append((p, t))
    return conflicts


def is_pixel_region(role: str, table_cells, editable_fallback: bool) -> bool:
    return role in PIXEL_ROLES or (role == "table" and table_cells is None) or editable_fallback


def _split_axis(items: list[tuple[int, Box]], axis: int) -> tuple[list[list[tuple[int, Box]]], float]:
    """Split along one axis at its widest empty gap(s); return groups and that gap."""

    ordered = sorted(items, key=lambda item: item[1][axis])
    runs: list[list[tuple[int, Box]]] = []
    gaps: list[float] = []
    end = None
    for item in ordered:
        start = item[1][axis]
        stop = start + item[1][axis + 2]
        # Tolerate slight overlaps from generous crop padding.
        if end is not None and start < end - 4:
            runs[-1].append(item)
            end = max(end, stop)
        else:
            if end is not None:
                gaps.append(start - end)
            runs.append([item])
            end = stop
    if not gaps:
        return runs, 0.0
    widest = max(gaps)
    groups = [runs[0]]
    for gap, run in zip(gaps, runs[1:], strict=False):
        if gap >= widest - 0.5:
            groups.append(list(run))
        else:
            groups[-1].extend(run)
    return groups, widest


def xy_cut_order(boxes: list[Box]) -> list[int]:
    """Recursive XY-cut, cutting at the widest gap first.

    A full-width band (title) forces horizontal cuts at the top; below it a
    card grid is read column by column because column gutters are wider
    than the gaps between a card's own lines.
    """

    def solve(items: list[tuple[int, Box]]) -> list[int]:
        if len(items) <= 1:
            return [index for index, _ in items]
        rows, row_gap = _split_axis(items, 1)
        columns, column_gap = _split_axis(items, 0)
        if len(rows) > 1 and (len(columns) <= 1 or row_gap >= column_gap):
            return [index for group in rows for index in solve(group)]
        if len(columns) > 1:
            return [index for group in columns for index in solve(group)]
        return [index for index, _ in sorted(items, key=lambda item: (item[1][1], item[1][0]))]

    return solve(list(enumerate(boxes)))


def redesign_ink_coverage(
    analysis: RasterAnalysis, page_number: int, page_image
) -> tuple[tuple[int, int, int, int], ...]:
    """Source-pixel boxes of visible ink no rendered region carries (empty = covered).

    ``page_image`` is the page's embedded source image. Decoration regions count
    as coverage only for icon-like ink (see ``raster_ink_coverage``).
    """

    from services.source_content.raster_ink_coverage import uncovered_ink

    page = analysis.pages[page_number - 1]
    sx = page_image.width / analysis.source.canvas_width_emu
    sy = page_image.height / analysis.source.canvas_height_emu

    def pixels(box) -> tuple[int, int, int, int]:
        return (int(box.x * sx), int(box.y * sy), max(1, int(box.width * sx)), max(1, int(box.height * sy)))

    rendered = [pixels(r.bbox) for r in page.regions if r.role != "decoration"]
    decorations = [pixels(r.bbox) for r in page.regions if r.role == "decoration"]
    words = [pixels(token.bbox) for token in page.ocr_inventory]
    return tuple(uncovered_ink(page_image, rendered, decorations, words))
