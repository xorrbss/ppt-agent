"""Metadata-only contracts for opt-in SVG slot readers and writers.

Artifact UUIDs refer to the existing PublicArtifact authority; these contracts
neither grant access nor resolve paths. Bytes must pass the existing authorized
artifact reader. This module does not enable the v2 writer.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator
from pydantic.alias_generators import to_camel
from utils.hashing import canonical_json_digest

POLICY_VERSION = "svg-image-preservation/v1"
PROFILE_VERSION = "svg-image-derivative/v1"
PreservationClass = Literal["critical", "supporting", "decorative"]
PolicyKind = Literal["mandatory", "selected_supporting", "selected_decorative", "not_selected"]


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, alias_generator=to_camel)


class PreservationPolicy(Metadata):
    policy_version: Literal["svg-image-preservation/v1"] = POLICY_VERSION
    kind: PolicyKind
    required: bool
    allow_omit: bool
    allow_exclude_on_failure: bool

    @model_validator(mode="after")
    def consistent(self):
        expected = {
            "mandatory": (True, False, False),
            "selected_supporting": (False, True, False),
            "selected_decorative": (False, True, True),
            "not_selected": (False, True, False),
        }[self.kind]
        if (self.required, self.allow_omit, self.allow_exclude_on_failure) != expected:
            raise ValueError("svg_asset_preservation_policy_inconsistent")
        return self


def decide_preservation_policy(*, selected: bool, preservation_class: PreservationClass,
                               explicitly_required: bool = False,
                               source_preservation_required: bool = False,
                               decorative_confirmed: bool = False) -> PreservationPolicy:
    """User/source preservation takes priority; uncertainty never permits loss."""
    if explicitly_required or source_preservation_required:
        kind = "mandatory"
    elif not selected:
        kind = "not_selected"
    elif preservation_class == "decorative" and decorative_confirmed:
        kind = "selected_decorative"
    else:
        kind = "selected_supporting"
    return PreservationPolicy(kind=kind, required=kind == "mandatory",
                              allow_omit=kind != "mandatory",
                              allow_exclude_on_failure=kind == "selected_decorative")


class ArtifactPointer(Metadata):
    """Public opaque pointer, deliberately excluding ArtifactRef.storage_key."""
    ref: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RasterDerivative(ArtifactPointer):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    media_type: Literal["image/png", "image/jpeg"]
    byte_size: int = Field(gt=0)
    recipe_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SourceLocator(Metadata):
    source_format: Literal["pptx", "pdf", "docx", "image"]
    page_index: int | None = Field(default=None, ge=0)
    coordinate_space: Literal["page-normalized", "document-order", "slide-normalized", "intrinsic"]
    object_id: str | None = None
    block_path: str | None = None


class FitPolicy(Metadata):
    fit: Literal["contain", "cover"] = "contain"
    allow_crop: bool = False

    @model_validator(mode="after")
    def crop_consistent(self):
        if self.fit == "cover" and not self.allow_crop:
            raise ValueError("svg_asset_crop_not_allowed")
        return self


class SourceSlot(Metadata):
    slot_id: str = Field(pattern=r"^source-slot-[A-Za-z0-9_-]+$")
    source_asset_id: uuid.UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    semantic_description: str = Field(max_length=4000)
    occurrence_id: uuid.UUID
    source_locator: SourceLocator
    extraction_kind: Literal["embedded_raster", "region_render", "scanned_page", "direct_image", "rendered_appearance"]
    source_appearance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preservation_class: PreservationClass
    classification_confidence: float | None = Field(default=None, ge=0, le=1)
    classification_reason: str = Field(default="", max_length=2000)
    importance_uncertain: bool = True
    referenced_by_body: bool = False
    policy: PreservationPolicy
    selection_reason: str = Field(min_length=1, max_length=2000)
    model_derivative: RasterDerivative | None = None
    screen_policy: FitPolicy = Field(default_factory=FitPolicy)
    export_policy: FitPolicy = Field(default_factory=FitPolicy)

    @model_validator(mode="after")
    def consistent(self):
        if self.policy.kind == "selected_decorative" and (
            self.preservation_class != "decorative" or self.importance_uncertain
        ):
            raise ValueError("svg_asset_decorative_not_confirmed")
        if self.policy.kind != "not_selected" and self.model_derivative is None:
            raise ValueError("svg_asset_model_derivative_required")
        return self


class OfferedSlots(Metadata):
    page_index: int = Field(ge=0)
    slot_ids: tuple[str, ...]


class ExtractionDiagnostic(Metadata):
    code: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    source_locator: SourceLocator
    occurrence_id: uuid.UUID | None = None


def canonical_digest(value: BaseModel | dict) -> str:
    data = value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else dict(value)
    data.pop("digest", None)
    return canonical_json_digest(data)


class SourceManifest(Metadata):
    schema_version: Literal["svg-asset-manifest/v1"] = "svg-asset-manifest/v1"
    profile_version: Literal["svg-image-derivative/v1"] = PROFILE_VERSION
    presentation_id: uuid.UUID
    source_revision: int = Field(ge=0)
    slots: tuple[SourceSlot, ...]
    offered_pages: tuple[OfferedSlots, ...]
    extraction_diagnostics: tuple[ExtractionDiagnostic, ...] = ()
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def consistent(self, info: ValidationInfo):
        slots = {slot.slot_id: slot for slot in self.slots}
        if len(slots) != len(self.slots) or len({s.occurrence_id for s in self.slots}) != len(self.slots):
            raise ValueError("svg_asset_duplicate_slot_or_occurrence")
        if len({page.page_index for page in self.offered_pages}) != len(self.offered_pages):
            raise ValueError("svg_asset_duplicate_offered_page")
        offered = set()
        for page in self.offered_pages:
            if len(set(page.slot_ids)) != len(page.slot_ids):
                raise ValueError("svg_asset_duplicate_offered_slot")
            for slot_id in page.slot_ids:
                if slot_id not in slots or slots[slot_id].policy.kind == "not_selected":
                    raise ValueError("svg_asset_invalid_offered_slot")
                offered.add(slot_id)
        if any(s.policy.kind != "not_selected" and s.slot_id not in offered for s in self.slots):
            raise ValueError("svg_asset_selected_slot_not_offered")
        if not (info.context or {}).get("allow_unsealed") and canonical_digest(self) != self.digest:
            raise ValueError("svg_asset_manifest_digest_mismatch")
        return self

    @classmethod
    def seal(cls, value: dict) -> "SourceManifest":
        """Validate all fields, then bind the digest to normalized defaults."""
        draft = dict(value)
        draft["digest"] = "sha256:" + "0" * 64
        normalized = cls.model_validate(draft, context={"allow_unsealed": True})
        sealed = normalized.model_dump(mode="json", by_alias=True)
        sealed["digest"] = canonical_digest(normalized)
        return cls.model_validate(sealed)


class DerivativeRecipe(Metadata):
    profile_version: str = PROFILE_VERSION
    decoder_version: str = Field(min_length=1)
    encoder_version: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    colorspace: Literal["srgb"] = "srgb"
    alpha: bool
    media_type: Literal["image/png", "image/jpeg"]
    crop: tuple[float, float, float, float] | None = None
    rotation: Literal[0, 90, 180, 270] = 0
    quality: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def consistent(self):
        if self.alpha and self.media_type == "image/jpeg":
            raise ValueError("svg_asset_jpeg_alpha_unsupported")
        if self.crop is not None:
            x, y, width, height = self.crop
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1-x and 0 < height <= 1-y):
                raise ValueError("svg_asset_invalid_normalized_crop")
        return self


def derivative_identity(*, tenant_id: str, owner_id: uuid.UUID,
                        source_appearance_sha256: str, recipe: DerivativeRecipe) -> tuple[str, str, str, str]:
    from models.sql.public_artifact import normalize_sha256
    if not tenant_id.strip():
        raise ValueError("svg_asset_tenant_required")
    return tenant_id, str(owner_id), normalize_sha256(source_appearance_sha256), canonical_digest(recipe)


def page_image_checkpoint(*, manifest: SourceManifest, manifest_ref: uuid.UUID,
                          page_index: int, used_slot_ids: tuple[str, ...]) -> dict:
    """Persist references only, without source bytes or model derivative copies."""
    page = next((page for page in manifest.offered_pages if page.page_index == page_index), None)
    if page is None or not set(used_slot_ids).issubset(page.slot_ids):
        raise ValueError("svg_asset_checkpoint_slot_not_offered")
    if len(set(used_slot_ids)) != len(used_slot_ids):
        raise ValueError("svg_asset_checkpoint_duplicate_slot")
    return {"sourceManifestRef": str(manifest_ref), "sourceManifestDigest": manifest.digest,
            "offeredSlotIds": list(page.slot_ids), "usedSlotIds": list(used_slot_ids)}


def unused_slot_outcome(slot: SourceSlot) -> dict:
    """A missing reason is reported honestly, never inferred from image class."""
    return {
        "slotId": slot.slot_id,
        "reason": "not_selected" if slot.policy.kind == "not_selected" else "not_used_by_model",
        "requiredMissing": slot.policy.required or not slot.policy.allow_omit,
        "reviewRequired": slot.policy.kind != "not_selected" and (
            slot.policy.required or slot.referenced_by_body or slot.importance_uncertain
            or slot.preservation_class == "critical"
        ),
    }


class SvgPlacement(Metadata):
    slot_id: str = Field(pattern=r"^source-slot-[A-Za-z0-9_-]+$")
    occurrence_id: uuid.UUID
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    fit: FitPolicy = Field(default_factory=FitPolicy)
    crop: tuple[float, float, float, float] | None = None
    transform: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    derivative: RasterDerivative

    @model_validator(mode="after")
    def consistent(self):
        import math
        if not all(math.isfinite(v) for v in (self.x, self.y, self.width, self.height, *self.transform)):
            raise ValueError("svg_asset_placement_geometry_invalid")
        if self.crop is not None:
            if not self.fit.allow_crop:
                raise ValueError("svg_asset_crop_not_allowed")
            x, y, width, height = self.crop
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1-x and 0 < height <= 1-y):
                raise ValueError("svg_asset_invalid_normalized_crop")
        return self


class PlacementManifest(Metadata):
    schema_version: Literal["svg-placement-manifest/v1"] = "svg-placement-manifest/v1"
    canonical_svg_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    slide_revision: int = Field(ge=0)
    purpose: Literal["screen", "export", "export-pptx", "export-pdf"]
    placements: tuple[SvgPlacement, ...]
    renderer_version: str = Field(min_length=1)
    profile_version: Literal["svg-image-derivative/v1"] = PROFILE_VERSION
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def consistent(self, info: ValidationInfo):
        if not (info.context or {}).get("allow_unsealed") and canonical_digest(self) != self.digest:
            raise ValueError("svg_asset_placement_digest_mismatch")
        return self

    @classmethod
    def seal(cls, value: dict) -> "PlacementManifest":
        draft = dict(value)
        draft["digest"] = "sha256:" + "0" * 64
        normalized = cls.model_validate(draft, context={"allow_unsealed": True})
        sealed = normalized.model_dump(mode="json", by_alias=True)
        sealed["digest"] = canonical_digest(normalized)
        return cls.model_validate(sealed)

    def validate_source(self, source: SourceManifest, *, page_index: int) -> None:
        if self.source_manifest_digest != source.digest:
            raise ValueError("svg_asset_placement_source_digest_mismatch")
        page = next((p for p in source.offered_pages if p.page_index == page_index), None)
        if page is None:
            raise ValueError("svg_asset_placement_page_not_offered")
        slots = {s.slot_id: s for s in source.slots}
        used = set()
        for placement in self.placements:
            if placement.slot_id not in page.slot_ids:
                raise ValueError("svg_asset_placement_slot_not_offered")
            slot = slots[placement.slot_id]
            if placement.occurrence_id != slot.occurrence_id:
                raise ValueError("svg_asset_placement_occurrence_mismatch")
            allowed_fit = slot.screen_policy if self.purpose == "screen" else slot.export_policy
            if placement.fit != allowed_fit:
                raise ValueError("svg_asset_placement_fit_policy_mismatch")
            used.add(placement.slot_id)
        if any((slots[s].policy.required or not slots[s].policy.allow_omit) and s not in used for s in page.slot_ids):
            raise ValueError("svg_asset_required_placement_missing")


async def read_derivative_bytes(*, artifact_service, session, derivative: RasterDerivative,
                                owner_id: uuid.UUID, tenant_id: str,
                                max_bytes: int, max_pixels: int) -> bytes:
    """Resolve through PublicArtifact authority before reading worker-only bytes.

    The caller supplies trusted operation owner/tenant context, never model input.
    Operation grants for export transport remain the existing gateway's concern.
    """
    import asyncio
    import io
    from PIL import Image

    if derivative.byte_size > max_bytes or derivative.width * derivative.height > max_pixels:
        raise ValueError("svg_asset_derivative_budget_exceeded")
    reference = await artifact_service.resolve_ref(
        session, artifact_id=derivative.ref, owner_id=owner_id,
        tenant_id=tenant_id, expected_digest=derivative.sha256,
    )
    if reference.media_type != derivative.media_type or reference.size_bytes != derivative.byte_size:
        raise ValueError("svg_asset_derivative_metadata_mismatch")
    payload = await artifact_service.read_ref_bytes(reference)
    if len(payload) != derivative.byte_size or hashlib.sha256(payload).hexdigest() != derivative.sha256:
        raise ValueError("svg_asset_derivative_digest_mismatch")

    def verify():
        with Image.open(io.BytesIO(payload)) as image:
            expected_format = {"image/png": "PNG", "image/jpeg": "JPEG"}[derivative.media_type]
            if image.format != expected_format or image.size != (derivative.width, derivative.height):
                raise ValueError("svg_asset_derivative_decoder_mismatch")
            image.verify()

    await asyncio.to_thread(verify)
    return payload
