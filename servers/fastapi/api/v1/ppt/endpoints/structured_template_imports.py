from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.database import get_async_session
from services.template_v2_pptx_ingestion_service import (
    IMPORT_TASK_KIND,
    notify_template_v2_pptx_dispatcher,
    requeue_failed_template_v2_pptx_import,
)
from services.template_v2_pptx_storage import (
    PptxUploadRejected,
    remove_private_source,
    store_private_pptx,
)
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)


STRUCTURED_TEMPLATE_IMPORTS_ROUTER = APIRouter(
    prefix="/structured-templates/imports",
    tags=["Structured Template Imports"],
)


class TemplateV2PptxImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    task_id: str
    requested_template_id: str
    draft_template_id: str | None
    state: str
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
    manifest: dict[str, Any]
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
        state=import_job.state,
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
        manifest=dict(import_job.manifest or {}),
        task_status=task.status,
        task_message=task.message,
        task_error=dict(task.error) if task.error else None,
        created_at=import_job.created_at,
        updated_at=import_job.updated_at,
    )


def _require_import_enabled(template_id: str) -> None:
    try:
        get_structured_template_policy().require_write_enabled(template_id)
    except StructuredTemplatePolicyError as error:
        raise HTTPException(status_code=403, detail=error.code) from error


async def _load_import(
    import_id: uuid.UUID,
    session: AsyncSession,
) -> tuple[TemplateV2PptxImport, AsyncPresentationGenerationTaskModel]:
    import_job = await session.get(TemplateV2PptxImport, import_id)
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
    template_id: str = Form(min_length=1, max_length=128),
    pptx_file: UploadFile = File(),
    sql_session: AsyncSession = Depends(get_async_session),
):
    _require_import_enabled(template_id)
    if await sql_session.get(TemplateV2, template_id) is not None:
        raise HTTPException(status_code=409, detail="Structured template already exists")
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
        requested_template_id=template_id,
        state="queued",
        source_filename=stored.display_filename,
        source_media_type=stored.media_type,
        source_size_bytes=stored.size_bytes,
        source_sha256=stored.sha256,
        source_storage_key=stored.storage_key,
        manifest={
            "schema_version": 1,
            "source_sha256": stored.sha256,
            "review": {"required": True, "reason": "analysis_pending"},
        },
    )
    sql_session.add(task)
    sql_session.add(import_job)
    try:
        await sql_session.commit()
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
    sql_session: AsyncSession = Depends(get_async_session),
):
    import_job, task = await _load_import(import_id, sql_session)
    return _response(import_job, task)


@STRUCTURED_TEMPLATE_IMPORTS_ROUTER.post(
    "/{import_id}/retry",
    response_model=TemplateV2PptxImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_structured_template_import(
    import_id: uuid.UUID,
    sql_session: AsyncSession = Depends(get_async_session),
):
    import_job, task = await _load_import(import_id, sql_session)
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
    ):
        raise HTTPException(
            status_code=409,
            detail="Import retry was already claimed",
        )
    await sql_session.refresh(import_job)
    await sql_session.refresh(task)
    notify_template_v2_pptx_dispatcher()
    return _response(import_job, task)
