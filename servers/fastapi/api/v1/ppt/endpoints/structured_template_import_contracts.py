from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2_pptx_import import TemplateV2PptxImport


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


class ImportMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class ConfirmImportRequest(ImportMutationRequest):
    accepted_repeat_suggestion_ids: list[str] = Field(
        default_factory=list,
        max_length=1_000,
    )


def template_import_response(
    import_job: TemplateV2PptxImport,
    task: AsyncPresentationGenerationTaskModel,
) -> TemplateV2PptxImportResponse:
    return TemplateV2PptxImportResponse(
        id=import_job.id,
        task_id=import_job.task_id,
        requested_template_id=import_job.requested_template_id,
        draft_template_id=import_job.draft_template_id,
        confirmed_template_id=(import_job.draft_template_id if import_job.state == "confirmed" else None),
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
        source_inventory=dict((import_job.manifest or {}).get("source_inventory") or {}),
        manifest=dict(import_job.manifest or {}),
        analysis_result=(dict(import_job.analysis_result) if import_job.analysis_result else None),
        repeat_suggestions=list(import_job.repeat_suggestions or []),
        confirmed_at=import_job.confirmed_at,
        cancelled_at=import_job.cancelled_at,
        task_status=task.status,
        task_message=task.message,
        task_error=dict(task.error) if task.error else None,
        created_at=import_job.created_at,
        updated_at=import_job.updated_at,
    )
