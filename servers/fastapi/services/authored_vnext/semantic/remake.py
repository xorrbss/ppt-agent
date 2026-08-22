"""Full ordered-set semantic republish helper for Authored page remake."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import AssetManifestV1, FontManifestV1
from models.sql.authored_semantic_bundle import AuthoredSemanticArtifact
from models.sql.slide import SlideModel

from .canonical import SemanticContractError, bytes_digest
from .publication import publish_authored_semantic_bundle_graph
from .query import get_active_authored_emission


async def republish_authored_semantic_bundle_graph_for_remake(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    expected_presentation_revision: int,
    operation_id: uuid.UUID,
    generation_attempt_id: uuid.UUID | None,
    ordered_slides: Sequence[SlideModel],
    htmls: Sequence[str],
    semantic_drafts: Sequence[Mapping[str, Any]],
    slide_asset_manifests: Sequence[AssetManifestV1] | None = None,
    slide_font_manifests: Sequence[FontManifestV1] | None = None,
    resolved_artifact_bytes: Mapping[str, bytes] | None = None,
    tenant_id: str = "default",
):
    """Preserve active deck pins/policy while publishing a complete remake graph."""

    active = await get_active_authored_emission(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    if (
        active.canonical_mode != "bundleGraph"
        or active.manifest is None
        or active.presentation_revision != expected_presentation_revision
        or len(active.bundles) != len(ordered_slides)
    ):
        raise SemanticContractError("authored_semantic_remake_active_manifest_mismatch")
    assets = list(slide_asset_manifests) if slide_asset_manifests is not None else []
    fonts = list(slide_font_manifests) if slide_font_manifests is not None else []
    inherited_bytes = dict(resolved_artifact_bytes or {})
    if not assets or not fonts:
        inherited_assets: list[AssetManifestV1] = []
        inherited_fonts: list[FontManifestV1] = []
        for bundle in active.bundles:
            inherited_assets.append(
                await _load_manifest_artifact(
                    session,
                    owner_id=owner_id,
                    tenant_id=tenant_id,
                    digest=bundle.asset_manifest.digest,
                    model=AssetManifestV1,
                )
            )
            inherited_fonts.append(
                await _load_manifest_artifact(
                    session,
                    owner_id=owner_id,
                    tenant_id=tenant_id,
                    digest=bundle.font_manifest.digest,
                    model=FontManifestV1,
                )
            )
        if not assets:
            assets = inherited_assets
        if not fonts:
            fonts = inherited_fonts
    for manifest in assets:
        for entry in manifest.assets:
            inherited_bytes.setdefault(
                entry.artifact.digest,
                await _load_bytes(session, owner_id, tenant_id, entry.artifact.digest),
            )
    for manifest in fonts:
        for entry in manifest.fonts:
            inherited_bytes.setdefault(
                entry.artifact.digest,
                await _load_bytes(session, owner_id, tenant_id, entry.artifact.digest),
            )
    return await publish_authored_semantic_bundle_graph(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        expected_presentation_revision=expected_presentation_revision,
        operation_id=operation_id,
        generation_attempt_id=generation_attempt_id,
        slides=ordered_slides,
        htmls=htmls,
        semantic_drafts=semantic_drafts,
        master_contract_digest=active.manifest.master_contract_digest,
        composition_digest=active.manifest.composition_digest,
        slide_asset_manifests=assets,
        slide_font_manifests=fonts,
        resolved_artifact_bytes=inherited_bytes,
        quality_policy_version=active.manifest.quality_policy_version,
        tenant_id=tenant_id,
    )


async def _load_manifest_artifact(session, *, owner_id, tenant_id, digest, model):
    content = await _load_bytes(session, owner_id, tenant_id, digest)
    try:
        # Canonical JSON can represent an integral value in exponent form
        # (for example 740 as 7.4e2). JSON decoders surface that token as a
        # float, so rehydrate with numeric coercion after the artifact bytes
        # themselves have been digest-verified.
        return model.model_validate_json(content, strict=False)
    except Exception as error:
        raise SemanticContractError("authored_semantic_manifest_artifact_invalid") from error


async def _load_bytes(
    session: AsyncSession, owner_id: uuid.UUID, tenant_id: str, digest: str
) -> bytes:
    artifact = await session.scalar(
        select(AuthoredSemanticArtifact).where(
            AuthoredSemanticArtifact.owner_id == owner_id,
            AuthoredSemanticArtifact.tenant_id == tenant_id,
            AuthoredSemanticArtifact.content_digest == digest,
        )
    )
    if artifact is None:
        raise SemanticContractError("authored_semantic_artifact_unresolvable")
    content = bytes(artifact.content_bytes)
    if bytes_digest(content) != digest:
        raise SemanticContractError("authored_semantic_artifact_digest_mismatch")
    return content


__all__ = ["republish_authored_semantic_bundle_graph_for_remake"]
