"""Pydantic contracts for immutable source-content analysis."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Source geometry is part of the authored substance for template restyling.
# Native shapes carry image frames, grouping panels, dividers, and process
# structure; dropping them leaves the source technically counted but visually
# incomplete. Speaker notes remain metadata and are not rendered on slides.
RESTYLE_CONTENT_KINDS = frozenset({"text", "table", "chart", "image", "shape"})
DESIGN_ONLY_KINDS = frozenset({"source_note"})


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class SourceObjectInventorySummary(ContractModel):
    discovered_object_count: int = Field(ge=0)
    classified_object_count: int = Field(ge=0)
    content_bearing_object_count: int = Field(ge=0)
    unsupported_content_object_ids: tuple[str, ...] = ()
    unclassified_object_ids: tuple[str, ...] = ()
    external_relationship_count: int = Field(default=0, ge=0)

class SourceInventoryObject(ContractModel):
    object_id: str
    object_type: Literal["package_part", "relationship", "slide_object", "notes_object"]
    classification: str
    source_unit_id: str | None = None
    content_bearing: bool = False
    supported: bool = True
    relationship_type: str | None = None
    target_part: str | None = None


class SourceContentBlock(ContractModel):
    source_block_id: str
    kind: Literal["text", "table", "chart", "image", "shape", "source_note"]
    semantic_role: str
    order: int = Field(ge=0)
    payload: dict[str, Any]
    comparison_projection: dict[str, Any] | None = None
    content_digest: str
    native_candidate: bool = True
    fallback_asset_id: str | None = None


class SourceContentUnit(ContractModel):
    source_unit_id: str
    source_index: int = Field(ge=0)
    blocks: tuple[SourceContentBlock, ...]


class SourceContentCounts(ContractModel):
    source_units: int = Field(ge=0)
    text_blocks: int = Field(ge=0)
    table_cells: int = Field(ge=0)
    chart_values: int = Field(ge=0)
    unique_images: int = Field(ge=0)
    image_occurrences: int = Field(ge=0)
    raster_fallback_blocks: int = Field(default=0, ge=0)


class SourceContentManifest(ContractModel):
    contract_version: Literal["source-content-manifest/v1"] = (
        "source-content-manifest/v1"
    )
    manifest_id: str
    owner_id: str = Field(exclude=True)
    reference_snapshot_id: str
    source_type: Literal["pptx"] = "pptx"
    source_digest: str
    object_inventory: SourceObjectInventorySummary
    inventory_objects: tuple[SourceInventoryObject, ...]
    units: tuple[SourceContentUnit, ...]
    counts: SourceContentCounts
    manifest_digest: str


class SourceContentManifestPin(ContractModel):
    manifest_id: str
    digest: str


class SourceContentCapabilities(ContractModel):
    pptx: bool = True
    pdf: bool = False
    docx: bool = False
    ppt: bool = False


class SourceContentAnalysisSummary(ContractModel):
    source_type: Literal["pptx"] = "pptx"
    source_units: int = Field(ge=0)
    text_blocks: int = Field(ge=0)
    tables: int = Field(ge=0)
    charts: int = Field(ge=0)
    unique_images: int = Field(ge=0)
    image_occurrences: int = Field(ge=0)
    raster_fallback_blocks: int = Field(ge=0)


class ReferenceSnapshotBinding(ContractModel):
    reference_snapshot_id: str
    provider_id: str
    source_revision: str


class SourceContentAnalysisReceipt(ContractModel):
    contract_version: Literal["source-content-analysis/v1"] = (
        "source-content-analysis/v1"
    )
    status: Literal["ready"] = "ready"
    capabilities: SourceContentCapabilities = SourceContentCapabilities()
    manifest_pin: SourceContentManifestPin
    reference_snapshot_binding: ReferenceSnapshotBinding
    summary: SourceContentAnalysisSummary
    object_inventory: SourceObjectInventorySummary
