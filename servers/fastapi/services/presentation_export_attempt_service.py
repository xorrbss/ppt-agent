"""Persistence helpers for one durable export-attempt identity."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
    PresentationExportAttempt,
    validate_export_attempt_provenance,
)
from models.sql.workspace import WorkspaceOperation
from services.presentation_export_capabilities import (
    PresentationExportCapabilityAnalysis,
    PresentationExportContractError,
)
from services.presentation_export_source_hashes import (
    _canonical_json as _canonical_json,
    sha256_canonical_json as sha256_canonical_json,
    build_export_source_snapshot_sha256 as build_export_source_snapshot_sha256,
    build_export_html_source_sha256 as build_export_html_source_sha256,
    build_export_composition_sha256 as build_export_composition_sha256,
    build_export_asset_manifest_sha256 as build_export_asset_manifest_sha256,
    require_export_asset_digests as require_export_asset_digests,
    build_sync_export_hashes as build_sync_export_hashes,
)


def initial_export_attempt_provenance(
    analysis: PresentationExportCapabilityAnalysis,
    *,
    source_revision: int,
    source_snapshot_sha256: str,
    html_source_sha256: str | None = None,
    composition_sha256: str | None = None,
    asset_manifest_sha256: str | None = None,
    renderer_source_sha256: str | None = None,
) -> dict[str, Any]:
    return validate_export_attempt_provenance(
        {
            "schema_version": 1,
            "adapter_chain": (analysis.selected_adapter,),
            "capability_evidence": {
                **analysis.capability_evidence,
                "attempted_adapters": [],
                "selected_adapter": analysis.selected_adapter,
            },
            "fallback_reason": None,
            "asset_digests": analysis.asset_digests,
            "compiler_runtime": {
                "compiler": "presentation-export-router",
                "compiler_version": "1",
                "runtime": "pending",
                "runtime_version": "1",
            },
            "freshness": {
                "source_revision": source_revision,
                "source_snapshot_sha256": source_snapshot_sha256,
                "html_source_sha256": html_source_sha256,
                "composition_sha256": composition_sha256,
                "asset_manifest_sha256": asset_manifest_sha256,
                "renderer_source_sha256": renderer_source_sha256,
                "package_sha256": None,
                "asset_digests_match": True,
                "package_digest_match": True,
            },
        }
    )


async def begin_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    owner_scope: str,
    workspace_operation_id: uuid.UUID | None,
    export_format: Literal["pptx", "pdf"],
    source_revision: int,
    source_snapshot_sha256: str,
    request_hash: str,
    idempotency_key_hash: str,
    requested_adapter: str,
    provenance: dict[str, Any],
) -> PresentationExportAttempt:
    """Create or resume the single identity before external rendering starts."""

    normalized_provenance = validate_export_attempt_provenance(provenance)
    identity_predicates = (
        PresentationExportAttempt.owner_scope == owner_scope,
        PresentationExportAttempt.presentation_id == presentation_id,
        PresentationExportAttempt.format == export_format,
        PresentationExportAttempt.idempotency_key_hash == idempotency_key_hash,
    )
    existing = (
        await session.execute(select(PresentationExportAttempt).where(*identity_predicates))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        attempt = PresentationExportAttempt(
            owner_id=owner_id,
            owner_scope=owner_scope,
            presentation_id=presentation_id,
            workspace_operation_id=workspace_operation_id,
            format=export_format,
            state="running",
            source_revision=source_revision,
            source_snapshot_sha256=source_snapshot_sha256,
            request_hash=request_hash,
            idempotency_key_hash=idempotency_key_hash,
            requested_adapter=requested_adapter,
            selected_adapter=None,
            provenance_schema_version=1,
            provenance=normalized_provenance,
            started_at=now,
            completed_at=None,
        )
        session.add(attempt)
        try:
            await session.commit()
        except IntegrityError:
            # Another worker may win the unique identity after our SELECT.
            # Roll back the failed INSERT, then reuse only the exact immutable
            # identity it committed; a conflicting payload remains fail-closed.
            await session.rollback()
            existing = (
                await session.execute(select(PresentationExportAttempt).where(*identity_predicates))
            ).scalar_one_or_none()
            if existing is None:
                raise
        else:
            await session.refresh(attempt)
            return attempt

    assert existing is not None
    immutable_matches = (
        existing.owner_id == owner_id
        and existing.workspace_operation_id == workspace_operation_id
        and existing.source_revision == source_revision
        and existing.source_snapshot_sha256 == source_snapshot_sha256
        and existing.request_hash == request_hash
        and existing.requested_adapter == requested_adapter
    )
    if not immutable_matches:
        raise PresentationExportContractError("presentation_export_idempotency_conflict")
    if existing.state == "succeeded":
        raise PresentationExportContractError("presentation_export_attempt_already_succeeded")
    existing.state = "running"
    existing.selected_adapter = None
    existing.artifact_sha256 = None
    existing.artifact_size_bytes = None
    existing.failure_code = None
    existing.failure_path = None
    existing.provenance = normalized_provenance
    existing.started_at = existing.started_at or now
    existing.completed_at = None
    existing.updated_at = now
    session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


async def terminalize_export_attempt_succeeded(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    presentation_id: uuid.UUID,
    expected_owner_id: uuid.UUID | None,
    expected_owner_scope: str,
    expected_workspace_operation_id: uuid.UUID | None,
    source_revision: int,
    source_snapshot_sha256: str,
    selected_adapter: str,
    artifact_sha256: str,
    artifact_size_bytes: int,
    provenance: dict[str, Any],
) -> None:
    """Stage success in the caller's publication transaction; never commit here."""

    normalized_provenance = validate_export_attempt_provenance(provenance)
    await require_export_source_revision(
        session,
        presentation_id=presentation_id,
        expected_revision=source_revision,
    )
    await require_export_asset_digests(
        session,
        expected_asset_digests=normalized_provenance["asset_digests"],
    )
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(PresentationExportAttempt)
        .where(
            PresentationExportAttempt.id == attempt_id,
            PresentationExportAttempt.presentation_id == presentation_id,
            PresentationExportAttempt.owner_id == expected_owner_id,
            PresentationExportAttempt.owner_scope == expected_owner_scope,
            PresentationExportAttempt.workspace_operation_id == expected_workspace_operation_id,
            PresentationExportAttempt.state == "running",
            PresentationExportAttempt.source_revision == source_revision,
            PresentationExportAttempt.source_snapshot_sha256 == source_snapshot_sha256,
        )
        .values(
            state="succeeded",
            selected_adapter=selected_adapter,
            artifact_sha256=artifact_sha256,
            artifact_size_bytes=artifact_size_bytes,
            failure_code=None,
            failure_path=None,
            provenance_schema_version=1,
            provenance=normalized_provenance,
            completed_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        raise PresentationExportContractError("presentation_export_attempt_terminal_conflict")


async def terminalize_export_attempt_failed(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    expected_owner_id: uuid.UUID | None,
    expected_owner_scope: str,
    expected_workspace_operation_id: uuid.UUID | None,
    failure_code: str,
    failure_path: str | None,
    provenance: dict[str, Any] | None = None,
    selected_adapter: str | None = None,
) -> None:
    """Persist a content-free terminal failure outside a rolled-back publication."""

    if not failure_code or len(failure_code) > 128:
        failure_code = "presentation_export_failed"
    safe_path = failure_path[:512] if failure_path else None
    values: dict[str, Any] = {
        "state": "failed",
        "selected_adapter": selected_adapter,
        "artifact_sha256": None,
        "artifact_size_bytes": None,
        "failure_code": failure_code,
        "failure_path": safe_path,
        "completed_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    if provenance is not None:
        values["provenance"] = validate_export_attempt_provenance(provenance)
    result = await session.execute(
        update(PresentationExportAttempt)
        .where(
            PresentationExportAttempt.id == attempt_id,
            PresentationExportAttempt.owner_id == expected_owner_id,
            PresentationExportAttempt.owner_scope == expected_owner_scope,
            PresentationExportAttempt.workspace_operation_id == expected_workspace_operation_id,
            PresentationExportAttempt.state.in_(("pending", "running", "failed")),
        )
        .values(**values)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationExportContractError("presentation_export_attempt_terminal_conflict")
    await session.commit()


async def terminalize_export_attempt_failed_if_current_workspace_claim(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    expected_owner_id: uuid.UUID | None,
    expected_owner_scope: str,
    expected_workspace_operation_id: uuid.UUID,
    expected_attempt_token: str,
    expected_fencing_token: int,
    failure_code: str,
    failure_path: str | None,
    selected_adapter: str | None = None,
) -> bool:
    """Fail a running export attempt only while its worker claim remains current."""

    operation = await session.scalar(
        select(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == expected_workspace_operation_id,
            WorkspaceOperation.owner_id == expected_owner_id,
            WorkspaceOperation.owner_scope == expected_owner_scope,
        )
        .with_for_update()
    )
    if (
        operation is None
        or operation.state != "running"
        or operation.attempt_token != expected_attempt_token
        or operation.fencing_token != expected_fencing_token
    ):
        await session.rollback()
        return False

    if not isinstance(failure_code, str) or not failure_code or len(failure_code) > 128:
        failure_code = "presentation_export_failed"
    safe_path = failure_path[:512] if isinstance(failure_path, str) else None
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(PresentationExportAttempt)
        .where(
            PresentationExportAttempt.id == attempt_id,
            PresentationExportAttempt.owner_id == expected_owner_id,
            PresentationExportAttempt.owner_scope == expected_owner_scope,
            PresentationExportAttempt.workspace_operation_id == expected_workspace_operation_id,
            PresentationExportAttempt.state == "running",
        )
        .values(
            state="failed",
            selected_adapter=selected_adapter,
            artifact_sha256=None,
            artifact_size_bytes=None,
            failure_code=failure_code,
            failure_path=safe_path,
            completed_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True


async def require_export_source_revision(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
) -> None:
    current = await session.scalar(select(PresentationModel.revision).where(PresentationModel.id == presentation_id))
    if current != expected_revision:
        raise PresentationExportContractError("stale_presentation_revision")


__all__ = [
    "begin_export_attempt",
    "build_export_asset_manifest_sha256",
    "build_export_composition_sha256",
    "build_export_html_source_sha256",
    "build_export_source_snapshot_sha256",
    "build_sync_export_hashes",
    "initial_export_attempt_provenance",
    "require_export_asset_digests",
    "require_export_source_revision",
    "sha256_canonical_json",
    "terminalize_export_attempt_failed",
    "terminalize_export_attempt_failed_if_current_workspace_claim",
    "terminalize_export_attempt_succeeded",
]
