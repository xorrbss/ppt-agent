"""Bind one existing export attempt to the complete semantic digest graph."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    AuthoredDeckEmissionManifestV1,
    AuthoredEmissionBundleV1,
    ExportAttemptDigestBindingV1,
    LegacyHtmlBundleV1,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticExportBinding,
)
from models.sql.presentation_export_attempt import PresentationExportAttempt

from .canonical import SemanticContractError, canonical_digest, seal_export_binding


async def bind_existing_export_attempt(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    binding: ExportAttemptDigestBindingV1,
    tenant_id: str = "default",
) -> ExportAttemptDigestBindingV1:
    """Append an immutable binding after resolving the DB attempt and graph."""

    sealed = seal_export_binding(binding)
    if binding.binding_digest is not None and binding.binding_digest != sealed.binding_digest:
        raise SemanticContractError("authored_semantic_export_binding_digest_mismatch")
    attempt = await session.scalar(
        select(PresentationExportAttempt).where(
            PresentationExportAttempt.id == sealed.export_attempt_id,
            PresentationExportAttempt.owner_scope == str(owner_id),
            PresentationExportAttempt.presentation_id == sealed.presentation_id,
        )
    )
    if attempt is None:
        raise SemanticContractError("authored_semantic_export_attempt_not_found")
    if attempt.source_revision != sealed.presentation_revision:
        raise SemanticContractError("authored_semantic_export_revision_mismatch")
    pointer = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == owner_id,
            AuthoredDeckEmissionActivePointer.tenant_id == tenant_id,
            AuthoredDeckEmissionActivePointer.presentation_id == sealed.presentation_id,
            AuthoredDeckEmissionActivePointer.canonical_mode == "bundleGraph",
        )
    )
    if (
        pointer is None
        or pointer.presentation_revision != sealed.presentation_revision
        or pointer.canonical_digest != sealed.canonical_digest
    ):
        raise SemanticContractError("authored_semantic_export_stale_manifest")
    manifest_record = await session.scalar(
        select(AuthoredDeckEmissionManifestRecord).where(
            AuthoredDeckEmissionManifestRecord.id == pointer.manifest_id
        )
    )
    if manifest_record is None:
        raise SemanticContractError("authored_semantic_active_manifest_unresolvable")
    manifest = AuthoredDeckEmissionManifestV1.model_validate_json(
        json.dumps(manifest_record.payload, ensure_ascii=False)
    )
    if (
        manifest.operation_id != sealed.operation_id
        or manifest.generation_attempt_id != sealed.generation_attempt_id
        or manifest.master_contract_digest != sealed.master_contract_digest
        or manifest.composition_digest != sealed.composition_digest
        or manifest.font_manifest_digest != sealed.font_manifest_digest
        or manifest.asset_manifest_digest != sealed.asset_manifest_digest
        or manifest.canonical_digest != sealed.manifest_digest
        or canonical_digest({"qualityPolicyVersion": manifest.quality_policy_version})
        != sealed.quality_policy_digest
    ):
        raise SemanticContractError("authored_semantic_export_manifest_binding_mismatch")
    records = list(
        await session.scalars(
            select(AuthoredEmissionBundleRecord).where(
                AuthoredEmissionBundleRecord.bundle_digest.in_(
                    [item.bundle_digest for item in manifest.ordered_slides]
                )
            )
        )
    )
    by_digest = {record.bundle_digest: record for record in records}
    semantic_digests: list[str] = []
    origins: list[uuid.UUID | None] = []
    for entry in manifest.ordered_slides:
        record = by_digest.get(entry.bundle_digest)
        if record is None:
            raise SemanticContractError("authored_semantic_export_bundle_unresolvable")
        model = AuthoredEmissionBundleV1 if entry.bundle_kind == "authored" else LegacyHtmlBundleV1
        bundle = model.model_validate_json(json.dumps(record.payload, ensure_ascii=False))
        component = (
            bundle.semantic_component
            if isinstance(bundle, AuthoredEmissionBundleV1)
            else bundle.compatibility_component
        )
        if component.digest is None:
            raise SemanticContractError("authored_semantic_export_component_digest_missing")
        semantic_digests.append(component.digest)
        origins.append(bundle.origin_generation_attempt_id)
    if semantic_digests != sealed.semantic_digests or origins != sealed.slide_origin_generation_attempt_ids:
        raise SemanticContractError("authored_semantic_export_lineage_mismatch")
    if attempt.artifact_sha256 is not None:
        expected_pptx = "sha256:" + attempt.artifact_sha256
        if sealed.pptx_digest != expected_pptx:
            raise SemanticContractError("authored_semantic_export_pptx_digest_mismatch")
    existing = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.export_attempt_id == sealed.export_attempt_id
        )
    )
    if existing is not None:
        if existing.binding_digest != sealed.binding_digest:
            raise SemanticContractError("authored_semantic_export_attempt_already_bound")
        return sealed
    session.add(
        AuthoredSemanticExportBinding(
            owner_id=owner_id,
            tenant_id=tenant_id,
            export_attempt_id=sealed.export_attempt_id,
            presentation_id=sealed.presentation_id,
            presentation_revision=sealed.presentation_revision,
            canonical_digest=sealed.canonical_digest,
            renderer_source_digest=sealed.renderer_source_digest,
            mode=sealed.mode,
            serializer_id=sealed.serializer_id,
            serializer_version=sealed.serializer_version,
            pptx_digest=sealed.pptx_digest,
            binding_digest=sealed.binding_digest,
            payload=sealed.model_dump(mode="json", by_alias=True),
        )
    )
    await session.flush()
    return sealed


__all__ = ["bind_existing_export_attempt"]
