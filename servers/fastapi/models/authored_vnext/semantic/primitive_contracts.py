"""Primitive authored semantic payload contracts (mechanically extracted)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel




QID_PATTERN = r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$"
Qid = Annotated[str, Field(pattern=QID_PATTERN)]
ArtifactRef = Annotated[str, Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")]
PolicyVersion = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._/-]{0,127}$")]


class ArtifactPointerV1(StrictCamelModel):
    schema_version: Literal["authored-artifact-pointer/v1"] = "authored-artifact-pointer/v1"
    artifact_ref: ArtifactRef
    digest: Digest
    byte_length: int = Field(ge=0, le=64 * 1024 * 1024)
    media_type: str = Field(min_length=1, max_length=128)


class ResolvedAssetV1(StrictCamelModel):
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._:-]{0,191}$")
    artifact: ArtifactPointerV1
    locator_digest: Digest
    resolver_version: str = Field(min_length=1, max_length=128)
    width_px: int = Field(gt=0, le=32768)
    height_px: int = Field(gt=0, le=32768)
    provenance: Literal["authored", "master", "composition", "internal-reviewed"]
    retention_class: Literal["active", "undo-redo", "audit-hold", "evidence-hold"]


class ResolvedFontV1(StrictCamelModel):
    face_id: str = Field(pattern=r"^font:[a-z0-9][a-z0-9._:-]{0,191}$")
    family: str = Field(min_length=1, max_length=128)
    face: str = Field(min_length=1, max_length=128)
    artifact: ArtifactPointerV1
    locator_digest: Digest
    resolver_version: str = Field(min_length=1, max_length=128)
    embedding_permitted: bool
    subsetting_permitted: bool
    retention_class: Literal["active", "undo-redo", "audit-hold", "evidence-hold"]


class AssetManifestV1(StrictCamelModel):
    schema_version: Literal["authored-asset-manifest/v1"] = "authored-asset-manifest/v1"
    assets: list[ResolvedAssetV1] = Field(default_factory=list, max_length=512)
    digest: Digest | None = None

    @model_validator(mode="after")
    def unique_actual_assets(self) -> "AssetManifestV1":
        _require_unique((item.asset_id for item in self.assets), "authored_asset_id_duplicate")
        return self


class FontManifestV1(StrictCamelModel):
    schema_version: Literal["authored-font-manifest/v1"] = "authored-font-manifest/v1"
    fonts: list[ResolvedFontV1] = Field(default_factory=list, max_length=256)
    digest: Digest | None = None

    @model_validator(mode="after")
    def unique_actual_faces(self) -> "FontManifestV1":
        _require_unique((item.face_id for item in self.fonts), "authored_font_face_duplicate")
        return self


class ElementIdentityRegistryV1(StrictCamelModel):
    schema_version: Literal["authored-element-identity-registry/v1"] = (
        "authored-element-identity-registry/v1"
    )
    active_qids: list[Qid] = Field(max_length=1024)
    retired_qids: list[Qid] = Field(default_factory=list, max_length=4096)
    lineage_digest: Digest | None = None
    digest: Digest | None = None

    @model_validator(mode="after")
    def validate_registry(self) -> "ElementIdentityRegistryV1":
        _require_unique(self.active_qids, "authored_semantic_qid_duplicate")
        _require_unique(self.retired_qids, "authored_semantic_retired_qid_duplicate")
        if set(self.active_qids).intersection(self.retired_qids):
            raise ValueError("authored_semantic_qid_reused")
        if self.active_qids != sorted(self.active_qids):
            raise ValueError("authored_semantic_qid_registry_not_canonical")
        if self.retired_qids != sorted(self.retired_qids):
            raise ValueError("authored_semantic_retired_qid_registry_not_canonical")
        return self


class TextRunV1(StrictCamelModel):
    text: str = Field(max_length=16384)
    language: str = Field(min_length=2, max_length=35)
    script: Literal["cjk", "latin", "common"]
    font_family: str = Field(min_length=1, max_length=128)
    font_face: str = Field(min_length=1, max_length=128)
    font_size_pt: float = Field(gt=0, le=400)
    weight: int = Field(ge=100, le=1000, multiple_of=100)
    italic: bool = False
    underline: bool = False
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


class TextParagraphV1(StrictCamelModel):
    runs: list[TextRunV1] = Field(min_length=1, max_length=512)
    alignment: Literal["left", "center", "right", "justify"] = "left"
    direction: Literal["ltr", "rtl"] = "ltr"
    explicit_break_after: bool = False


class TextPayloadV1(StrictCamelModel):
    paragraphs: list[TextParagraphV1] = Field(min_length=1, max_length=256)
    whitespace: Literal["normal", "pre", "pre-wrap", "nowrap"] = "normal"


class SourceTextRunV2(TextRunV1):
    """Source run boundary spaces and explicit breaks are content, not padding."""

    text: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(max_length=16384)


class SourceTextParagraphV2(TextParagraphV1):
    runs: list[SourceTextRunV2] = Field(min_length=1, max_length=512)


class SourceTextPayloadV2(TextPayloadV1):
    schema_version: Literal["authored-source-text/v2"] = "authored-source-text/v2"
    source_fit_policy: Literal["composer-source-fit/v2"]
    paragraphs: list[SourceTextParagraphV2] = Field(min_length=1, max_length=256)


class ImagePayloadV1(StrictCamelModel):
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._:-]{0,191}$")
    asset_digest: Digest
    alt_text: str = Field(max_length=2048)
    intrinsic_width_px: int = Field(gt=0, le=32768)
    intrinsic_height_px: int = Field(gt=0, le=32768)
    object_fit: Literal["contain", "cover", "fill", "none", "scale-down"] = "contain"
    object_position: str = Field(default="50% 50%", min_length=1, max_length=128)


class ShapePayloadV1(StrictCamelModel):
    preset: Literal["rectangle", "roundedRectangle", "ellipse", "line"]
    fill: str | None = Field(default=None, max_length=128)
    stroke: str | None = Field(default=None, max_length=128)
    radius_pt: float | None = Field(default=None, ge=0, le=1000)


class BoundsIntentV1(StrictCamelModel):
    x_pt: float
    y_pt: float
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    rotation_deg: float = Field(default=0, ge=-360, le=360)


def _require_unique(values: Any, code: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(code)
