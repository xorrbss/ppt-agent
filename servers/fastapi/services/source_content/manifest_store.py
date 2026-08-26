"""Owner-scoped immutable manifest persistence and receipt lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime
import hmac
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from models.sql.presentation import PresentationModel
from models.sql.source_content import (
    SourceContentCoverageReceipt,
    SourceContentGenerationBinding,
    SourceContentManifestAsset,
    SourceContentManifestRecord,
)
from services.references import ReferenceProviderError, admit_reference_snapshot
from services.source_content.contracts import (
    ReferenceSnapshotBinding,
    SourceContentAnalysisReceipt,
    SourceContentAnalysisSummary,
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.digests import digest_json
from services.source_content.errors import MANIFEST_INVALID, SECURITY_REJECTED, SourceContentError
from services.source_content.pptx import ExtractedSourceContent, extract_pptx_source_content
from services.workspace_asset_service import resolve_workspace_asset_download
from services.workspace_service import WorkspaceServiceError
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


def _scope(owner_id: uuid.UUID | None) -> str:
    return str(owner_id) if owner_id is not None else "anonymous"


def _manifest_query(
    *,
    owner_id: uuid.UUID | None,
    manifest_id: str,
) -> Select[tuple[SourceContentManifestRecord]]:
    owner_predicate = (
        SourceContentManifestRecord.owner_id == owner_id
        if owner_id is not None
        else SourceContentManifestRecord.owner_id.is_(None)
    )
    return select(SourceContentManifestRecord).where(
        SourceContentManifestRecord.manifest_id == manifest_id,
        SourceContentManifestRecord.owner_scope == _scope(owner_id),
        owner_predicate,
    )


def _manifest_from_record(
    row: SourceContentManifestRecord,
    *,
    owner_id: uuid.UUID | None,
) -> SourceContentManifest:
    try:
        manifest = SourceContentManifest.model_validate(
            {**row.manifest_json, "ownerId": _scope(owner_id)}
        )
    except (TypeError, ValueError) as error:
        raise SourceContentError(MANIFEST_INVALID, status_code=409) from error
    if not hmac.compare_digest(manifest.manifest_digest, row.manifest_digest):
        raise SourceContentError(MANIFEST_INVALID, status_code=409)
    public_payload = manifest.model_dump(mode="json", by_alias=True)
    digest_payload = dict(public_payload)
    digest_payload.pop("manifestDigest", None)
    if not hmac.compare_digest(digest_json(digest_payload), row.manifest_digest):
        raise SourceContentError(MANIFEST_INVALID, status_code=409)
    return manifest


def _analysis_receipt(
    manifest: SourceContentManifest,
    *,
    provider_id: str,
    source_revision: str,
) -> SourceContentAnalysisReceipt:
    blocks = [block for unit in manifest.units for block in unit.blocks]
    return SourceContentAnalysisReceipt(
        manifest_pin=SourceContentManifestPin(
            manifest_id=manifest.manifest_id,
            digest=manifest.manifest_digest,
        ),
        reference_snapshot_binding=ReferenceSnapshotBinding(
            reference_snapshot_id=manifest.reference_snapshot_id,
            provider_id=provider_id,
            source_revision=source_revision,
        ),
        summary=SourceContentAnalysisSummary(
            source_type="pptx",
            source_units=manifest.counts.source_units,
            text_blocks=manifest.counts.text_blocks,
            tables=sum(block.kind == "table" for block in blocks),
            charts=sum(block.kind == "chart" for block in blocks),
            unique_images=manifest.counts.unique_images,
            image_occurrences=manifest.counts.image_occurrences,
            raster_fallback_blocks=manifest.counts.raster_fallback_blocks,
        ),
        object_inventory=manifest.object_inventory,
    )


async def analyze_reference_snapshot(
    session: AsyncSession,
    *,
    reference_snapshot_id: uuid.UUID,
    owner_id: uuid.UUID | None = None,
) -> SourceContentAnalysisReceipt:
    """Revalidate one uploaded PPTX and append its deterministic immutable manifest."""

    effective_owner = get_current_owner_id() if owner_id is None else owner_id
    try:
        snapshot = await admit_reference_snapshot(
            session,
            snapshot_id=reference_snapshot_id,
        )
    except ReferenceProviderError as error:
        raise SourceContentError(
            MANIFEST_INVALID,
            status_code=error.status_code,
            detail={"reason": error.code},
        ) from error
    if snapshot.provider_id != "uploaded_document" or len(snapshot.asset_bindings) != 1:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "single_uploaded_document_required"},
        )
    binding = snapshot.asset_bindings[0]
    try:
        asset, source_path = await resolve_workspace_asset_download(
            session,
            asset_id=binding.asset_id,
        )
    except WorkspaceServiceError as error:
        raise SourceContentError(
            MANIFEST_INVALID,
            status_code=error.status_code,
            detail={"reason": error.code},
        ) from error
    if asset.sha256 != binding.sha256 or asset.version != binding.version:
        raise SourceContentError(
            MANIFEST_INVALID,
            status_code=409,
            detail={"reason": "reference_asset_binding_stale"},
        )
    extension = Path(asset.original_name).suffix.lower()
    if extension != ".pptx":
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={
                "reason": "source_type_capability_disabled",
                "sourceType": extension.lstrip(".") or "unknown",
                "capability": False,
            },
        )

    extracted = await asyncio.to_thread(
        extract_pptx_source_content,
        source_path,
        owner_id=_scope(effective_owner),
        reference_snapshot_id=str(snapshot.snapshot_id),
    )
    manifest = extracted.manifest
    existing = await session.scalar(
        _manifest_query(owner_id=effective_owner, manifest_id=manifest.manifest_id)
    )
    if existing is None:
        public_manifest = manifest.model_dump(mode="json", by_alias=True)
        row = SourceContentManifestRecord(
            manifest_id=manifest.manifest_id,
            owner_id=effective_owner,
            owner_scope=_scope(effective_owner),
            reference_snapshot_id=snapshot.snapshot_id,
            contract_version=manifest.contract_version,
            source_type=manifest.source_type,
            source_digest=manifest.source_digest,
            manifest_digest=manifest.manifest_digest,
            manifest_json=public_manifest,
            inventory_json=manifest.object_inventory.model_dump(mode="json", by_alias=True),
            counts_json=manifest.counts.model_dump(mode="json", by_alias=True),
        )
        session.add(row)
        for sealed in extracted.assets:
            session.add(
                SourceContentManifestAsset(
                    manifest_id=manifest.manifest_id,
                    asset_id=sealed.asset_id,
                    media_type=sealed.media_type,
                    content_digest=sealed.content_digest,
                    payload=sealed.payload,
                )
            )
        await session.commit()
    else:
        existing_manifest = _manifest_from_record(existing, owner_id=effective_owner)
        if not hmac.compare_digest(existing_manifest.manifest_digest, manifest.manifest_digest):
            raise SourceContentError(MANIFEST_INVALID, status_code=409)
    return _analysis_receipt(
        manifest,
        provider_id=snapshot.provider_id,
        source_revision=snapshot.source_revision,
    )


async def admit_source_content_manifest_pin(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    manifest_id: str,
    digest: str,
) -> SourceContentManifest:
    row = await session.scalar(_manifest_query(owner_id=owner_id, manifest_id=manifest_id))
    if row is None:
        raise SourceContentError(MANIFEST_INVALID, status_code=404)
    manifest = _manifest_from_record(row, owner_id=owner_id)
    if not hmac.compare_digest(manifest.manifest_digest, digest):
        raise SourceContentError(
            MANIFEST_INVALID,
            status_code=409,
            detail={"reason": "manifest_pin_digest_mismatch"},
        )
    try:
        await admit_reference_snapshot(
            session,
            snapshot_id=uuid.UUID(manifest.reference_snapshot_id),
        )
    except (ReferenceProviderError, ValueError) as error:
        raise SourceContentError(
            MANIFEST_INVALID,
            status_code=getattr(error, "status_code", 409),
            detail={"reason": getattr(error, "code", "manifest_snapshot_invalid")},
        ) from error
    return manifest


async def load_source_content_manifest_asset(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    manifest_id: str,
    asset_id: str,
) -> SourceContentManifestAsset:
    manifest = await session.scalar(_manifest_query(owner_id=owner_id, manifest_id=manifest_id))
    if manifest is None:
        raise SourceContentError(MANIFEST_INVALID, status_code=404)
    asset = await session.scalar(
        select(SourceContentManifestAsset).where(
            SourceContentManifestAsset.manifest_id == manifest_id,
            SourceContentManifestAsset.asset_id == asset_id,
        )
    )
    if asset is None:
        raise SourceContentError(MANIFEST_INVALID, status_code=404)
    return asset


async def load_source_content_manifest_assets(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    manifest_id: str,
) -> dict[str, dict[str, Any]]:
    """Load sealed binary payloads only after the owner-scoped manifest check."""

    manifest = await session.scalar(_manifest_query(owner_id=owner_id, manifest_id=manifest_id))
    if manifest is None:
        raise SourceContentError(MANIFEST_INVALID, status_code=404)
    rows = list(
        (
            await session.scalars(
                select(SourceContentManifestAsset)
                .where(SourceContentManifestAsset.manifest_id == manifest_id)
                .order_by(SourceContentManifestAsset.asset_id)
            )
        ).all()
    )
    return {
        row.asset_id: {
            "content": bytes(row.payload),
            "digest": row.content_digest,
            "media_type": row.media_type,
            "size_bytes": len(row.payload),
        }
        for row in rows
    }


async def get_source_content_manifest_status(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    manifest_id: str,
    digest: str,
) -> SourceContentAnalysisReceipt:
    manifest = await admit_source_content_manifest_pin(
        session,
        owner_id=owner_id,
        manifest_id=manifest_id,
        digest=digest,
    )
    snapshot = await admit_reference_snapshot(
        session,
        snapshot_id=uuid.UUID(manifest.reference_snapshot_id),
    )
    return _analysis_receipt(
        manifest,
        provider_id=snapshot.provider_id,
        source_revision=snapshot.source_revision,
    )


async def bind_source_content_generation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    manifest: SourceContentManifest,
    composition_pin: dict[str, Any],
    content_policy: dict[str, Any],
    generation_engine_version: str,
    presentation_id: uuid.UUID | None = None,
) -> SourceContentGenerationBinding:
    content_policy_digest = digest_json(content_policy)
    identity = digest_json(
        {
            "manifestDigest": manifest.manifest_digest,
            "compositionPin": composition_pin,
            "contentPolicyDigest": content_policy_digest,
            "generationEngineVersion": generation_engine_version,
        }
    )
    existing = await session.scalar(
        select(SourceContentGenerationBinding).where(
            SourceContentGenerationBinding.owner_scope == _scope(owner_id),
            SourceContentGenerationBinding.generation_identity_digest == identity,
        )
    )
    if existing is not None:
        return existing
    row = SourceContentGenerationBinding(
        owner_id=owner_id,
        owner_scope=_scope(owner_id),
        manifest_id=manifest.manifest_id,
        manifest_digest=manifest.manifest_digest,
        composition_pin=composition_pin,
        content_policy_digest=content_policy_digest,
        generation_identity_digest=identity,
        presentation_id=presentation_id,
    )
    session.add(row)
    await session.flush()
    return row


async def append_source_content_coverage_receipt(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID,
    presentation_revision: int,
    receipt: dict[str, Any],
) -> SourceContentCoverageReceipt:
    row = SourceContentCoverageReceipt(
        owner_id=owner_id,
        owner_scope=_scope(owner_id),
        contract_version="source-content-coverage-receipt/v1",
        presentation_id=presentation_id,
        presentation_revision=presentation_revision,
        manifest_digest=str(receipt["manifestDigest"]),
        plan_digest=str(receipt["planDigest"]),
        scene_digest=str(receipt["sceneDigest"]),
        candidate_artifact_digest=str(receipt["candidateArtifactDigest"]),
        export_readback_digest=str(receipt["exportReadbackDigest"]),
        receipt_json=receipt,
        edit_state=str(receipt["editState"]),
        status=str(receipt["status"]),
    )
    session.add(row)
    await session.flush()
    return row


def _modified_theme(theme: dict[str, Any] | None, *, new_revision: int) -> dict[str, Any]:
    updated = dict(theme or {})
    source_content = dict(updated.get("sourceContent") or {})
    source_content.update(
        {
            "sourceCoverageStatus": "modified_by_user",
            "modifiedAtRevision": new_revision,
        }
    )
    updated["sourceContent"] = source_content
    return updated


def mark_coverage_receipts_stale_sync(
    session: Session,
    *,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID,
    prior_revision: int,
    new_revision: int,
) -> int:
    """Stale prior receipts and update provenance in the caller's transaction."""

    now = get_current_utc_datetime()
    result = session.execute(
        update(SourceContentCoverageReceipt)
        .where(
            SourceContentCoverageReceipt.owner_scope == _scope(owner_id),
            SourceContentCoverageReceipt.presentation_id == presentation_id,
            SourceContentCoverageReceipt.presentation_revision <= prior_revision,
            SourceContentCoverageReceipt.edit_state == "pristine",
        )
        .values(edit_state="stale", superseded_at=now, updated_at=now)
    )
    presentation = session.get(PresentationModel, presentation_id)
    if presentation is not None and presentation.owner_id == owner_id:
        presentation.theme = _modified_theme(presentation.theme, new_revision=new_revision)
        presentation.updated_at = now
        session.add(presentation)
    return int(result.rowcount or 0)


async def mark_coverage_receipts_stale(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID,
    prior_revision: int,
    new_revision: int,
) -> int:
    now = get_current_utc_datetime()
    result = await session.execute(
        update(SourceContentCoverageReceipt)
        .where(
            SourceContentCoverageReceipt.owner_scope == _scope(owner_id),
            SourceContentCoverageReceipt.presentation_id == presentation_id,
            SourceContentCoverageReceipt.presentation_revision <= prior_revision,
            SourceContentCoverageReceipt.edit_state == "pristine",
        )
        .values(edit_state="stale", superseded_at=now, updated_at=now)
    )
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is not None and presentation.owner_id == owner_id:
        presentation.theme = _modified_theme(presentation.theme, new_revision=new_revision)
        presentation.updated_at = now
        session.add(presentation)
    await session.flush()
    return int(result.rowcount or 0)
