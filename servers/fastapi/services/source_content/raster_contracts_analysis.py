"""The top-level raster reconstruction analysis: one sealed record of every
page's regions, cross-checked for id uniqueness, geometry and redesign
invariants."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_validator

from services.source_content.raster_contracts_base import (
    RasterBox,
    RasterContract,
    RasterPage,
    RasterSourceIdentity,
    RasterTargetCanvas,
)
from services.source_content.raster_contracts_timing import RasterStageTimings


class RasterAnalysis(RasterContract):
    contract_version: Literal["raster-reconstruction-analysis/v1"] = "raster-reconstruction-analysis/v1"
    analysis_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    revision: int = Field(default=1, ge=1)
    source: RasterSourceIdentity
    target_canvas: RasterTargetCanvas
    pages: tuple[RasterPage, ...]
    unresolved: tuple[str, ...] = ()
    digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    # Timing receipt of the analysis run (additive; excluded from ``digest``).
    stage_timings: RasterStageTimings | None = None

    @model_validator(mode="after")
    def require_complete_pages(self):
        if tuple(p.page_number for p in self.pages) != tuple(range(1, self.source.page_count + 1)):
            raise ValueError("raster_source_page_order_mismatch")
        ids = [
            r.region_id for p in self.pages
            for r in (*p.regions, *(x for x in (p.full_page_region, p.reference_region) if x is not None))
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("raster_duplicate_region_id")
        for page in self.pages:
            if page.approval_method == "automatic_preserve" and page.render_mode != "full_page_preserve":
                raise ValueError("raster_automatic_preserve_mode_invalid")
            if page.render_mode == "full_page_preserve" and page.full_page_region is None:
                raise ValueError("raster_full_page_region_required")
            if page.full_page_region is not None:
                region = page.full_page_region
                if (
                    region.page_number != page.page_number
                    or region.role != "full_page_preserve"
                    or region.text
                    or region.ocr
                ):
                    raise ValueError("raster_full_page_region_invalid")
                if region.bbox != _full_canvas(self.source):
                    raise ValueError("raster_full_page_geometry_invalid")
            if any(region.role == "full_page_preserve" for region in page.regions):
                raise ValueError("raster_full_page_role_requires_page_mode")
            _validate_redesign_page(page, self.source)
            page_ids = {r.region_id for r in page.regions}
            for region in page.regions:
                if region.page_number != page.page_number:
                    raise ValueError("raster_region_page_mismatch")
                if any(i not in page_ids or i == region.region_id for i in (*region.overlaps, *region.occluded_by)):
                    raise ValueError("raster_overlap_reference_invalid")
                box, source = region.bbox, self.source
                if box.x + box.width > source.canvas_width_emu + 1 or box.y + box.height > source.canvas_height_emu + 1:
                    raise ValueError("raster_region_outside_source")
        return self


def _full_canvas(source: RasterSourceIdentity) -> RasterBox:
    return RasterBox(x=0, y=0, width=source.canvas_width_emu, height=source.canvas_height_emu)


def _validate_redesign_page(page: RasterPage, source: RasterSourceIdentity) -> None:
    if page.approval_method == "automatic_redesign" and page.render_mode != "redesign":
        raise ValueError("raster_automatic_redesign_mode_invalid")
    if any(region.role == "source_reference" for region in page.regions):
        raise ValueError("raster_source_reference_role_requires_page_field")
    if page.render_mode == "redesign" and (page.reference_region is None or page.full_page_region is not None):
        raise ValueError("raster_redesign_reference_required")
    reference = page.reference_region
    if reference is not None:
        carries_text = bool(reference.text or reference.ocr or reference.ocr_token_indices)
        if reference.page_number != page.page_number or reference.role != "source_reference" or carries_text:
            raise ValueError("raster_reference_region_invalid")
        if reference.bbox != _full_canvas(source):
            raise ValueError("raster_reference_geometry_invalid")
    orders = [region.reading_order for region in page.regions if region.reading_order is not None]
    if len(orders) != len(set(orders)):
        raise ValueError("raster_reading_order_duplicate")
    carried = [index for region in page.regions for index in (region.ocr_token_indices or ())]
    size = len(page.ocr_inventory)
    if len(carried) != len(set(carried)) or any(index >= size for index in (*carried, *page.unpromoted_ocr_indices)):
        raise ValueError("raster_region_token_assignment_invalid")
    if set(carried) & set(page.unpromoted_ocr_indices):
        raise ValueError("raster_unpromoted_token_carried")
    _validate_pixel_cells(page)


def _within(inner, outer, tolerance: float) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[0] + inner[2] <= outer[0] + outer[2] + tolerance
        and inner[1] + inner[3] <= outer[1] + outer[3] + tolerance
    )


def _validate_pixel_cells(page: RasterPage) -> None:
    """A table's pixel cell is a screenshot crop of source pixels inside that table,
    linked by exactly one native table (text "" there, see RasterRegion)."""

    by_id = {region.region_id: region for region in page.regions}
    linked: set[str] = set()
    for table in page.regions:
        for _, _, crop_id in table.table_pixel_cells:
            crop = by_id.get(crop_id)
            if crop is None or crop_id in linked or crop.role != "screenshot" or not crop.editable_fallback:
                raise ValueError("raster_table_pixel_cell_invalid")
            linked.add(crop_id)
            box, outer = crop.bbox, table.bbox
            if not _within((box.x, box.y, box.width, box.height), (outer.x, outer.y, outer.width, outer.height), 1):
                raise ValueError("raster_table_pixel_cell_invalid")
            if crop.source_pixel_bbox is not None and table.source_pixel_bbox is not None:
                if not _within(crop.source_pixel_bbox, table.source_pixel_bbox, 0):
                    raise ValueError("raster_table_pixel_cell_invalid")
