"""Fenced SVG asset publication through the existing durable artifact authority.

The caller owns the transaction. Public results contain opaque refs and digests;
neither storage keys nor raster payloads belong in canonical SVG/checkpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from services.artifact_store import (
    ArtifactBindingError,
    DurableArtifactService,
    _locked_current_operation,
    promote_artifact_publication_if_current,
)
from services.svg_asset_manifest import ArtifactPointer, PlacementManifest, RasterDerivative, SourceManifest
from services.svg_image_derivative import ModelPreview
from utils.hashing import sha256_hex


async def _load_manifest_pointer(session, service, *, pointer, owner_id, tenant_id, kind, model):
    pointer = ArtifactPointer.model_validate(pointer)
    reference = await service.resolve_ref(
        session,
        artifact_id=pointer.ref,
        owner_id=owner_id,
        tenant_id=tenant_id,
        expected_digest=pointer.sha256,
    )
    if reference.kind != kind or reference.media_type != "application/json":
        raise ArtifactBindingError("svg_asset_manifest_kind_mismatch")
    payload = await service.read_ref_bytes(reference)
    if sha256_hex(payload) != pointer.sha256:
        raise ArtifactBindingError("svg_asset_manifest_bytes_mismatch")
    return model.model_validate_json(payload)


async def load_source_manifest_pointer(
    session: AsyncSession,
    service: DurableArtifactService,
    *,
    pointer: ArtifactPointer | dict,
    owner_id: uuid.UUID,
    tenant_id: str,
) -> SourceManifest:
    return await _load_manifest_pointer(
        session,
        service,
        pointer=pointer,
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="source-manifest",
        model=SourceManifest,
    )


async def load_placement_manifest_pointer(
    session: AsyncSession,
    service: DurableArtifactService,
    *,
    pointer: ArtifactPointer | dict,
    owner_id: uuid.UUID,
    tenant_id: str,
) -> PlacementManifest:
    return await _load_manifest_pointer(
        session,
        service,
        pointer=pointer,
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="placement-manifest",
        model=PlacementManifest,
    )


@dataclass(frozen=True)
class SvgPublicationContext:
    operation_id: uuid.UUID
    owner_id: uuid.UUID
    tenant_id: str
    attempt_number: int
    attempt_token: str
    fencing_token: int
    operation_kind: Literal["generation", "export"] = "generation"

    def fence(self) -> dict:
        return dict(
            operation_id=self.operation_id,
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            attempt_number=self.attempt_number,
            attempt_token=self.attempt_token,
            fencing_token=self.fencing_token,
            operation_kind=self.operation_kind,
        )


async def _persist_if_current(
    session: AsyncSession,
    service: DurableArtifactService,
    *,
    context: SvgPublicationContext,
    publication_name: str,
    revision: int,
    kind: str,
    payload: bytes,
    media_type: str,
):
    if not publication_name.strip() or len(publication_name.strip()) > 64 or revision < 0:
        raise ValueError("svg_asset_publication_identity_invalid")
    if await _locked_current_operation(session, **context.fence()) is None:
        return None
    reference = await service.persist_bytes(
        session,
        owner_id=context.owner_id,
        tenant_id=context.tenant_id,
        kind=kind,
        payload=payload,
        media_type=media_type,
        created_by_operation_id=context.operation_id,
        retention_class="checkpoint",
    )
    published = await promote_artifact_publication_if_current(
        session,
        **context.fence(),
        publication_name=publication_name,
        revision=revision,
        quality_digest=reference.sha256,
        artifact_id=reference.artifact_id,
    )
    if published is None:
        raise ArtifactBindingError("svg_asset_publication_fence_lost")
    return ArtifactPointer(ref=reference.artifact_id, sha256=reference.sha256)


async def persist_derivative_if_current(
    session: AsyncSession,
    service: DurableArtifactService,
    *,
    context: SvgPublicationContext,
    preview: ModelPreview,
    publication_name: str,
    revision: int,
) -> RasterDerivative | None:
    if sha256_hex(preview.payload) != preview.sha256:
        raise ArtifactBindingError("svg_asset_derivative_digest_mismatch")
    from services.svg_asset_manifest import canonical_digest

    if canonical_digest(preview.recipe) != preview.recipe_digest:
        raise ArtifactBindingError("svg_asset_derivative_recipe_mismatch")
    if (preview.width, preview.height, preview.media_type) != (
        preview.recipe.width,
        preview.recipe.height,
        preview.recipe.media_type,
    ):
        raise ArtifactBindingError("svg_asset_derivative_recipe_dimensions_mismatch")
    pointer = await _persist_if_current(
        session,
        service,
        context=context,
        publication_name=publication_name,
        revision=revision,
        kind="image-derivative",
        payload=preview.payload,
        media_type=preview.media_type,
    )
    if pointer is None:
        return None
    return RasterDerivative(
        ref=pointer.ref,
        sha256=pointer.sha256,
        width=preview.width,
        height=preview.height,
        media_type=preview.media_type,
        byte_size=len(preview.payload),
        recipe_digest=preview.recipe_digest,
    )


async def persist_manifest_if_current(
    session: AsyncSession,
    service: DurableArtifactService,
    *,
    context: SvgPublicationContext,
    manifest: SourceManifest | PlacementManifest,
    publication_name: str,
    revision: int,
) -> ArtifactPointer | None:
    # Revalidate sealed bytes even when callers constructed a model without validation.
    manifest = type(manifest).model_validate(manifest.model_dump(mode="json", by_alias=True))
    operation = await _locked_current_operation(session, **context.fence())
    if operation is None:
        return None
    if isinstance(manifest, SourceManifest):
        if manifest.presentation_id != operation.presentation_id or manifest.source_revision != revision:
            raise ArtifactBindingError("svg_asset_manifest_operation_mismatch")
        derivatives = [slot.model_derivative for slot in manifest.slots if slot.model_derivative]
        kind = "source-manifest"
    else:
        if manifest.slide_revision != revision:
            raise ArtifactBindingError("svg_asset_manifest_revision_mismatch")
        derivatives = [placement.derivative for placement in manifest.placements]
        kind = "placement-manifest"
    for derivative in derivatives:
        reference = await service.resolve_ref(
            session,
            artifact_id=derivative.ref,
            owner_id=context.owner_id,
            tenant_id=context.tenant_id,
            expected_digest=derivative.sha256,
        )
        if (
            reference.kind != "image-derivative"
            or reference.media_type != derivative.media_type
            or reference.size_bytes != derivative.byte_size
        ):
            raise ArtifactBindingError("svg_asset_manifest_derivative_binding_mismatch")
    payload = json.dumps(
        manifest.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return await _persist_if_current(
        session,
        service,
        context=context,
        publication_name=publication_name,
        revision=revision,
        kind=kind,
        payload=payload,
        media_type="application/json",
    )
