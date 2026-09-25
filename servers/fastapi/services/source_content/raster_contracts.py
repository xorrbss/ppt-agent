"""Independent, reviewable contracts for screenshot source reconstruction."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.sql.public_artifact import ArtifactRef


class RasterContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RasterSourceIdentity(RasterContract):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_artifact_ref: ArtifactRef | None = None
    page_count: int = Field(ge=1)
    canvas_width_emu: int = Field(gt=0)
    canvas_height_emu: int = Field(gt=0)


class RasterBox(RasterContract):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class RasterTargetCanvas(RasterContract):
    format: Literal["A4"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    width_units: int = 840
    height_units: int = 1188
    emu_per_unit: Literal[9000] = 9000

    @model_validator(mode="after")
    def require_a4(self):
        expected = (840, 1188) if self.orientation == "portrait" else (1188, 840)
        if (self.width_units, self.height_units) != expected:
            raise ValueError("raster_target_canvas_must_be_a4")
        return self


class RasterOCRText(RasterContract):
    text: str
    bbox: RasterBox
    confidence: float | None = Field(default=None, ge=0, le=1)
    engine: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_required: bool = True


class RasterRegion(RasterContract):
    region_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    # "source_reference" is only valid as RasterPage.reference_region: the
    # complete source image kept for audit/fallback, never rendered by default.
    role: Literal[
        "title", "body", "table", "screenshot", "logo", "decoration", "unknown",
        "full_page_preserve", "source_reference",
    ]
    bbox: RasterBox
    z_order: int = Field(ge=0)
    text: str | None = None
    ocr: RasterOCRText | None = None
    crop_artifact_ref: ArtifactRef | None = None
    source_occurrence_id: str | None = None
    source_media_part: str | None = None
    source_pixel_bbox: tuple[int, int, int, int] | None = None
    crop_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pixel_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    overlaps: tuple[str, ...] = ()
    occluded_by: tuple[str, ...] = ()
    review_required: bool = True
    # Redesign fields (additive; None on legacy records).
    confidence: float | None = Field(default=None, ge=0, le=1)
    reading_order: int | None = Field(default=None, ge=0)
    # Row-major cell text for role "table"; None keeps the table as an image crop.
    table_cells: tuple[tuple[str, ...], ...] | None = None
    review_reason: str | None = None
    # Indices into the page ocr_inventory carried by this region, in reading
    # order (text, cells row-major, or pixels of a preserved crop).
    ocr_token_indices: tuple[int, ...] | None = None
    # Low-confidence text rendered from its source-pixel crop; ``text`` or the
    # table suggestion then holds the OCR reading for optional later review.
    editable_fallback: bool = False
    # Card/section membership (regions of one card share it; reading order is
    # the order within the group) and, for body regions, the number of
    # leading text lines that form the card heading.
    group_id: str | None = None
    heading_line_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_crop(self):
        if self.table_cells is not None:
            widths = {len(row) for row in self.table_cells}
            if self.role != "table" or not self.table_cells or not self.table_cells[0] or len(widths) != 1:
                raise ValueError("raster_table_cells_invalid")
        if self.heading_line_count and (self.text is None or self.heading_line_count > len(self.text.split("\n"))):
            raise ValueError("raster_heading_line_count_invalid")
        if self.ocr_token_indices is not None and len(set(self.ocr_token_indices)) != len(self.ocr_token_indices):
            raise ValueError("raster_region_token_duplicate")
        if self.source_pixel_bbox is not None:
            x, y, width, height = self.source_pixel_bbox
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise ValueError("raster_pixel_bbox_invalid")
        if self.crop_artifact_ref is not None:
            if self.crop_artifact_ref.kind != "image-derivative":
                raise ValueError("raster_crop_kind_invalid")
            if not all((self.source_occurrence_id, self.source_pixel_bbox, self.crop_source_sha256, self.pixel_sha256)):
                raise ValueError("raster_crop_provenance_required")
        return self


class RasterPage(RasterContract):
    page_number: int = Field(ge=1)
    regions: tuple[RasterRegion, ...]
    ocr_inventory: tuple[RasterOCRText, ...] = ()
    render_mode: Literal["reconstruct", "full_page_preserve", "redesign"] = "reconstruct"
    approval_method: Literal["manual_review", "automatic_preserve", "automatic_redesign"] = "manual_review"
    full_page_region: RasterRegion | None = None
    full_page_preserve_acknowledgements: tuple[Literal["image_only", "legibility"], ...] = ()
    recommended_render_mode: Literal["reconstruct", "full_page_preserve", "redesign"] | None = None
    recommendation_reason: str | None = None
    # Redesign pages: the full source image for reference/fallback only.
    reference_region: RasterRegion | None = None
    # Inventory indices judged not to be content (rule/icon noise without
    # letters or digits); the content ledger excludes exactly these.
    unpromoted_ocr_indices: tuple[int, ...] = ()


class RasterAnalysis(RasterContract):
    contract_version: Literal["raster-reconstruction-analysis/v1"] = "raster-reconstruction-analysis/v1"
    analysis_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    revision: int = Field(default=1, ge=1)
    source: RasterSourceIdentity
    target_canvas: RasterTargetCanvas
    pages: tuple[RasterPage, ...]
    unresolved: tuple[str, ...] = ()
    digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")

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
                if region.page_number != page.page_number or region.role != "full_page_preserve" or region.text or region.ocr:
                    raise ValueError("raster_full_page_region_invalid")
                if region.bbox != RasterBox(x=0, y=0, width=self.source.canvas_width_emu, height=self.source.canvas_height_emu):
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
                if region.bbox.x + region.bbox.width > self.source.canvas_width_emu + 1 or region.bbox.y + region.bbox.height > self.source.canvas_height_emu + 1:
                    raise ValueError("raster_region_outside_source")
        return self


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
        if reference.bbox != RasterBox(x=0, y=0, width=source.canvas_width_emu, height=source.canvas_height_emu):
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


class RasterApprovalPin(RasterContract):
    contract_version: Literal["raster-reconstruction-approval/v1"] = "raster-reconstruction-approval/v1"
    operation_id: uuid.UUID
    analysis_id: uuid.UUID
    analysis_revision: int = Field(ge=1)
    analysis_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_region_ids: tuple[str, ...]
    full_page_preserve_pages: tuple[int, ...] = ()
    automatic_preserve_pages: tuple[int, ...] = ()
    automatic_redesign_pages: tuple[int, ...] = ()
    approval_digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")


def raster_digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def seal_analysis(analysis: RasterAnalysis | dict) -> RasterAnalysis:
    value = RasterAnalysis.model_validate(analysis.model_dump(mode="json") if isinstance(analysis, RasterAnalysis) else analysis)
    digest = raster_digest(value.model_dump(mode="json", exclude={"digest"}))
    return value.model_copy(update={"digest": digest})


_ADDITIVE_REGION_DEFAULTS = {
    "confidence": None, "reading_order": None, "table_cells": None, "review_reason": None,
    "ocr_token_indices": None, "editable_fallback": False, "group_id": None, "heading_line_count": 0,
}
_ADDITIVE_PAGE_DEFAULTS = {"reference_region": None, "unpromoted_ocr_indices": []}


def _strip_additive_defaults(payload: dict) -> dict | None:
    """Rebuild the pre-redesign serialization, or None if redesign data is present."""

    for page in payload["pages"]:
        for key, value in _ADDITIVE_PAGE_DEFAULTS.items():
            if page.get(key, value) != value:
                return None
            page.pop(key, None)
        for region in (*page["regions"], *((page["full_page_region"],) if page.get("full_page_region") else ())):
            for key, value in _ADDITIVE_REGION_DEFAULTS.items():
                if region.get(key, value) != value:
                    return None
                region.pop(key, None)
    return payload


def require_analysis_digest(analysis: RasterAnalysis) -> None:
    if analysis.digest and analysis.digest == seal_analysis(analysis).digest:
        return
    # Records sealed before the additive redesign fields existed.
    legacy = _strip_additive_defaults(analysis.model_dump(mode="json", exclude={"digest"}))
    if legacy is None:
        raise ValueError("raster_analysis_digest_mismatch")
    if analysis.digest and analysis.digest == raster_digest(legacy):
        return
    if any(page["approval_method"] != "manual_review" for page in legacy["pages"]):
        raise ValueError("raster_analysis_digest_mismatch")
    for page in legacy["pages"]:
        page.pop("approval_method")
    if analysis.digest and analysis.digest == raster_digest(legacy):
        return
    for page in legacy["pages"]:
        defaults = {"render_mode": "reconstruct", "full_page_region": None,
                    "full_page_preserve_acknowledgements": [], "recommended_render_mode": None,
                    "recommendation_reason": None}
        if any(page.get(key) != value for key, value in defaults.items()):
            raise ValueError("raster_analysis_digest_mismatch")
        for key in defaults:
            page.pop(key, None)
    if not analysis.digest or analysis.digest != raster_digest(legacy):
        raise ValueError("raster_analysis_digest_mismatch")


def seal_approval(pin: RasterApprovalPin) -> RasterApprovalPin:
    return pin.model_copy(update={"approval_digest": raster_digest(pin.model_dump(mode="json", exclude={"approval_digest"}))})


def approval_digest_matches(pin: RasterApprovalPin) -> bool:
    if pin.approval_digest and pin.approval_digest == seal_approval(pin).approval_digest:
        return True
    if pin.automatic_redesign_pages:
        return False
    # Pins sealed before automatic_redesign_pages existed.
    legacy = pin.model_dump(mode="json", exclude={"approval_digest", "automatic_redesign_pages"})
    if pin.approval_digest and pin.approval_digest == raster_digest(legacy):
        return True
    if pin.full_page_preserve_pages:
        return False
    oldest = pin.model_dump(mode="json", exclude={"approval_digest", "full_page_preserve_pages", "automatic_redesign_pages"})
    return bool(pin.approval_digest) and pin.approval_digest == raster_digest(oldest)
