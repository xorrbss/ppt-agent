"""Canonical, versioned scene graph for authored-vNext decks.

The scene is the editable product document.  ``SlideSpecV2`` remains readable as
a compatibility projection, but preview, Workbench, persistence, publication,
and native export identify the same scene and stable element IDs.
"""

from __future__ import annotations

import hashlib
import base64
import binascii
import json
import math
import re
import unicodedata
from typing import Annotated, Any, Literal, Union

from pydantic import Field, StringConstraints, model_validator

from ..master.base import DeckId, Digest, SlideId, StrictCamelModel
from ..master.contract import Rect


# A Scene owner ID is the authored QID.  Older generated decks used the
# ``element:...`` subset; the wider pattern is required so declared HTML QIDs
# can survive semantic source -> Scene -> Workbench -> OOXML without remapping.
ElementId = Annotated[
    str, Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
]
AssetId = Annotated[str, Field(pattern=r"^asset:[a-z0-9][a-z0-9._-]*$")]
DataId = Annotated[str, Field(pattern=r"^data:[a-z0-9][a-z0-9._-]*$")]


# Versioned admission IDs are intentionally closed.  Adding serializer code is
# not enough to widen this set: the semantic admission and readiness projection
# must name the same capability version first.
SCENE_OWNER_CAPABILITY_KINDS: dict[str, frozenset[str]] = {
    "editable.text.ko-en.v1": frozenset({"text"}),
    "editable.text.ja-zh.v1": frozenset({"text", "shape"}),
    "editable.text.advanced.v1": frozenset({"text", "shape"}),
    "editable.shape.solid-basic.v1": frozenset({"shape"}),
    "editable.shape.advanced.v1": frozenset({"shape", "group", "connector"}),
    "editable.image.rect-crop.v1": frozenset({"image"}),
    "editable.image.advanced-crop-mask.v1": frozenset({"image", "shape"}),
    "editable.table.basic.v1": frozenset({"table"}),
    "editable.table.advanced.v1": frozenset({"table", "shape"}),
    "editable.chart.line-pie.v1": frozenset({"chart", "shape"}),
    # Read compatibility for the pre-versioned chart raster fixture.
    "editable.chart.raster.v1": frozenset({"chart", "shape"}),
    "editable.raster-island-integrity.v1": frozenset(
        {"text", "image", "shape", "connector", "table", "chart", "group"}
    ),
    "css.gradient.v1": frozenset({"shape"}),
    "css.shadow.v1": frozenset({"shape"}),
    "css.clip-path.v1": frozenset({"shape"}),
    "css.filter-blend.v1": frozenset({"shape"}),
    "css.grid-flex.v1": frozenset({"shape"}),
    "css.background-plane.v1": frozenset({"shape"}),
    "css.paint-closure.v1": frozenset({"shape"}),
}


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("scene canonical JSON does not permit non-finite numbers")
        if value.is_integer():
            return int(value)
        return float(format(value, ".15g"))
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_value(item)
            for key, item in value.items()
        }
    return value


def scene_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class SceneEditability(StrictCamelModel):
    """Independent edit permissions consumed by Workbench and mutation CAS."""

    text: bool = False
    geometry: bool = False
    style: bool = False
    asset: bool = False
    data: bool = False


class SceneTextStyle(StrictCamelModel):
    font_family: str | None = Field(default=None, min_length=1, max_length=200)
    font_size: float | None = Field(default=None, ge=1, le=400)
    font_weight: int | None = Field(default=None, ge=100, le=900)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_align: Literal["left", "center", "right", "justify"] | None = None
    vertical_align: Literal["top", "middle", "bottom"] | None = None
    auto_fit: Literal["none", "shrink"] = "none"
    # Scene font and text-frame inset values use PowerPoint points. Browser CSS
    # pixels are converted at the semantic boundary before these fields are
    # sealed, so the OOXML exporter must not apply a second 0.75 factor.
    line_height: float | None = Field(
        default=None, ge=0.5, le=5, exclude_if=lambda value: value is None
    )
    margin_left_pt: float | None = Field(
        default=None, ge=0, le=1000, exclude_if=lambda value: value is None
    )
    margin_right_pt: float | None = Field(
        default=None, ge=0, le=1000, exclude_if=lambda value: value is None
    )
    margin_top_pt: float | None = Field(
        default=None, ge=0, le=1000, exclude_if=lambda value: value is None
    )
    margin_bottom_pt: float | None = Field(
        default=None, ge=0, le=1000, exclude_if=lambda value: value is None
    )


class SceneTextRun(StrictCamelModel):
    """Stable UTF-16 run identity, including intentionally empty runs."""

    run_id: str = Field(pattern=r"^run:[A-Za-z0-9][A-Za-z0-9._:-]{0,123}$")
    # Source-content restyling seals verbatim run boundaries. The shared strict
    # model trims strings by default, which would silently delete significant
    # leading/trailing spaces inside OOXML runs.
    text: Annotated[str, StringConstraints(strip_whitespace=False, max_length=50000)]
    font_family: str | None = Field(default=None, min_length=1, max_length=200)
    font_size: float | None = Field(default=None, ge=1, le=400)
    font_weight: int | None = Field(default=None, ge=100, le=900)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    italic: bool | None = None
    underline: bool | None = None


class SceneImageCropGeometry(StrictCamelModel):
    """PowerPoint-compatible fractional crop insets from the source image."""

    left: float = Field(default=0, ge=0, lt=1)
    top: float = Field(default=0, ge=0, lt=1)
    right: float = Field(default=0, ge=0, lt=1)
    bottom: float = Field(default=0, ge=0, lt=1)

    @model_validator(mode="after")
    def _leaves_visible_pixels(self) -> "SceneImageCropGeometry":
        if self.left + self.right >= 1 or self.top + self.bottom >= 1:
            raise ValueError("image crop geometry must leave a visible source region")
        return self


class _SceneElementBase(StrictCamelModel):
    element_id: ElementId
    z_order: int = Field(ge=-100000, le=100000)
    bounds: Rect
    rotation: float = Field(default=0, ge=-360, le=360)
    semantic_role: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z][A-Za-z0-9._-]*$",
    )
    editability: SceneEditability


class SceneTextElement(_SceneElementBase):
    kind: Literal["text"]
    text: Annotated[
        str,
        StringConstraints(strip_whitespace=False, min_length=1, max_length=50000),
    ]
    style: SceneTextStyle | None = None
    runs: list[SceneTextRun] | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _text_capability_matches_kind(self) -> "SceneTextElement":
        if self.editability.asset or self.editability.data:
            raise ValueError("text elements cannot expose asset or data editing")
        if self.runs is not None:
            run_ids = [run.run_id for run in self.runs]
            if len(run_ids) != len(set(run_ids)):
                raise ValueError("text run IDs must be unique")
            if "".join(run.text for run in self.runs) != self.text:
                raise ValueError("text runs must concatenate to the text value")
        return self


class SceneImageElement(_SceneElementBase):
    kind: Literal["image"]
    asset_id: AssetId
    asset_digest: Digest
    alt_text: str = Field(min_length=1, max_length=1000)
    crop: Literal["contain", "cover"] = "cover"
    crop_geometry: SceneImageCropGeometry | None = None

    @model_validator(mode="after")
    def _image_capability_matches_kind(self) -> "SceneImageElement":
        if self.editability.text or self.editability.data:
            raise ValueError("image elements cannot expose text or data editing")
        return self


class SceneShapeElement(_SceneElementBase):
    kind: Literal["shape"]
    shape: Literal[
        "rect", "roundRect", "ellipse", "triangle", "diamond", "chevron", "line"
    ]
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_width: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _shape_capability_matches_kind(self) -> "SceneShapeElement":
        if self.editability.text or self.editability.asset or self.editability.data:
            raise ValueError("shape elements expose geometry/style editing only")
        if self.semantic_role == "canvasBackground" and (
            self.shape != "rect"
            or self.fill is None
            or self.stroke is not None
            or self.stroke_width is not None
            or self.z_order != -100_000
            or self.editability.geometry
            or self.editability.style
        ):
            raise ValueError(
                "canvasBackground must be a filled, non-editable lowest-z rectangle"
            )
        return self


class SceneConnectorElement(_SceneElementBase):
    kind: Literal["connector"]
    from_element_id: ElementId
    to_element_id: ElementId
    connector_type: Literal["straight", "elbow", "curve"] = "straight"
    start_arrow: Literal["none", "triangle", "stealth", "diamond", "oval"] = "none"
    end_arrow: Literal["none", "triangle", "stealth", "diamond", "oval"] = "none"

    @model_validator(mode="after")
    def _connector_capability_matches_kind(self) -> "SceneConnectorElement":
        if self.from_element_id == self.to_element_id:
            raise ValueError("connector endpoints must differ")
        if self.editability.text or self.editability.asset or self.editability.data:
            raise ValueError("connector elements expose geometry editing only")
        return self


class SceneTableElement(_SceneElementBase):
    kind: Literal["table"]
    data_id: DataId
    data_digest: Digest
    rows: int = Field(ge=1, le=100)
    columns: int = Field(ge=1, le=30)

    @model_validator(mode="after")
    def _table_capability_matches_kind(self) -> "SceneTableElement":
        if self.editability.text or self.editability.asset:
            raise ValueError("table elements expose geometry and data editing only")
        return self


class SceneChartElement(_SceneElementBase):
    kind: Literal["chart"]
    data_id: DataId
    data_digest: Digest
    chart_type: Literal["bar", "column", "line", "area", "pie", "donut"]
    accessibility_summary: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _chart_capability_matches_kind(self) -> "SceneChartElement":
        if self.editability.text or self.editability.asset:
            raise ValueError("chart elements expose geometry and data editing only")
        return self


class SceneGroupElement(_SceneElementBase):
    kind: Literal["group"]
    child_element_ids: list[ElementId] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _group_is_unique_and_geometry_only(self) -> "SceneGroupElement":
        if len(set(self.child_element_ids)) != len(self.child_element_ids):
            raise ValueError("group childElementIds must be unique")
        if self.element_id in self.child_element_ids:
            raise ValueError("group cannot contain itself")
        if self.editability.text or self.editability.asset or self.editability.data:
            raise ValueError("group elements expose geometry editing only")
        return self


SceneElement = Union[
    SceneTextElement,
    SceneImageElement,
    SceneShapeElement,
    SceneConnectorElement,
    SceneTableElement,
    SceneChartElement,
    SceneGroupElement,
]


class SceneAssetManifestEntry(StrictCamelModel):
    asset_id: AssetId
    asset_digest: Digest
    media_type: Literal[
        "image/png", "image/jpeg", "image/svg+xml", "image/webp"
    ]
    storage_reference: str = Field(min_length=1, max_length=133_334_000)
    size_bytes: int = Field(gt=0, le=100_000_000)

    @model_validator(mode="after")
    def _storage_reference_is_digest_sealed(self) -> "SceneAssetManifestEntry":
        data_prefix = f"data:{self.media_type};base64,"
        asset_store_reference = f"asset-store:{self.asset_id}"
        if self.storage_reference.startswith("data:"):
            if not self.storage_reference.startswith(data_prefix):
                raise ValueError("scene asset data URI media type mismatch")
            try:
                content = base64.b64decode(
                    self.storage_reference.removeprefix(data_prefix), validate=True
                )
            except (ValueError, binascii.Error) as error:
                raise ValueError("scene asset data URI bytes are invalid") from error
            if len(content) != self.size_bytes:
                raise ValueError("scene asset data URI size mismatch")
            if f"sha256:{hashlib.sha256(content).hexdigest()}" != self.asset_digest:
                raise ValueError("scene asset data URI digest mismatch")
        elif self.storage_reference != asset_store_reference:
            raise ValueError(
                "scene asset storageReference must be a sealed data URI or asset-store reference"
            )
        return self


class SceneDataManifestEntry(StrictCamelModel):
    data_id: DataId
    data_digest: Digest
    kind: Literal["table", "chart"]
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_digest_matches(self) -> "SceneDataManifestEntry":
        if self.kind == "table":
            if not set(self.payload).issubset(
                {
                    "cells",
                    "cellStyles",
                    "columnWidthsPt",
                    "rowHeightsPt",
                    "mergedCells",
                }
            ) or "cells" not in self.payload:
                raise ValueError(
                    "scene table data payload fields are invalid"
                )
            cells = self.payload.get("cells")
            if (
                not isinstance(cells, list)
                or not cells
                or len(cells) > 100
                or not all(isinstance(row, list) and row for row in cells)
            ):
                raise ValueError("scene table data cells must be a non-empty matrix")
            column_count = len(cells[0])
            if column_count > 30 or any(len(row) != column_count for row in cells):
                raise ValueError("scene table data cells must be rectangular")
            if any(
                not isinstance(cell, str) or len(cell) > 5000
                for row in cells
                for cell in row
            ):
                raise ValueError("scene table data cells must be bounded strings")
            widths = self.payload.get("columnWidthsPt")
            if widths is not None and (
                not isinstance(widths, list)
                or len(widths) != column_count
                or any(
                    isinstance(width, bool)
                    or not isinstance(width, (int, float))
                    or not math.isfinite(float(width))
                    or not 0 < float(width) <= 10_000
                    for width in widths
                )
            ):
                raise ValueError("scene table columnWidthsPt are invalid")
            styles = self.payload.get("cellStyles")
            if styles is not None:
                if (
                    not isinstance(styles, list)
                    or len(styles) != len(cells)
                    or any(
                        not isinstance(row, list) or len(row) != column_count
                        for row in styles
                    )
                ):
                    raise ValueError("scene table cellStyles must match cells")
                for row in styles:
                    for style in row:
                        SceneTableCellStyle.model_validate(style)
            row_heights = self.payload.get("rowHeightsPt")
            if row_heights is not None and (
                not isinstance(row_heights, list)
                or len(row_heights) != len(cells)
                or any(
                    isinstance(height, bool)
                    or not isinstance(height, (int, float))
                    or not math.isfinite(float(height))
                    or not 0 < float(height) <= 1000
                    for height in row_heights
                )
            ):
                raise ValueError("scene table rowHeightsPt are invalid")
            merged = self.payload.get("mergedCells")
            if merged is not None:
                if not isinstance(merged, list):
                    raise ValueError("scene table mergedCells are invalid")
                occupied: set[tuple[int, int]] = set()
                for raw_merge in merged:
                    if not isinstance(raw_merge, dict) or set(raw_merge) != {
                        "row",
                        "column",
                        "rowSpan",
                        "columnSpan",
                    }:
                        raise ValueError("scene table mergedCells are invalid")
                    if any(
                        isinstance(raw_merge[key], bool)
                        or not isinstance(raw_merge[key], int)
                        for key in raw_merge
                    ):
                        raise ValueError("scene table mergedCells are invalid")
                    row = raw_merge["row"]
                    column = raw_merge["column"]
                    row_span = raw_merge["rowSpan"]
                    column_span = raw_merge["columnSpan"]
                    if (
                        row < 0
                        or column < 0
                        or row_span < 1
                        or column_span < 1
                        or row_span * column_span < 2
                        or row + row_span > len(cells)
                        or column + column_span > column_count
                    ):
                        raise ValueError("scene table mergedCells are invalid")
                    covered = {
                        (row_index, column_index)
                        for row_index in range(row, row + row_span)
                        for column_index in range(column, column + column_span)
                    }
                    if occupied.intersection(covered):
                        raise ValueError("scene table mergedCells overlap")
                    occupied.update(covered)
        else:
            if not {"categories", "series"}.issubset(self.payload) or not set(
                self.payload
            ).issubset({"categories", "series", "style"}):
                raise ValueError(
                    "scene chart data payload fields are invalid"
                )
            categories = self.payload.get("categories")
            series = self.payload.get("series")
            if (
                not isinstance(categories, list)
                or not categories
                or len(categories) > 100
                or any(not isinstance(item, str) or not item or len(item) > 500 for item in categories)
                or not isinstance(series, list)
                or not series
                or len(series) > 30
            ):
                raise ValueError("scene chart data categories or series are invalid")
            names: list[str] = []
            for item in series:
                if not isinstance(item, dict) or set(item) != {"name", "values"}:
                    raise ValueError("scene chart series must contain only name and values")
                name = item.get("name")
                values = item.get("values")
                if (
                    not isinstance(name, str)
                    or not name
                    or len(name) > 500
                    or not isinstance(values, list)
                    or len(values) != len(categories)
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        for value in values
                    )
                ):
                    raise ValueError("scene chart series values are invalid")
                names.append(name)
            if len(set(names)) != len(names):
                raise ValueError("scene chart series names must be unique")
            style = self.payload.get("style")
            if style is not None:
                if not isinstance(style, dict) or not set(style).issubset(
                    {"chartStyle", "seriesColors", "legend"}
                ):
                    raise ValueError("scene chart style is invalid")
                chart_style = style.get("chartStyle", 2)
                legend = style.get("legend")
                colors = style.get("seriesColors")
                if (
                    isinstance(chart_style, bool)
                    or not isinstance(chart_style, int)
                    or not 1 <= chart_style <= 48
                    or legend is not None
                    and not isinstance(legend, bool)
                    or colors is not None
                    and (
                        not isinstance(colors, list)
                        or len(colors) != len(series)
                        or any(
                            not isinstance(color, str)
                            or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
                            for color in colors
                        )
                    )
                ):
                    raise ValueError("scene chart style is invalid")
        if scene_digest(self.payload) != self.data_digest:
            raise ValueError("scene data manifest digest mismatch")
        return self


class SceneCapability(StrictCamelModel):
    kind: Literal["text", "image", "shape", "connector", "table", "chart", "group"]
    preview: Literal["supported", "partial", "readOnly", "unsupported"]
    workbench_edit: Literal["supported", "partial", "readOnly", "unsupported"]
    native_pptx_export: Literal["supported", "partial", "readOnly", "unsupported"]
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _unsupported_has_reason(self) -> "SceneCapability":
        states = (self.preview, self.workbench_edit, self.native_pptx_export)
        if "unsupported" in states and self.reason is None:
            raise ValueError("unsupported scene capability requires a reason")
        if self.kind in {"connector", "group"} and self.native_pptx_export != "unsupported":
            raise ValueError(
                f"{self.kind} native PPTX export must remain explicitly unsupported"
            )
        return self


class SceneTableBorderStyle(StrictCamelModel):
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    width_pt: float = Field(ge=0, le=100)


class SceneTableCellBorders(StrictCamelModel):
    top: SceneTableBorderStyle | None = None
    right: SceneTableBorderStyle | None = None
    bottom: SceneTableBorderStyle | None = None
    left: SceneTableBorderStyle | None = None


class SceneTableCellStyle(StrictCamelModel):
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    borders: SceneTableCellBorders | None = None
    horizontal_align: Literal["left", "center", "right", "justify"] | None = None
    vertical_align: Literal["top", "middle", "bottom"] | None = None


class SceneOwnerDecision(StrictCamelModel):
    owner_id: ElementId
    decision: Literal["nativeEditable", "rasterIsland", "publicationBlocked"]
    capability_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^(?:editable|css)\.[a-z0-9][a-z0-9._-]*\.v[0-9]+$",
    )
    reason_code: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )

    @model_validator(mode="after")
    def _non_native_decision_has_reason(self) -> "SceneOwnerDecision":
        if self.capability_id not in SCENE_OWNER_CAPABILITY_KINDS:
            raise ValueError("owner decision uses an unknown capability version")
        if self.decision != "nativeEditable" and self.reason_code is None:
            raise ValueError("non-native owner decision requires reasonCode")
        if self.decision == "nativeEditable" and self.reason_code is not None:
            raise ValueError("native owner decision cannot carry a fallback reason")
        return self


class SceneRasterZBand(StrictCamelModel):
    min_z: int = Field(ge=-100000, le=100000)
    max_z: int = Field(ge=-100000, le=100000)

    @model_validator(mode="after")
    def _ordered(self) -> "SceneRasterZBand":
        if self.min_z > self.max_z:
            raise ValueError("raster island zBand must be ordered")
        return self


class SceneRasterIsland(StrictCamelModel):
    island_id: str = Field(pattern=r"^island:[a-z0-9][a-z0-9._-]*$")
    source_owner_ids: list[ElementId] = Field(min_length=1, max_length=500)
    asset_id: AssetId
    asset_digest: Digest
    bounds: Rect
    z_band: SceneRasterZBand
    raster_plane_digest: Digest

    @model_validator(mode="after")
    def _owners_are_unique(self) -> "SceneRasterIsland":
        if len(set(self.source_owner_ids)) != len(self.source_owner_ids):
            raise ValueError("raster island sourceOwnerIds must be unique")
        return self


class SlideHybridExportPlan(StrictCamelModel):
    schema_version: Literal["slide-hybrid-export-plan/v1"] = (
        "slide-hybrid-export-plan/v1"
    )
    owner_decisions: list[SceneOwnerDecision] = Field(min_length=1, max_length=500)
    raster_islands: list[SceneRasterIsland] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _owner_and_island_sets_are_closed(self) -> "SlideHybridExportPlan":
        owner_ids = [item.owner_id for item in self.owner_decisions]
        if len(set(owner_ids)) != len(owner_ids):
            raise ValueError("hybrid ownerDecisions must contain each owner once")
        island_ids = [item.island_id for item in self.raster_islands]
        if len(set(island_ids)) != len(island_ids):
            raise ValueError("hybrid raster island IDs must be unique")
        island_owners = [
            owner_id
            for island in self.raster_islands
            for owner_id in island.source_owner_ids
        ]
        if len(set(island_owners)) != len(island_owners):
            raise ValueError("hybrid raster island owner sets must be disjoint")
        raster_decisions = {
            item.owner_id
            for item in self.owner_decisions
            if item.decision == "rasterIsland"
        }
        if set(island_owners) != raster_decisions:
            raise ValueError(
                "rasterIsland decisions must map to exactly one raster island"
            )
        return self


class SlideSceneIR(StrictCamelModel):
    schema_version: Literal["slide-scene-ir/v1"] = "slide-scene-ir/v1"
    slide_id: SlideId
    revision: int = Field(ge=1)
    slide_digest: Digest
    elements: list[SceneElement] = Field(min_length=1, max_length=500)
    hybrid_export: SlideHybridExportPlan | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _identity_order_and_digest_are_canonical(self) -> "SlideSceneIR":
        ids = [element.element_id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("scene element IDs must be unique per slide")
        if list(self.elements) != sorted(
            self.elements, key=lambda element: (element.z_order, element.element_id)
        ):
            raise ValueError("scene elements must be ordered by zOrder and elementId")
        id_set = set(ids)
        if self.hybrid_export is not None:
            decisions = {
                item.owner_id: item for item in self.hybrid_export.owner_decisions
            }
            if set(decisions) != id_set:
                raise ValueError(
                    "hybrid ownerDecisions must exactly match slide element IDs"
                )
            for element in self.elements:
                decision = decisions[element.element_id]
                if element.kind not in SCENE_OWNER_CAPABILITY_KINDS[
                    decision.capability_id
                ]:
                    raise ValueError(
                        "hybrid owner capability does not match the Scene element kind"
                    )
        backgrounds = [
            element
            for element in self.elements
            if element.semantic_role == "canvasBackground"
        ]
        if len(backgrounds) > 1:
            raise ValueError("slide scene allows at most one canvasBackground")
        if backgrounds and any(
            element.z_order <= backgrounds[0].z_order
            for element in self.elements
            if element.element_id != backgrounds[0].element_id
        ):
            raise ValueError("canvasBackground must have the lowest zOrder")
        for element in self.elements:
            if isinstance(element, SceneConnectorElement) and (
                element.from_element_id not in id_set
                or element.to_element_id not in id_set
            ):
                raise ValueError("connector endpoint does not exist on the slide")
            if isinstance(element, SceneGroupElement) and any(
                child not in id_set for child in element.child_element_ids
            ):
                raise ValueError("group child does not exist on the slide")
        payload = self.model_dump(
            mode="json", by_alias=True, exclude={"slide_digest"}
        )
        if self.slide_digest == scene_digest(payload):
            return self
        legacy_payload = dict(payload)
        if self.hybrid_export is None and "hybrid_export" in self.model_fields_set:
            # An intermediate schema sealed an explicit nullable field. Accept
            # only payloads that actually carried that null; new sealing omits
            # it and non-null Hybrid plans remain part of the strict digest.
            legacy_payload["hybridExport"] = None
        if self.slide_digest != scene_digest(legacy_payload):
            raise ValueError("slide scene digest mismatch")
        return self


class DeckSceneIR(StrictCamelModel):
    schema_version: Literal["deck-scene-ir/v1"] = "deck-scene-ir/v1"
    deck_id: DeckId
    revision: int = Field(ge=1)
    composition_digest: Digest
    source_digest: Digest
    scene_digest: Digest
    hybrid_profile_id: Literal[
        "authored-editable-hybrid-ko-en-basic-v1"
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    slides: list[SlideSceneIR] = Field(min_length=1, max_length=500)
    asset_manifest: list[SceneAssetManifestEntry] = Field(
        default_factory=list, max_length=5000
    )
    data_manifest: list[SceneDataManifestEntry] = Field(
        default_factory=list, max_length=5000
    )
    capabilities: list[SceneCapability] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def _identity_manifests_capabilities_and_digest_are_canonical(self) -> "DeckSceneIR":
        slide_ids = [slide.slide_id for slide in self.slides]
        if len(slide_ids) != len(set(slide_ids)):
            raise ValueError("scene slide IDs must be unique")
        assets = {entry.asset_id: entry for entry in self.asset_manifest}
        if len(assets) != len(self.asset_manifest):
            raise ValueError("scene asset IDs must be unique")
        data = {entry.data_id: entry for entry in self.data_manifest}
        if len(data) != len(self.data_manifest):
            raise ValueError("scene data IDs must be unique")
        capability_kinds = [item.kind for item in self.capabilities]
        expected_kinds = {"text", "image", "shape", "connector", "table", "chart", "group"}
        if set(capability_kinds) != expected_kinds or len(capability_kinds) != 7:
            raise ValueError("scene capabilities must declare every element kind once")
        has_hybrid_plan = [slide.hybrid_export is not None for slide in self.slides]
        if self.hybrid_profile_id is None and any(has_hybrid_plan):
            raise ValueError("hybridExport requires hybridProfileId")
        if self.hybrid_profile_id is not None and not all(has_hybrid_plan):
            raise ValueError("hybrid profile requires a plan on every slide")
        for slide in self.slides:
            for element in slide.elements:
                if isinstance(element, SceneImageElement):
                    entry = assets.get(element.asset_id)
                    if entry is None or entry.asset_digest != element.asset_digest:
                        raise ValueError("image element is not digest-pinned in assetManifest")
                if isinstance(element, (SceneTableElement, SceneChartElement)):
                    entry = data.get(element.data_id)
                    if (
                        entry is None
                        or entry.data_digest != element.data_digest
                        or entry.kind != element.kind
                    ):
                        raise ValueError("data element is not digest-pinned in dataManifest")
                    if isinstance(element, SceneTableElement):
                        cells = entry.payload["cells"]
                        if element.rows != len(cells) or element.columns != len(cells[0]):
                            raise ValueError(
                                "table element dimensions do not match dataManifest"
                            )
                    elif element.chart_type in {"pie", "donut"} and len(
                        entry.payload["series"]
                    ) != 1:
                        raise ValueError(
                            "pie and donut chart elements require exactly one series"
                        )
            if slide.hybrid_export is not None:
                for island in slide.hybrid_export.raster_islands:
                    entry = assets.get(island.asset_id)
                    if entry is None or entry.asset_digest != island.asset_digest:
                        raise ValueError(
                            "raster island is not digest-pinned in assetManifest"
                        )
        payload = self.model_dump(
            mode="json", by_alias=True, exclude={"scene_digest"}
        )
        if self.scene_digest == scene_digest(payload):
            return self
        legacy_payload = dict(payload)
        legacy_slides = [dict(item) for item in payload["slides"]]
        for slide, slide_payload in zip(
            self.slides, legacy_slides, strict=True
        ):
            if (
                slide.hybrid_export is None
                and "hybrid_export" in slide.model_fields_set
            ):
                slide_payload["hybridExport"] = None
        legacy_payload["slides"] = legacy_slides
        if (
            self.hybrid_profile_id is None
            and "hybrid_profile_id" in self.model_fields_set
        ):
            legacy_payload["hybridProfileId"] = None
        if self.scene_digest != scene_digest(legacy_payload):
            raise ValueError("deck scene digest mismatch")
        return self

    def require_capability(self, *, consumer: Literal["preview", "workbenchEdit", "nativePptxExport"]) -> None:
        """Reject present element kinds that the selected consumer cannot handle."""

        present = {element.kind for slide in self.slides for element in slide.elements}
        if consumer == "nativePptxExport" and any(
            decision.decision == "publicationBlocked"
            for slide in self.slides
            if slide.hybrid_export is not None
            for decision in slide.hybrid_export.owner_decisions
        ):
            raise ValueError("scene has publicationBlocked hybrid owners")
        if consumer == "nativePptxExport" and self.hybrid_profile_id is not None:
            # Raster owners are exported only through their single raster
            # island.  Their placeholder Scene kind must not be mistaken for a
            # native serializer requirement.
            present = {
                element.kind
                for slide in self.slides
                for element in slide.elements
                if slide.hybrid_export is not None
                and next(
                    decision
                    for decision in slide.hybrid_export.owner_decisions
                    if decision.owner_id == element.element_id
                ).decision
                == "nativeEditable"
            }
        by_kind = {capability.kind: capability for capability in self.capabilities}
        unsupported = []
        for kind in sorted(present):
            capability = by_kind[kind]
            state = {
                "preview": capability.preview,
                "workbenchEdit": capability.workbench_edit,
                "nativePptxExport": capability.native_pptx_export,
            }[consumer]
            if state == "unsupported":
                unsupported.append(f"{kind}:{capability.reason}")
        if unsupported:
            raise ValueError(
                f"scene capability unsupported for {consumer}: " + ", ".join(unsupported)
            )


def seal_slide_scene(
    *,
    slide_id: str,
    revision: int,
    elements: list[SceneElement],
    hybrid_export: SlideHybridExportPlan | None = None,
) -> SlideSceneIR:
    ordered = sorted(elements, key=lambda element: (element.z_order, element.element_id))
    payload = {
        "schemaVersion": "slide-scene-ir/v1",
        "slideId": slide_id,
        "revision": revision,
        "elements": [
            element.model_dump(mode="json", by_alias=True) for element in ordered
        ],
    }
    if hybrid_export is not None:
        payload["hybridExport"] = hybrid_export.model_dump(
            mode="json", by_alias=True
        )
    return SlideSceneIR(**payload, slideDigest=scene_digest(payload))


def seal_deck_scene(
    *,
    deck_id: str,
    revision: int,
    composition_digest: str,
    source_digest: str,
    hybrid_profile_id: Literal["authored-editable-hybrid-ko-en-basic-v1"] | None = None,
    slides: list[SlideSceneIR],
    asset_manifest: list[SceneAssetManifestEntry] | None = None,
    data_manifest: list[SceneDataManifestEntry] | None = None,
    capabilities: list[SceneCapability],
) -> DeckSceneIR:
    payload = {
        "schemaVersion": "deck-scene-ir/v1",
        "deckId": deck_id,
        "revision": revision,
        "compositionDigest": composition_digest,
        "sourceDigest": source_digest,
        "slides": [slide.model_dump(mode="json", by_alias=True) for slide in slides],
        "assetManifest": [
            item.model_dump(mode="json", by_alias=True)
            for item in (asset_manifest or [])
        ],
        "dataManifest": [
            item.model_dump(mode="json", by_alias=True)
            for item in (data_manifest or [])
        ],
        "capabilities": [
            item.model_dump(mode="json", by_alias=True) for item in capabilities
        ],
    }
    if hybrid_profile_id is not None:
        payload["hybridProfileId"] = hybrid_profile_id
    return DeckSceneIR(**payload, sceneDigest=scene_digest(payload))


__all__ = [
    "AssetId",
    "DataId",
    "DeckSceneIR",
    "ElementId",
    "SceneAssetManifestEntry",
    "SceneCapability",
    "SceneChartElement",
    "SceneConnectorElement",
    "SceneDataManifestEntry",
    "SceneEditability",
    "SceneElement",
    "SceneGroupElement",
    "SceneImageElement",
    "SceneImageCropGeometry",
    "SCENE_OWNER_CAPABILITY_KINDS",
    "SceneShapeElement",
    "SceneTableElement",
    "SceneTableBorderStyle",
    "SceneTableCellBorders",
    "SceneTableCellStyle",
    "SceneTextElement",
    "SceneTextRun",
    "SceneTextStyle",
    "SlideSceneIR",
    "SceneOwnerDecision",
    "SceneRasterIsland",
    "SceneRasterZBand",
    "SlideHybridExportPlan",
    "scene_digest",
    "seal_deck_scene",
    "seal_slide_scene",
]
