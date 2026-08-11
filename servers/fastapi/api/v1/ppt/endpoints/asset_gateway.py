"""Path-free HTTP facade for canonical assets and reference snapshots."""

# FastAPI dependency declarations intentionally call Depends/File in defaults.
# ruff: noqa: B008

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_async_session
from services.assets import backfill_legacy_image_assets
from services.compatibility_readiness import (
    backfill_presentation_profiles,
    compatibility_readiness,
)
from services.references import ReferenceProviderError, create_reference_snapshot
from services.workspace_asset_service import create_workspace_asset
from services.workspace_service import WorkspaceServiceError
from utils.asset_directory_utils import get_images_directory
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_is_admin


ASSET_GATEWAY_ROUTER = APIRouter(tags=["Asset Gateway"])


class CanonicalAssetReceipt(BaseModel):
    asset_id: uuid.UUID
    kind: Literal["document"] = "document"
    state: str
    media_type: str | None
    size_bytes: int
    sha256: str
    version: int
    conversation_id: uuid.UUID | None
    expires_at: datetime | None


class ReferenceSnapshotCreateRequest(BaseModel):
    provider_id: Literal["community", "uploaded_document", "template"]
    source: dict[str, Any] = Field(
        description="Provider-specific opaque identifiers; filesystem paths are forbidden."
    )
    conversation_id: uuid.UUID | None = None


class ReferenceSnapshotReceipt(BaseModel):
    snapshot_id: uuid.UUID
    provider_id: str
    source_revision: str
    source_fingerprint: str
    content_sha256: str
    asset_ids: list[uuid.UUID]
    conversation_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime | None


class CompatibilityBackfillReceipt(BaseModel):
    image_rows_scanned: int
    image_rows_backfilled: int
    image_rows_failed: int
    profile_rows_scanned: int
    profile_rows_backfilled: int
    profile_rows_failed: int
    readiness: dict[str, Any]


@ASSET_GATEWAY_ROUTER.post(
    "/assets/documents",
    response_model=CanonicalAssetReceipt,
)
async def post_canonical_document_asset(
    file: UploadFile = File(...),
    conversation_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> CanonicalAssetReceipt:
    """Admit, scan, and process a document without returning a raw path."""

    try:
        asset = await create_workspace_asset(
            session,
            upload=file,
            conversation_id=conversation_id,
            process_immediately=True,
        )
    except WorkspaceServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    if asset.state != "ready" or not asset.sha256:
        raise HTTPException(status_code=409, detail="asset_not_ready")
    return CanonicalAssetReceipt(
        asset_id=asset.id,
        state=asset.state,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        version=asset.version,
        conversation_id=asset.conversation_id,
        expires_at=asset.expires_at,
    )


@ASSET_GATEWAY_ROUTER.post(
    "/references/snapshots",
    response_model=ReferenceSnapshotReceipt,
)
async def post_reference_snapshot(
    request: ReferenceSnapshotCreateRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ReferenceSnapshotReceipt:
    """Capture mutable provider input once for deterministic generation retries."""

    try:
        snapshot = await create_reference_snapshot(
            session,
            provider_id=request.provider_id,
            request=request.source,
            conversation_id=request.conversation_id,
        )
    except ReferenceProviderError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    return ReferenceSnapshotReceipt(
        snapshot_id=snapshot.snapshot_id,
        provider_id=snapshot.provider_id,
        source_revision=snapshot.source_revision,
        source_fingerprint=snapshot.source_fingerprint,
        content_sha256=snapshot.content_sha256,
        asset_ids=[binding.asset_id for binding in snapshot.asset_bindings],
        conversation_id=snapshot.conversation_id,
        created_at=snapshot.created_at,
        expires_at=snapshot.expires_at,
    )


@ASSET_GATEWAY_ROUTER.get("/compatibility/readiness")
async def get_compatibility_readiness(
    global_scope: bool = Query(default=False, alias="global"),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Return owner evidence, or an explicit administrator-wide aggregate."""

    if global_scope and not get_current_owner_is_admin():
        raise HTTPException(status_code=403, detail="admin_aggregate_required")
    return (
        await compatibility_readiness(
            session,
            aggregate_all_owners=global_scope,
        )
    ).as_dict()


@ASSET_GATEWAY_ROUTER.post(
    "/compatibility/backfill",
    response_model=CompatibilityBackfillReceipt,
)
async def post_compatibility_backfill(
    include_admin_legacy_root: bool = Query(default=False),
    global_readiness: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
) -> CompatibilityBackfillReceipt:
    """Run one bounded, restartable compatibility backfill batch."""

    if (include_admin_legacy_root or global_readiness) and not get_current_owner_is_admin():
        raise HTTPException(status_code=403, detail="admin_migration_required")
    admin_legacy_image_root = (
        Path(get_app_data_directory_env()) / "images"
        if include_admin_legacy_root
        else None
    )
    images = await backfill_legacy_image_assets(
        session,
        image_root=get_images_directory(),
        admin_legacy_image_root=admin_legacy_image_root,
    )
    profiles = await backfill_presentation_profiles(session)
    readiness = await compatibility_readiness(
        session,
        aggregate_all_owners=global_readiness,
    )
    return CompatibilityBackfillReceipt(
        image_rows_scanned=images.scanned,
        image_rows_backfilled=images.backfilled,
        image_rows_failed=images.failed,
        profile_rows_scanned=profiles.scanned,
        profile_rows_backfilled=profiles.backfilled,
        profile_rows_failed=profiles.failed,
        readiness=readiness.as_dict(),
    )
