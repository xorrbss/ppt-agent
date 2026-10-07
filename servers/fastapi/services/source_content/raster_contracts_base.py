"""Core pydantic models for screenshot source reconstruction: source identity,
geometry, OCR readings, regions and pages, and the approval pin."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.sql.public_artifact import ArtifactRef
from models.document_canvas import DocumentCanvasMetadata
from models.svg_canvas import SvgCanvasInput


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
    format: Literal["A4", "SVG"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    width_units: int = 840
    height_units: int = 1188
    emu_per_unit: int | float = Field(default=9000, gt=0)
    document_canvas: DocumentCanvasMetadata | None = Field(default=None, exclude_if=lambda value: value is None)
    aspect_ratio: Literal["16:9", "4:3", "9:16", "9:13", "1:1", "21:9"] | None = Field(default=None, exclude_if=lambda value: value is None)
    svg_canvas: SvgCanvasInput | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def require_a4(self):
        if self.format == "SVG":
            from types import SimpleNamespace
            from services.svg_canvas import selected_svg_canvas

            if (self.aspect_ratio is None) == (self.svg_canvas is None):
                raise ValueError("raster_target_selectors_invalid")
            expected_canvas = selected_svg_canvas(SimpleNamespace(
                aspect_ratio=self.aspect_ratio, svg_canvas=self.svg_canvas, page_size=None))
            if (self.document_canvas != expected_canvas
                    or (self.width_units, self.height_units) != (expected_canvas.width_px, expected_canvas.height_px)
                    or self.orientation != expected_canvas.orientation
                    or self.emu_per_unit != expected_canvas.emu_per_unit):
                raise ValueError("raster_target_canvas_mismatch")
            return self
        expected = (840, 1188) if self.orientation == "portrait" else (1188, 840)
        if ((self.width_units, self.height_units) != expected or self.emu_per_unit != 9000
                or self.document_canvas is not None or self.aspect_ratio is not None or self.svg_canvas is not None):
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
    # Native table only: (row, col, crop region id) of each unverified cell shown
    # as its source-pixel crop over the table (raster_table_overlay); that cell's
    # text is "". Cross-region rules: raster_contracts_analysis.
    # Omitted from serialization while empty, so records (and digests) without
    # pixel cells are byte-identical to those sealed before the field existed.
    table_pixel_cells: tuple[tuple[int, int, str], ...] = Field(default=(), exclude_if=lambda cells: not cells)

    @model_validator(mode="after")
    def valid_crop(self):
        if self.table_cells is not None:
            widths = {len(row) for row in self.table_cells}
            if self.role != "table" or not self.table_cells or not self.table_cells[0] or len(widths) != 1:
                raise ValueError("raster_table_cells_invalid")
        if self.table_pixel_cells:
            cells = self.table_cells
            positions = [(row, col) for row, col, _ in self.table_pixel_cells]
            ids = [crop for _, _, crop in self.table_pixel_cells]
            if (
                cells is None
                or self.editable_fallback
                or len(set(positions)) != len(positions)
                or len(set(ids)) != len(ids)
                or any(not 0 <= r < len(cells) or not 0 <= c < len(cells[0]) or cells[r][c] for r, c in positions)
            ):
                raise ValueError("raster_table_pixel_cells_invalid")
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
