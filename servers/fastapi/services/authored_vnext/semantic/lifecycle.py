"""Atomic ordered remake, manifest rollback, and legacy cutover operations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any, Literal
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    AssetManifestV1,
    AuthoredDeckEmissionManifestV1,
    AuthoredEmissionBundleV1,
    AuthoredSemanticSourceV1,
    FontManifestV1,
    SemanticUnavailableReceiptV1,
)
from models.authored_vnext.semantic.lifecycle import (
    AuthoredEmissionLifecycleReceiptV1,
    AuthoredLegacyCutoverRequestV1,
    AuthoredManifestRollbackRequestV1,
    AuthoredOrderedRemakeRequestV1,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_semantic_candidate import compile_semantic_components
from utils.datetime_utils import get_current_utc_datetime

from .canonical import SemanticContractError, bytes_digest, canonical_digest
from .publication import publish_authored_semantic_bundle_graph
from .remake import republish_authored_semantic_bundle_graph_for_remake


async def remake_ordered_authored_emission(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    request: AuthoredOrderedRemakeRequestV1,
    tenant_id: str = "default",
) -> AuthoredEmissionLifecycleReceiptV1:
    presentation, pointer = await _load_and_validate_cas(
        session, presentation_id, owner_id, request, tenant_id=tenant_id
    )
    if pointer.canonical_mode != "bundleGraph":
        raise SemanticContractError("authored_semantic_remake_active_manifest_required")
    await _reject_reused_attempt(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        operation_id=request.operation_id,
        generation_attempt_id=request.generation_attempt_id,
    )
    slides = await _ordered_slides(session, presentation_id, owner_id)
    htmls = [str(slide.html_content or "") for slide in slides]
    drafts = compile_semantic_components(htmls)
    manifest = await republish_authored_semantic_bundle_graph_for_remake(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        expected_presentation_revision=request.expected_presentation_revision,
        operation_id=request.operation_id,
        generation_attempt_id=request.generation_attempt_id,
        ordered_slides=slides,
        htmls=htmls,
        semantic_drafts=drafts,
        tenant_id=tenant_id,
    )
    await _advance_presentation_revision(
        session, presentation, request.expected_presentation_revision
    )
    return _receipt(
        action="orderedRemake",
        presentation_id=presentation_id,
        operation_id=request.operation_id,
        generation_attempt_id=request.generation_attempt_id,
        previous_digest=request.expected_manifest_digest,
        target_digest=None,
        active_digest=_required(manifest.canonical_digest),
        canonical_mode="bundleGraph",
        revision=manifest.presentation_revision,
        generation=request.expected_active_generation + 1,
        slides=len(slides),
        drafts=drafts,
    )


async def rollback_authored_emission_manifest(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    request: AuthoredManifestRollbackRequestV1,
    tenant_id: str = "default",
) -> AuthoredEmissionLifecycleReceiptV1:
    presentation, _pointer = await _load_and_validate_cas(
        session, presentation_id, owner_id, request, tenant_id=tenant_id
    )
    await _reject_reused_attempt(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        operation_id=request.operation_id,
        generation_attempt_id=request.generation_attempt_id,
    )
    target = await _load_manifest(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        digest=request.target_manifest_digest,
    )
    slides = await _ordered_slides(session, presentation_id, owner_id)
    if [slide.id for slide in slides] != [entry.slide_id for entry in target.ordered_slides]:
        raise SemanticContractError("authored_semantic_rollback_ordered_set_mismatch")
    bundles = await _load_bundles(
        session, target, owner_id=owner_id, tenant_id=tenant_id
    )
    htmls: list[str] = []
    drafts: list[dict[str, Any]] = []
    assets: list[AssetManifestV1] = []
    fonts: list[FontManifestV1] = []
    resolved_bytes: dict[str, bytes] = {}
    for bundle in bundles:
        html_bytes = await _load_artifact_bytes(
            session, owner_id, tenant_id, bundle.authored_html.digest
        )
        try:
            htmls.append(html_bytes.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise SemanticContractError("authored_semantic_rollback_html_invalid") from error
        drafts.append(_component_draft(bundle))
        asset_manifest = AssetManifestV1.model_validate_json(
            await _load_artifact_bytes(
                session, owner_id, tenant_id, bundle.asset_manifest.digest
            ),
            strict=False,
        )
        font_manifest = FontManifestV1.model_validate_json(
            await _load_artifact_bytes(
                session, owner_id, tenant_id, bundle.font_manifest.digest
            ),
            strict=False,
        )
        assets.append(asset_manifest)
        fonts.append(font_manifest)
        for entry in [
            *(item.artifact for item in asset_manifest.assets),
            *(item.artifact for item in font_manifest.fonts),
        ]:
            resolved_bytes[entry.digest] = await _load_artifact_bytes(
                session, owner_id, tenant_id, entry.digest
            )
    manifest = await publish_authored_semantic_bundle_graph(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        expected_presentation_revision=request.expected_presentation_revision,
        operation_id=request.operation_id,
        generation_attempt_id=request.generation_attempt_id,
        slides=slides,
        htmls=htmls,
        semantic_drafts=drafts,
        master_contract_digest=target.master_contract_digest,
        composition_digest=target.composition_digest,
        slide_asset_manifests=assets,
        slide_font_manifests=fonts,
        resolved_artifact_bytes=resolved_bytes,
        quality_policy_version=target.quality_policy_version,
        tenant_id=tenant_id,
    )
    for slide, html in zip(slides, htmls, strict=True):
        result = await session.execute(
            update(SlideModel)
            .where(
                SlideModel.id == slide.id,
                SlideModel.presentation == presentation_id,
                SlideModel.html_content == slide.html_content,
            )
            .values(html_content=html)
        )
        if result.rowcount != 1:
            raise SemanticContractError("authored_semantic_rollback_slide_cas_conflict")
    await _advance_presentation_revision(
        session, presentation, request.expected_presentation_revision
    )
    return _receipt(
        action="manifestRollback",
        presentation_id=presentation_id,
        operation_id=request.operation_id,
        generation_attempt_id=request.generation_attempt_id,
        previous_digest=request.expected_manifest_digest,
        target_digest=request.target_manifest_digest,
        active_digest=_required(manifest.canonical_digest),
        canonical_mode="bundleGraph",
        revision=manifest.presentation_revision,
        generation=request.expected_active_generation + 1,
        slides=len(slides),
        drafts=drafts,
    )


async def cutover_authored_emission_to_legacy(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    request: AuthoredLegacyCutoverRequestV1,
    tenant_id: str = "default",
) -> AuthoredEmissionLifecycleReceiptV1:
    presentation, pointer = await _load_and_validate_cas(
        session, presentation_id, owner_id, request, tenant_id=tenant_id
    )
    slides = await _ordered_slides(session, presentation_id, owner_id)
    next_revision = request.expected_presentation_revision + 1
    digest = canonical_digest(
        {
            "schemaVersion": "authored-legacy-html-cutover/v1",
            "presentationId": str(presentation_id),
            "presentationRevision": next_revision,
            "operationId": str(request.operation_id),
            "orderedSlides": [
                {
                    "slideId": str(slide.id),
                    "htmlDigest": canonical_digest(str(slide.html_content or "")),
                }
                for slide in slides
            ],
        }
    )
    result = await session.execute(
        update(AuthoredDeckEmissionActivePointer)
        .where(
            AuthoredDeckEmissionActivePointer.id == pointer.id,
            AuthoredDeckEmissionActivePointer.presentation_revision
            == request.expected_presentation_revision,
            AuthoredDeckEmissionActivePointer.canonical_digest
            == request.expected_manifest_digest,
            AuthoredDeckEmissionActivePointer.generation
            == request.expected_active_generation,
        )
        .values(
            canonical_mode="legacyHtml",
            manifest_id=None,
            presentation_revision=next_revision,
            canonical_digest=digest,
            generation=AuthoredDeckEmissionActivePointer.generation + 1,
            operation_id=request.operation_id,
        )
    )
    if result.rowcount != 1:
        raise SemanticContractError("authored_semantic_lifecycle_cas_conflict")
    await _advance_presentation_revision(
        session, presentation, request.expected_presentation_revision
    )
    return _receipt(
        action="legacyCutover",
        presentation_id=presentation_id,
        operation_id=request.operation_id,
        generation_attempt_id=None,
        previous_digest=request.expected_manifest_digest,
        target_digest=None,
        active_digest=digest,
        canonical_mode="legacyHtml",
        revision=next_revision,
        generation=request.expected_active_generation + 1,
        slides=len(slides),
        drafts=[],
    )


async def _load_and_validate_cas(
    session: AsyncSession,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    request: AuthoredOrderedRemakeRequestV1
    | AuthoredManifestRollbackRequestV1
    | AuthoredLegacyCutoverRequestV1,
    *,
    tenant_id: str,
) -> tuple[PresentationModel, AuthoredDeckEmissionActivePointer]:
    presentation = await session.scalar(
        select(PresentationModel).where(
            PresentationModel.id == presentation_id,
            PresentationModel.owner_id == owner_id,
        )
    )
    if presentation is None:
        raise SemanticContractError("authored_semantic_presentation_not_found")
    if presentation.revision != request.expected_presentation_revision:
        raise SemanticContractError("authored_semantic_lifecycle_stale_revision")
    pointer = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == owner_id,
            AuthoredDeckEmissionActivePointer.tenant_id == tenant_id,
            AuthoredDeckEmissionActivePointer.presentation_id == presentation_id,
        )
    )
    if pointer is None:
        raise SemanticContractError("authored_semantic_lifecycle_active_pointer_missing")
    if pointer.presentation_revision != request.expected_presentation_revision:
        raise SemanticContractError("authored_semantic_lifecycle_stale_revision")
    if pointer.generation != request.expected_active_generation:
        raise SemanticContractError("authored_semantic_lifecycle_stale_generation")
    if pointer.canonical_digest != request.expected_manifest_digest:
        raise SemanticContractError("authored_semantic_lifecycle_stale_manifest")
    return presentation, pointer


async def _reject_reused_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str,
    operation_id: uuid.UUID,
    generation_attempt_id: uuid.UUID,
) -> None:
    existing = await session.scalar(
        select(AuthoredDeckEmissionManifestRecord.id).where(
            AuthoredDeckEmissionManifestRecord.owner_id == owner_id,
            AuthoredDeckEmissionManifestRecord.tenant_id == tenant_id,
            AuthoredDeckEmissionManifestRecord.presentation_id == presentation_id,
            (
                (AuthoredDeckEmissionManifestRecord.operation_id == operation_id)
                | (
                    AuthoredDeckEmissionManifestRecord.generation_attempt_id
                    == generation_attempt_id
                )
            ),
        )
    )
    if existing is not None:
        raise SemanticContractError("authored_semantic_lifecycle_attempt_reused")


async def _ordered_slides(
    session: AsyncSession, presentation_id: uuid.UUID, owner_id: uuid.UUID
) -> list[SlideModel]:
    slides = list(
        await session.scalars(
            select(SlideModel)
            .where(
                SlideModel.presentation == presentation_id,
                (SlideModel.owner_id == owner_id) | (SlideModel.owner_id.is_(None)),
            )
            .order_by(SlideModel.index)
        )
    )
    if not slides or [slide.index for slide in slides] != list(range(len(slides))):
        raise SemanticContractError("authored_semantic_ordered_slide_set_invalid")
    return slides


async def _advance_presentation_revision(
    session: AsyncSession, presentation: PresentationModel, expected_revision: int
) -> None:
    result = await session.execute(
        update(PresentationModel)
        .where(
            PresentationModel.id == presentation.id,
            PresentationModel.revision == expected_revision,
        )
        .values(
            revision=PresentationModel.revision + 1,
            updated_at=get_current_utc_datetime(),
        )
    )
    if result.rowcount != 1:
        raise SemanticContractError("authored_semantic_lifecycle_cas_conflict")


async def _load_manifest(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str,
    digest: str,
) -> AuthoredDeckEmissionManifestV1:
    record = await session.scalar(
        select(AuthoredDeckEmissionManifestRecord).where(
            AuthoredDeckEmissionManifestRecord.owner_id == owner_id,
            AuthoredDeckEmissionManifestRecord.tenant_id == tenant_id,
            AuthoredDeckEmissionManifestRecord.presentation_id == presentation_id,
            AuthoredDeckEmissionManifestRecord.canonical_digest == digest,
        )
    )
    if record is None:
        raise SemanticContractError("authored_semantic_rollback_target_not_found")
    manifest = AuthoredDeckEmissionManifestV1.model_validate_json(
        json.dumps(record.payload, ensure_ascii=False), strict=False
    )
    if manifest.canonical_digest != digest:
        raise SemanticContractError("authored_semantic_rollback_target_digest_mismatch")
    return manifest


async def _load_bundles(
    session: AsyncSession,
    manifest: AuthoredDeckEmissionManifestV1,
    *,
    owner_id: uuid.UUID,
    tenant_id: str,
) -> list[AuthoredEmissionBundleV1]:
    records = list(
        await session.scalars(
            select(AuthoredEmissionBundleRecord).where(
                AuthoredEmissionBundleRecord.owner_id == owner_id,
                AuthoredEmissionBundleRecord.tenant_id == tenant_id,
                AuthoredEmissionBundleRecord.bundle_digest.in_(
                    [entry.bundle_digest for entry in manifest.ordered_slides]
                ),
            )
        )
    )
    by_digest = {record.bundle_digest: record for record in records}
    bundles: list[AuthoredEmissionBundleV1] = []
    for entry in manifest.ordered_slides:
        record = by_digest.get(entry.bundle_digest)
        if record is None or record.bundle_kind != "authored":
            raise SemanticContractError("authored_semantic_rollback_bundle_unresolvable")
        bundle = AuthoredEmissionBundleV1.model_validate_json(
            json.dumps(record.payload, ensure_ascii=False), strict=False
        )
        if bundle.bundle_digest != entry.bundle_digest or bundle.slide_id != entry.slide_id:
            raise SemanticContractError("authored_semantic_rollback_bundle_digest_mismatch")
        bundles.append(bundle)
    return bundles


async def _load_artifact_bytes(
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
        raise SemanticContractError("authored_semantic_rollback_artifact_unresolvable")
    content = bytes(artifact.content_bytes)
    if bytes_digest(content) != digest:
        raise SemanticContractError("authored_semantic_rollback_artifact_digest_mismatch")
    return content


def _component_draft(bundle: AuthoredEmissionBundleV1) -> dict[str, Any]:
    component = bundle.semantic_component
    if isinstance(component, SemanticUnavailableReceiptV1):
        return {
            "semanticUnavailableReceipt": {
                "htmlDigest": component.authored_html_digest,
                "failureCode": component.failure_code,
                "boundedAttemptCount": component.bounded_attempt_count,
            }
        }
    assert isinstance(component, AuthoredSemanticSourceV1)
    return {
        "semanticSource": {
            "htmlDigest": component.authored_html_digest,
            "elements": [
                item.model_dump(mode="json", by_alias=True)
                for item in component.elements
            ],
            "elementIdentityRegistry": component.identity_registry.model_dump(
                mode="json", by_alias=True
            ),
        }
    }


def _receipt(
    *,
    action: Literal["orderedRemake", "manifestRollback", "legacyCutover"],
    presentation_id: uuid.UUID,
    operation_id: uuid.UUID,
    generation_attempt_id: uuid.UUID | None,
    previous_digest: str,
    target_digest: str | None,
    active_digest: str,
    canonical_mode: Literal["legacyHtml", "bundleGraph"],
    revision: int,
    generation: int,
    slides: int,
    drafts: Sequence[Mapping[str, Any]],
) -> AuthoredEmissionLifecycleReceiptV1:
    degraded = sum("semanticUnavailableReceipt" in draft for draft in drafts)
    attempts = sum(
        int(draft.get("semanticUnavailableReceipt", {}).get("boundedAttemptCount", 1))
        if "semanticUnavailableReceipt" in draft
        else 1
        for draft in drafts
    )
    payload = {
        "schemaVersion": "authored-emission-lifecycle-receipt/v1",
        "action": action,
        "presentationId": str(presentation_id),
        "operationId": str(operation_id),
        "generationAttemptId": str(generation_attempt_id)
        if generation_attempt_id
        else None,
        "previousManifestDigest": previous_digest,
        "targetManifestDigest": target_digest,
        "activeManifestDigest": active_digest,
        "canonicalMode": canonical_mode,
        "presentationRevision": revision,
        "activeGeneration": generation,
        "orderedSlideCount": slides,
        "degradedFidelityOnlyCount": degraded,
        "boundedSemanticAttempts": attempts,
    }
    return AuthoredEmissionLifecycleReceiptV1(
        action=action,
        presentation_id=presentation_id,
        operation_id=operation_id,
        generation_attempt_id=generation_attempt_id,
        previous_manifest_digest=previous_digest,
        target_manifest_digest=target_digest,
        active_manifest_digest=active_digest,
        canonical_mode=canonical_mode,
        presentation_revision=revision,
        active_generation=generation,
        ordered_slide_count=slides,
        degraded_fidelity_only_count=degraded,
        bounded_semantic_attempts=attempts,
        receipt_digest=canonical_digest(payload),
    )


def _required(value: str | None) -> str:
    if value is None:
        raise SemanticContractError("authored_semantic_digest_missing")
    return value


__all__ = [
    "cutover_authored_emission_to_legacy",
    "remake_ordered_authored_emission",
    "rollback_authored_emission_manifest",
]
