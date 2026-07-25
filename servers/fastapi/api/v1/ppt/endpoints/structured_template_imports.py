from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import mimetypes
from typing import Any
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.database import get_async_session
from services.template_v2_pptx_ingestion_service import (
    IMPORT_TASK_KIND,
    cancel_template_v2_pptx_import,
    confirm_template_v2_pptx_import,
    notify_template_v2_pptx_dispatcher,
    requeue_failed_template_v2_pptx_import,
)
from services.template_v2_pptx_storage import (
    PptxUploadRejected,
    private_asset_reference,
    remove_private_source,
    resolve_private_asset,
    store_private_pptx,
)
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)
from templates.v2.pptx.source_inventory import SourceInventory
from utils.simple_auth import get_request_owner_scope


STRUCTURED_TEMPLATE_IMPORTS_ROUTER = APIRouter(
    prefix="/structured-templates/imports",
    tags=["Structured Template Imports"],
)
_ASSET_NOT_FOUND_DETAIL = "Structured template import asset not found"


class TemplateV2PptxImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    task_id: str
    requested_template_id: str
    draft_template_id: str | None
    confirmed_template_id: str | None
    state: str
    revision: int
    source_filename: str
    source_size_bytes: int
    source_sha256: str
    pipeline_version: str
    attempt_number: int
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    source_retention_expires_at: datetime | None
    source_cleanup_attempted_at: datetime | None
    source_deleted_at: datetime | None
    source_inventory: dict[str, Any]
    manifest: dict[str, Any]
    analysis_result: dict[str, Any] | None
    repeat_suggestions: list[dict[str, Any]]
    confirmed_at: datetime | None
    cancelled_at: datetime | None
    task_status: str
    task_message: str | None
    task_error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


def _response(
    import_job: TemplateV2PptxImport,
    task: AsyncPresentationGenerationTaskModel,
) -> TemplateV2PptxImportResponse:
    return TemplateV2PptxImportResponse(
        id=import_job.id,
        task_id=import_job.task_id,
        requested_template_id=import_job.requested_template_id,
        draft_template_id=import_job.draft_template_id,
        confirmed_template_id=(
            import_job.draft_template_id if import_job.state == "confirmed" else None
        ),
        state=import_job.state,
        revision=import_job.revision,
        source_filename=import_job.source_filename,
        source_size_bytes=import_job.source_size_bytes,
        source_sha256=import_job.source_sha256,
        pipeline_version=import_job.pipeline_version,
        attempt_number=import_job.attempt_number,
        lease_expires_at=import_job.lease_expires_at,
        heartbeat_at=import_job.heartbeat_at,
        source_retention_expires_at=import_job.source_retention_expires_at,
        source_cleanup_attempted_at=import_job.source_cleanup_attempted_at,
        source_deleted_at=import_job.source_deleted_at,
        source_inventory=dict(
            (import_job.manifest or {}).get("source_inventory") or {}
        ),
        manifest=dict(import_job.manifest or {}),
        analysis_result=(
            dict(import_job.analysis_result)
            if import_job.analysis_result
            else None
        ),
        repeat_suggestions=list(import_job.repeat_suggestions or []),
        confirmed_at=import_job.confirmed_at,
        cancelled_at=import_job.cancelled_at,
        task_status=task.status,
        task_message=task.message,
        task_error=dict(task.error) if task.error else None,
        created_at=import_job.created_at,
        updated_at=import_job.updated_at,
    )


class ImportMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class ConfirmImportRequest(ImportMutationRequest):
    accepted_repeat_suggestion_ids: list[str] = Field(
        default_factory=list,
        max_length=1_000,
    )


def _require_import_enabled(template_id: str) -> None:
    try:
        get_structured_template_policy().require_write_enabled(template_id)
    except StructuredTemplatePolicyError as error:
        raise HTTPException(status_code=403, detail=error.code) from error


def _owner_scope(request: Request) -> str:
    try:
        return get_request_owner_scope(request)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated owner scope unavailable",
        ) from error


def _request_key_hash(owner_scope: str, idempotency_key: str) -> str:
    return hmac.new(
        owner_scope.encode("utf-8"),
        b"template-v2-pptx-import-request-v1\x00"
        + idempotency_key.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _request_fingerprint(template_id: str, source_sha256: str) -> str:
    return hashlib.sha256(
        f"{template_id}\x00{source_sha256}".encode("utf-8")
    ).hexdigest()


async def _load_import(
    import_id: uuid.UUID,
    session: AsyncSession,
    owner_scope: str,
) -> tuple[TemplateV2PptxImport, AsyncPresentationGenerationTaskModel]:
    import_job = (
        await session.execute(
            select(TemplateV2PptxImport).where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.owner_scope == owner_scope,
            )
        )
    ).scalar_one_or_none()
    if import_job is None:
        raise HTTPException(status_code=404, detail="Structured template import not found")
    task = await session.get(
        AsyncPresentationGenerationTaskModel,
        import_job.task_id,
    )
    if (
        task is None
        or not isinstance(task.data, dict)
        or task.data.get("kind") != IMPORT_TASK_KIND
        or task.data.get("import_id") != str(import_job.id)
    ):
        raise HTTPException(status_code=409, detail="Structured template import task invalid")
    return import_job, task


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.post(
    "",
    response_model=TemplateV2PptxImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_structured_template_import(
    request: Request,
    template_id: str = Form(min_length=1, max_length=128),
    pptx_file: UploadFile = File(),
    idempotency_key: str = Header(
        min_length=8,
        max_length=200,
        alias="Idempotency-Key",
    ),
    sql_session: AsyncSession = Depends(get_async_session),
):
    _require_import_enabled(template_id)
    owner_scope = _owner_scope(request)
    request_key_hash = _request_key_hash(owner_scope, idempotency_key)
    import_id = uuid.uuid4()
    try:
        stored = await store_private_pptx(pptx_file, import_id=import_id)
    except PptxUploadRejected as error:
        status_code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if error.code == "pptx_upload_size_limit_exceeded"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=error.code) from error
    request_fingerprint = _request_fingerprint(template_id, stored.sha256)
    existing = (
        await sql_session.execute(
            select(TemplateV2PptxImport).where(
                TemplateV2PptxImport.owner_scope == owner_scope,
                TemplateV2PptxImport.request_key_hash == request_key_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        remove_private_source(stored.storage_key)
        if existing.request_fingerprint != request_fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was reused for a different import",
            )
        task = await sql_session.get(
            AsyncPresentationGenerationTaskModel,
            existing.task_id,
        )
        if task is None:
            raise HTTPException(
                status_code=409,
                detail="Structured template import task invalid",
            )
        return _response(existing, task)
    if await sql_session.get(TemplateV2, template_id) is not None:
        remove_private_source(stored.storage_key)
        raise HTTPException(status_code=409, detail="Structured template already exists")
    task = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="Queued for private PPTX validation",
        data={
            "kind": IMPORT_TASK_KIND,
            "import_id": str(import_id),
            "state": "queued",
            "attempt_number": 0,
        },
    )
    import_job = TemplateV2PptxImport(
        id=import_id,
        task_id=task.id,
        owner_scope=owner_scope,
        request_key_hash=request_key_hash,
        request_fingerprint=request_fingerprint,
        requested_template_id=template_id,
        state="queued",
        source_filename=stored.display_filename,
        source_media_type=stored.media_type,
        source_size_bytes=stored.size_bytes,
        source_sha256=stored.sha256,
        source_storage_key=stored.storage_key,
        manifest={
            "schema_version": 1,
            "source_inventory": SourceInventory(
                source=stored.secret_free_metadata(),
            ).to_manifest(),
            "review": {"required": True, "reason": "analysis_pending"},
        },
    )
    sql_session.add(task)
    sql_session.add(import_job)
    try:
        await sql_session.commit()
    except IntegrityError:
        await sql_session.rollback()
        remove_private_source(stored.storage_key)
        existing = (
            await sql_session.execute(
                select(TemplateV2PptxImport).where(
                    TemplateV2PptxImport.owner_scope == owner_scope,
                    TemplateV2PptxImport.request_key_hash == request_key_hash,
                )
            )
        ).scalar_one_or_none()
        if (
            existing is None
            or existing.request_fingerprint != request_fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key conflict",
            )
        task = await sql_session.get(
            AsyncPresentationGenerationTaskModel,
            existing.task_id,
        )
        if task is None:
            raise HTTPException(
                status_code=409,
                detail="Structured template import task invalid",
            )
        return _response(existing, task)
    except Exception:
        await sql_session.rollback()
        remove_private_source(stored.storage_key)
        raise
    notify_template_v2_pptx_dispatcher()
    return _response(import_job, task)


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.get(
    "/{import_id}",
    response_model=TemplateV2PptxImportResponse,
)
async def get_structured_template_import(
    import_id: uuid.UUID,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    import_job, task = await _load_import(
        import_id,
        sql_session,
        _owner_scope(request),
    )
    return _response(import_job, task)


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.get(
    "/{import_id}/assets/{asset_name}",
    response_class=FileResponse,
)
async def get_structured_template_import_asset(
    import_id: uuid.UUID,
    asset_name: str,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Serve one relocated import asset from outside the /app_data mount.

    Ungated by the write policy on purpose: layouts persist these references as
    `image.data`, so the rollout flag must never blank an imported deck
    (`StructuredTemplatePolicy.can_read_existing`).
    """

    # Owner scoping first, 404 not 403, so a foreign import is never confirmed.
    await _load_import(import_id, sql_session, _owner_scope(request))
    try:
        asset = resolve_private_asset(
            private_asset_reference(import_id, asset_name),
            expected_import_id=import_id,
        )
    except PptxUploadRejected as error:
        # An unsafe reference must be indistinguishable from a missing asset.
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND_DETAIL) from error
    if not asset.is_file():
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND_DETAIL)
    # Generic binary beats Starlette's `text/plain` fallback for emf/wmf media.
    media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    return FileResponse(asset, media_type=media_type)


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.post(
    "/{import_id}/retry",
    response_model=TemplateV2PptxImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_structured_template_import(
    import_id: uuid.UUID,
    request: Request,
    mutation: ImportMutationRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    owner_scope = _owner_scope(request)
    import_job, task = await _load_import(import_id, sql_session, owner_scope)
    _require_import_enabled(import_job.requested_template_id)
    if import_job.state != "failed" or task.status != "error":
        raise HTTPException(status_code=409, detail="Only failed imports can be retried")
    if await sql_session.get(TemplateV2, import_job.requested_template_id) is not None:
        raise HTTPException(status_code=409, detail="Structured template already exists")
    manifest = {
        **dict(import_job.manifest or {}),
        "failure": None,
        "review": {"required": True, "reason": "retry_pending"},
    }
    if not await requeue_failed_template_v2_pptx_import(
        sql_session,
        import_job.id,
        task.id,
        manifest,
        expected_revision=mutation.expected_revision,
    ):
        raise HTTPException(
            status_code=409,
            detail="Import retry was already claimed",
        )
    await sql_session.refresh(import_job)
    await sql_session.refresh(task)
    notify_template_v2_pptx_dispatcher()
    return _response(import_job, task)


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.post(
    "/{import_id}/cancel",
    response_model=TemplateV2PptxImportResponse,
)
async def cancel_structured_template_import(
    import_id: uuid.UUID,
    request: Request,
    mutation: ImportMutationRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    owner_scope = _owner_scope(request)
    import_job, task = await _load_import(import_id, sql_session, owner_scope)
    _require_import_enabled(import_job.requested_template_id)
    if not await cancel_template_v2_pptx_import(
        sql_session,
        import_job.id,
        task.id,
        owner_scope=owner_scope,
        expected_revision=mutation.expected_revision,
    ):
        raise HTTPException(
            status_code=409,
            detail="Import state or revision changed",
        )
    await sql_session.refresh(import_job)
    await sql_session.refresh(task)
    return _response(import_job, task)


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.post(
    "/{import_id}/confirm",
    response_model=TemplateV2PptxImportResponse,
)
async def confirm_structured_template_import(
    import_id: uuid.UUID,
    request: Request,
    mutation: ConfirmImportRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    owner_scope = _owner_scope(request)
    import_job, task = await _load_import(import_id, sql_session, owner_scope)
    _require_import_enabled(import_job.requested_template_id)
    result = await confirm_template_v2_pptx_import(
        sql_session,
        import_job.id,
        task.id,
        owner_scope=owner_scope,
        expected_revision=mutation.expected_revision,
        accepted_repeat_suggestion_ids=tuple(
            mutation.accepted_repeat_suggestion_ids
        ),
    )
    if result == "not_found":
        raise HTTPException(
            status_code=404,
            detail="Structured template import not found",
        )
    if result not in {"confirmed", "already_confirmed"}:
        details = {
            "revision_conflict": "Import revision changed",
            "state_conflict": "Import is not ready for confirmation",
            "template_conflict": "Structured template already exists",
            "suggestion_conflict": "Repeat-block selection is invalid",
            "assets_reclaimed": (
                "Import media was reclaimed by retention; re-import the source deck"
            ),
        }
        raise HTTPException(
            status_code=409,
            detail=details.get(result, "Import confirmation conflict"),
        )
    await sql_session.refresh(import_job)
    await sql_session.refresh(task)
    return _response(import_job, task)
