"""Path-free HTTP facade for canonical assets and reference snapshots."""

# FastAPI dependency declarations intentionally call Depends/File in defaults.
# ruff: noqa: B008

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Any, Literal
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.asset_gateway_idempotency_receipt import (
    AssetGatewayIdempotencyReceipt,
)
from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.workspace import WorkspaceAsset
from services.database import get_async_session
from services.asset_gateway_idempotency_service import (
    ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE,
    cleanup_expired_asset_gateway_idempotency_receipts,
)
from services.assets import backfill_legacy_image_assets
from services.compatibility_readiness import (
    backfill_presentation_profiles,
    compatibility_readiness,
)
from services.references import ReferenceProviderError, create_reference_snapshot
from services.workspace_asset_service import create_workspace_asset, get_workspace_asset
from services.workspace_service import WorkspaceServiceError
from utils.datetime_utils import get_current_utc_datetime
from utils.asset_directory_utils import get_images_directory
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id, get_current_owner_is_admin


ASSET_GATEWAY_ROUTER = APIRouter(tags=["Asset Gateway"])
_IDEMPOTENCY_RECEIPT_TTL = timedelta(hours=24)
_IDEMPOTENCY_CLEANUP_BATCH_SIZE = ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE
_DOCUMENT_UPLOAD_OPERATION = "document.upload"
_SNAPSHOT_CREATE_OPERATION = "reference.snapshot.create"


def _owner_scope() -> str:
    owner_id = get_current_owner_id()
    return str(owner_id) if owner_id is not None else "anonymous"


def _idempotency_scope(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise HTTPException(status_code=400, detail="invalid_idempotency_key")
    return _owner_scope(), sha256(normalized.encode("utf-8")).hexdigest()


async def _cleanup_expired_idempotency_receipts(
    session: AsyncSession,
    *,
    owner_scope: str,
    now: datetime | None = None,
    limit: int = _IDEMPOTENCY_CLEANUP_BATCH_SIZE,
) -> int:
    """Delete a bounded owner-scoped batch so stale reservations cannot grow forever."""

    return await cleanup_expired_asset_gateway_idempotency_receipts(
        session,
        owner_scope=owner_scope,
        now=now,
        limit=limit,
    )


async def _find_idempotency_receipt(
    session: AsyncSession,
    *,
    owner_scope: str,
    operation: str,
    key_hash: str,
) -> AssetGatewayIdempotencyReceipt | None:
    return await session.scalar(
        select(AssetGatewayIdempotencyReceipt).where(
            AssetGatewayIdempotencyReceipt.owner_scope == owner_scope,
            AssetGatewayIdempotencyReceipt.operation == operation,
            AssetGatewayIdempotencyReceipt.key_hash == key_hash,
        )
    )


def _replay_idempotency_receipt(
    receipt: AssetGatewayIdempotencyReceipt,
    *,
    request_sha256: str,
) -> dict[str, Any]:
    if receipt.request_sha256 != request_sha256:
        raise HTTPException(
            status_code=409, detail="idempotency_key_payload_conflict"
        )
    if receipt.state != "complete" or not isinstance(
        receipt.response_payload, dict
    ):
        raise HTTPException(status_code=409, detail="idempotency_record_incomplete")
    return receipt.response_payload


async def _reserve_idempotency_receipt(
    session: AsyncSession,
    *,
    owner_scope: str,
    operation: str,
    key_hash: str,
    request_sha256: str,
) -> tuple[AssetGatewayIdempotencyReceipt | None, dict[str, Any] | None]:
    """Durably win a key or replay the row that won a concurrent insert."""

    now = get_current_utc_datetime()
    await _cleanup_expired_idempotency_receipts(
        session,
        owner_scope=owner_scope,
        now=now,
    )
    existing = await _find_idempotency_receipt(
        session,
        owner_scope=owner_scope,
        operation=operation,
        key_hash=key_hash,
    )
    if existing is not None:
        return None, _replay_idempotency_receipt(
            existing,
            request_sha256=request_sha256,
        )

    receipt = AssetGatewayIdempotencyReceipt(
        owner_id=get_current_owner_id(),
        owner_scope=owner_scope,
        operation=operation,
        key_hash=key_hash,
        request_sha256=request_sha256,
        expires_at=now + _IDEMPOTENCY_RECEIPT_TTL,
    )
    session.add(receipt)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await _find_idempotency_receipt(
            session,
            owner_scope=owner_scope,
            operation=operation,
            key_hash=key_hash,
        )
        if winner is None:
            # Only the unique-key race is recoverable. Preserve unrelated
            # integrity failures as server faults instead of misreporting them
            # as a client idempotency conflict.
            raise
        return None, _replay_idempotency_receipt(
            winner,
            request_sha256=request_sha256,
        )
    return receipt, None


async def _complete_idempotency_receipt(
    session: AsyncSession,
    *,
    receipt_id: uuid.UUID,
    response_payload: dict[str, Any],
    asset_id: uuid.UUID | None = None,
    snapshot_id: uuid.UUID | None = None,
) -> None:
    receipt = await session.get(AssetGatewayIdempotencyReceipt, receipt_id)
    if receipt is None or receipt.state != "pending":
        raise HTTPException(status_code=409, detail="idempotency_record_incomplete")
    receipt.state = "complete"
    receipt.response_payload = response_payload
    receipt.asset_id = asset_id
    receipt.snapshot_id = snapshot_id
    receipt.updated_at = get_current_utc_datetime()
    session.add(receipt)
    await session.commit()


async def _delete_resource_idempotency_receipts(
    session: AsyncSession,
    *,
    asset_ids: list[uuid.UUID] | None = None,
    snapshot_id: uuid.UUID | None = None,
) -> None:
    predicates = []
    if asset_ids:
        predicates.append(AssetGatewayIdempotencyReceipt.asset_id.in_(asset_ids))
    if snapshot_id is not None:
        predicates.append(AssetGatewayIdempotencyReceipt.snapshot_id == snapshot_id)
    if not predicates:
        return
    await session.execute(
        delete(AssetGatewayIdempotencyReceipt).where(
            AssetGatewayIdempotencyReceipt.owner_scope == _owner_scope(),
            or_(*predicates),
        )
    )


async def _upload_payload_sha256(
    file: UploadFile,
    *,
    conversation_id: uuid.UUID | None,
) -> str:
    content_digest = sha256()
    size_bytes = 0
    try:
        while chunk := await file.read(1024 * 1024):
            content_digest.update(chunk)
            size_bytes += len(chunk)
    finally:
        await file.seek(0)
    payload = json.dumps(
        {
            "conversation_id": str(conversation_id) if conversation_id else None,
            "content_sha256": content_digest.hexdigest(),
            "file_name": file.filename or "",
            "media_type": file.content_type or "",
            "size_bytes": size_bytes,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_payload_sha256(request: "ReferenceSnapshotCreateRequest") -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


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


class CanonicalCleanupReceipt(BaseModel):
    state: Literal["expired"] = "expired"
    asset_ids: list[uuid.UUID]
    snapshot_id: uuid.UUID | None = None


@ASSET_GATEWAY_ROUTER.post(
    "/assets/documents",
    response_model=CanonicalAssetReceipt,
)
async def post_canonical_document_asset(
    file: UploadFile = File(...),
    conversation_id: uuid.UUID | None = None,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    session: AsyncSession = Depends(get_async_session),
) -> CanonicalAssetReceipt:
    """Admit, scan, and process a document without returning a raw path."""

    scope = _idempotency_scope(idempotency_key)
    request_sha256 = (
        await _upload_payload_sha256(file, conversation_id=conversation_id)
        if scope is not None
        else None
    )

    async def create() -> CanonicalAssetReceipt:
        try:
            asset = await create_workspace_asset(
                session,
                upload=file,
                conversation_id=conversation_id,
                process_immediately=True,
            )
        except WorkspaceServiceError as error:
            raise HTTPException(
                status_code=error.status_code, detail=error.code
            ) from error
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

    if scope is None or request_sha256 is None:
        return await create()
    pending, replay = await _reserve_idempotency_receipt(
        session,
        owner_scope=scope[0],
        operation=_DOCUMENT_UPLOAD_OPERATION,
        key_hash=scope[1],
        request_sha256=request_sha256,
    )
    if replay is not None:
        try:
            return CanonicalAssetReceipt.model_validate(replay)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="idempotency_record_incomplete"
            ) from error
    if pending is None:
        raise HTTPException(status_code=409, detail="idempotency_record_incomplete")
    receipt = await create()
    await _complete_idempotency_receipt(
        session,
        receipt_id=pending.id,
        response_payload=receipt.model_dump(mode="json"),
        asset_id=receipt.asset_id,
    )
    return receipt


@ASSET_GATEWAY_ROUTER.post(
    "/references/snapshots",
    response_model=ReferenceSnapshotReceipt,
)
async def post_reference_snapshot(
    request: ReferenceSnapshotCreateRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    session: AsyncSession = Depends(get_async_session),
) -> ReferenceSnapshotReceipt:
    """Capture mutable provider input once for deterministic generation retries."""

    scope = _idempotency_scope(idempotency_key)
    request_sha256 = _snapshot_payload_sha256(request)

    async def create() -> ReferenceSnapshotReceipt:
        try:
            snapshot = await create_reference_snapshot(
                session,
                provider_id=request.provider_id,
                request=request.source,
                conversation_id=request.conversation_id,
            )
        except ReferenceProviderError as error:
            raise HTTPException(
                status_code=error.status_code, detail=error.code
            ) from error
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

    if scope is None:
        return await create()
    pending, replay = await _reserve_idempotency_receipt(
        session,
        owner_scope=scope[0],
        operation=_SNAPSHOT_CREATE_OPERATION,
        key_hash=scope[1],
        request_sha256=request_sha256,
    )
    if replay is not None:
        try:
            return ReferenceSnapshotReceipt.model_validate(replay)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="idempotency_record_incomplete"
            ) from error
    if pending is None:
        raise HTTPException(status_code=409, detail="idempotency_record_incomplete")
    receipt = await create()
    await _complete_idempotency_receipt(
        session,
        receipt_id=pending.id,
        response_payload=receipt.model_dump(mode="json"),
        snapshot_id=receipt.snapshot_id,
    )
    return receipt


async def _expire_unclaimed_assets(
    session: AsyncSession,
    *,
    asset_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    assets: list[WorkspaceAsset] = []
    for asset_id in asset_ids:
        try:
            asset = await get_workspace_asset(session, asset_id=asset_id)
        except WorkspaceServiceError as error:
            if error.status_code == 404:
                continue
            raise HTTPException(
                status_code=error.status_code, detail=error.code
            ) from error
        if asset.conversation_id is not None:
            raise HTTPException(status_code=409, detail="asset_already_claimed")
        assets.append(asset)

    now = get_current_utc_datetime()
    expired: list[uuid.UUID] = []
    for asset in assets:
        if asset.state == "deleted":
            continue
        if asset.state != "expired":
            asset.state = "expired"
            asset.failure_code = "asset_admission_compensated"
            asset.failure_detail = {"retryable": False}
            asset.expires_at = now
            asset.version += 1
            asset.updated_at = now
            session.add(asset)
        expired.append(asset.id)
    return expired


@ASSET_GATEWAY_ROUTER.delete(
    "/assets/documents/{asset_id}",
    response_model=CanonicalCleanupReceipt,
)
async def delete_unclaimed_document_asset(
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> CanonicalCleanupReceipt:
    """Expire one owner-scoped upload only while it remains unattached."""

    expired = await _expire_unclaimed_assets(session, asset_ids=[asset_id])
    await _delete_resource_idempotency_receipts(
        session,
        asset_ids=[asset_id],
    )
    await session.commit()
    return CanonicalCleanupReceipt(asset_ids=expired or [asset_id])


@ASSET_GATEWAY_ROUTER.delete(
    "/references/snapshots/{snapshot_id}",
    response_model=CanonicalCleanupReceipt,
)
async def delete_unclaimed_reference_snapshot(
    snapshot_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> CanonicalCleanupReceipt:
    """Compensate an abandoned snapshot and its still-unclaimed assets."""

    row = await session.scalar(
        select(ReferenceSnapshot).where(
            ReferenceSnapshot.id == snapshot_id,
            ReferenceSnapshot.owner_id == get_current_owner_id(),
        )
    )
    if row is None:
        return CanonicalCleanupReceipt(asset_ids=[], snapshot_id=snapshot_id)
    if row.conversation_id is not None:
        raise HTTPException(status_code=409, detail="reference_snapshot_already_claimed")
    try:
        asset_ids = [
            uuid.UUID(str(binding["assetId"])) for binding in row.asset_bindings
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=409, detail="reference_snapshot_payload_invalid"
        ) from error
    expired = await _expire_unclaimed_assets(session, asset_ids=asset_ids)
    await _delete_resource_idempotency_receipts(
        session,
        asset_ids=asset_ids,
        snapshot_id=snapshot_id,
    )
    await session.delete(row)
    await session.commit()
    return CanonicalCleanupReceipt(
        asset_ids=expired,
        snapshot_id=snapshot_id,
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
