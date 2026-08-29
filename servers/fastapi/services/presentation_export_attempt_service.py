"""Persistence helpers for one durable export-attempt identity."""

from __future__ import annotations
import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
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
from models.sql.slide import SlideModel
from services.presentation_export_capabilities import (
    PresentationExportCapabilityAnalysis,
    PresentationExportContractError,
    PresentationExportFreshnessError,
)
from services.workspace_asset_service import resolve_workspace_asset_download
from services.workspace_service import WorkspaceServiceError


def _canonical_json(value: Any) -> bytes:
    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise PresentationExportContractError(
            "presentation_export_snapshot_invalid"
        ) from error
    return serialized.encode("utf-8")


def sha256_canonical_json(value: Any) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def build_export_source_snapshot_sha256(
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
    *,
    durable_presentation_id: uuid.UUID | None = None,
) -> str:
    """Hash semantic persisted inputs without transient staging/slide UUIDs."""

    slide_payloads = [
        {
            "index": slide.index,
            "layout_group": slide.layout_group,
            "layout": slide.layout,
            "content": slide.content,
            "ui": slide.ui,
            "html_content": slide.html_content,
            "speaker_note": slide.speaker_note,
            "properties": slide.properties,
        }
        for slide in slides
    ]
    slide_payloads.sort(
        key=lambda item: (item["index"], sha256_canonical_json(item))
    )

    return sha256_canonical_json(
        {
            "schema_version": 2,
            "presentation": {
                "id": str(durable_presentation_id or presentation.id),
                "revision": presentation.revision,
                "lifecycle_status": presentation.lifecycle_status,
                "version": presentation.version,
                "mode": presentation.mode,
                "content": presentation.content,
                "title": presentation.title,
                "language": presentation.language,
                "reference_snapshot_ids": presentation.reference_snapshot_ids,
                "outlines": presentation.outlines,
                "layout": presentation.layout,
                "structure": presentation.structure,
                "instructions": presentation.instructions,
                "tone": presentation.tone,
                "verbosity": presentation.verbosity,
                "include_table_of_contents": presentation.include_table_of_contents,
                "include_title_slide": presentation.include_title_slide,
                "theme": presentation.theme,
                "fonts": presentation.fonts,
                "generation_mode": presentation.generation_mode,
                "profile_driver_id": presentation.profile_driver_id,
                "profile_schema_version": presentation.profile_schema_version,
            },
            "slides": slide_payloads,
        }
    )


def build_export_html_source_sha256(slides: Sequence[SlideModel]) -> str:
    return sha256_canonical_json(
        [
            {"index": slide.index, "html_content": slide.html_content}
            for slide in sorted(slides, key=lambda item: (item.index, str(item.id)))
        ]
    )


def build_export_composition_sha256(
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
) -> str:
    """Seal exactly the authored paint/layout inputs shared by preview/export."""

    return sha256_canonical_json(
        {
            "schema_version": 1,
            "presentation": {
                "mode": presentation.mode,
                "theme": presentation.theme,
                "fonts": presentation.fonts,
                "layout": presentation.layout,
                "structure": presentation.structure,
                "profile_driver_id": presentation.profile_driver_id,
                "profile_schema_version": presentation.profile_schema_version,
            },
            "slides": [
                {
                    "index": slide.index,
                    "layout_group": slide.layout_group,
                    "layout": slide.layout,
                    "html_content": slide.html_content,
                    "ui": slide.ui,
                    "properties": slide.properties,
                }
                for slide in sorted(
                    slides, key=lambda item: (item.index, str(item.id))
                )
            ],
        }
    )


def build_export_asset_manifest_sha256(asset_digests: dict[str, str]) -> str:
    return sha256_canonical_json(
        {
            "schema_version": 1,
            "assets": dict(sorted(asset_digests.items())),
        }
    )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def require_export_asset_digests(
    session: AsyncSession,
    *,
    expected_asset_digests: dict[str, str],
) -> None:
    """Revalidate each canonical immutable workspace asset against real bytes."""

    for raw_asset_id, expected_digest in sorted(expected_asset_digests.items()):
        if raw_asset_id.startswith("asset:"):
            # Canonical Scene assets have no workspace UUID row. Their strict
            # manifest entry already binds ID, digest, media type, byte size,
            # and either a digest-verified data URI or the exact asset-store
            # reference. The source snapshot seals that storage reference.
            continue
        try:
            asset_id = uuid.UUID(raw_asset_id)
            asset, private_path = await resolve_workspace_asset_download(
                session,
                asset_id=asset_id,
            )
        except (ValueError, WorkspaceServiceError) as error:
            raise PresentationExportFreshnessError(
                "presentation_export_asset_unavailable",
                path=raw_asset_id,
            ) from error
        stored_digest = (asset.sha256 or "").lower()
        if not stored_digest or not hmac.compare_digest(
            stored_digest,
            expected_digest,
        ):
            raise PresentationExportFreshnessError(
                "presentation_export_asset_digest_mismatch",
                path=raw_asset_id,
            )
        try:
            stat_size = private_path.stat().st_size
            actual_digest = await asyncio.to_thread(_sha256_file, private_path)
        except OSError as error:
            raise PresentationExportFreshnessError(
                "presentation_export_asset_unavailable",
                path=raw_asset_id,
            ) from error
        if stat_size != asset.size_bytes or not hmac.compare_digest(
            actual_digest,
            expected_digest,
        ):
            raise PresentationExportFreshnessError(
                "presentation_export_asset_digest_mismatch",
                path=raw_asset_id,
            )


def build_sync_export_hashes(
    *,
    presentation_id: uuid.UUID,
    export_format: Literal["pptx", "pdf"],
    source_revision: int,
    source_snapshot_sha256: str,
    identity: str,
) -> tuple[str, str]:
    request_hash = sha256_canonical_json(
        {
            "presentation_id": str(presentation_id),
            "format": export_format,
            "source_revision": source_revision,
            "source_snapshot_sha256": source_snapshot_sha256,
        }
    )
    idempotency_key_hash = sha256(
        f"presentation-export:{identity}".encode("utf-8")
    ).hexdigest()
    return request_hash, idempotency_key_hash


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
        await session.execute(
            select(PresentationExportAttempt).where(*identity_predicates)
        )
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
                await session.execute(
                    select(PresentationExportAttempt).where(*identity_predicates)
                )
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
        raise PresentationExportContractError(
            "presentation_export_idempotency_conflict"
        )
    if existing.state == "succeeded":
        raise PresentationExportContractError(
            "presentation_export_attempt_already_succeeded"
        )
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
            PresentationExportAttempt.workspace_operation_id
            == expected_workspace_operation_id,
            PresentationExportAttempt.state == "running",
            PresentationExportAttempt.source_revision == source_revision,
            PresentationExportAttempt.source_snapshot_sha256
            == source_snapshot_sha256,
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
        raise PresentationExportContractError(
            "presentation_export_attempt_terminal_conflict"
        )


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
            PresentationExportAttempt.workspace_operation_id
            == expected_workspace_operation_id,
            PresentationExportAttempt.state.in_(("pending", "running", "failed")),
        )
        .values(**values)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationExportContractError(
            "presentation_export_attempt_terminal_conflict"
        )
    await session.commit()


async def require_export_source_revision(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
) -> None:
    current = await session.scalar(
        select(PresentationModel.revision).where(
            PresentationModel.id == presentation_id
        )
    )
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
    "terminalize_export_attempt_succeeded",
]
