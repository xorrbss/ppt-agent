"""Owner-only, read-only views of unsubmitted SVG checkpoint renders."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint as Checkpoint
from models.sql.presentation_generation_job import PresentationGenerationJob as Job
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from services.database import get_async_session
from services.generation_activity import checkpoint_progress_by_job
from services.generation_failure_class import classify_generation_failure
from services.presentation_access_service import require_live_presentation
from utils.env_settings.authored_runtime import get_authored_checkpoint_directory
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id, get_current_tenant_id


PRESENTATION_DRAFT_PREVIEW_ROUTER = APIRouter()
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


async def _context(session: AsyncSession, presentation_id: uuid.UUID):
    owner = get_current_owner_id()
    tenant = get_current_tenant_id()
    if owner is None or not tenant:
        raise HTTPException(401, "authentication_required")
    presentation = await require_live_presentation(session, presentation_id)
    if presentation.owner_id != owner or not presentation.is_svg():
        raise HTTPException(404, "Presentation not found")
    row = (await session.execute(select(
        Job.id, Job.state, Job.total_slides, Job.current_stage,
        WorkspaceOperation.id.label("operation_id"),
        WorkspaceOperation.state.label("operation_state"), WorkspaceOperation.error_code,
    ).join(WorkspaceOperation, WorkspaceOperation.id == Job.workspace_operation_id).join(
        PublicOperationAdmission, PublicOperationAdmission.operation_id == WorkspaceOperation.id,
    ).where(
        Job.presentation_id == presentation_id,
        WorkspaceOperation.presentation_id == presentation_id,
        WorkspaceOperation.owner_id == owner,
        PublicOperationAdmission.owner_id == owner,
        PublicOperationAdmission.tenant_id == tenant,
    ))).one_or_none()
    if row is None:
        raise HTTPException(404, "draft_preview_unavailable")
    return row


@PRESENTATION_DRAFT_PREVIEW_ROUTER.get("/{presentation_id}/draft-preview")
async def draft_preview(presentation_id: uuid.UUID, response: Response,
                        session: AsyncSession = Depends(get_async_session)):
    job = await _context(session, presentation_id)
    # Read physical metadata columns only: no JSON extraction or TOAST decoding.
    rows = (await session.execute(select(
        Checkpoint.slide_index, Checkpoint.author_status, Checkpoint.render_status,
        Checkpoint.persist_status, Checkpoint.updated_at,
    ).where(Checkpoint.job_id == job.id).order_by(Checkpoint.slide_index))).all()
    # Same definition as the generation-activity list's ``last_progress_at``.
    progress = (await checkpoint_progress_by_job(session, [job.id])).get(job.id)
    last_progress_at = progress.last_progress_at if progress is not None else None
    # Only a registered machine code is returned, never provider detail.
    stop_code = job.error_code if job.error_code and re.fullmatch(r"[a-z0-9_]{1,128}", job.error_code) else None
    response.headers.update(_PRIVATE_HEADERS)
    return {"kind": "checkpoint_draft", "published": False,
            "jobId": str(job.id), "operationId": str(job.operation_id),
            "jobState": job.state, "operationState": job.operation_state,
            "stage": job.current_stage, "totalPages": job.total_slides,
            "lastProgressAt": last_progress_at.isoformat() if last_progress_at is not None else None,
            "stopCode": stop_code,
            "stopClass": classify_generation_failure(stop_code),
            "pages": [{"index": row.slide_index, "author": row.author_status,
                       "render": row.render_status, "persist": row.persist_status,
                       "quality": "unconfirmed", "imageAvailability": "unconfirmed",
                       "pageVersion": row.updated_at.isoformat()}
                      for row in rows]}


@PRESENTATION_DRAFT_PREVIEW_ROUTER.get("/{presentation_id}/draft-preview/{page_index}/image")
async def draft_preview_image(presentation_id: uuid.UUID, page_index: int,
                              job_id: uuid.UUID, revision: str | None = None,
                              page_version: str | None = None,
                              session: AsyncSession = Depends(get_async_session)):
    job = await _context(session, presentation_id)
    if job.id != job_id or page_index < 0:
        raise HTTPException(404, "draft_preview_unavailable")
    if revision is None and page_version is None:
        raise HTTPException(400, "draft_preview_version_required")
    selected = (await session.execute(select(
        Checkpoint.updated_at, Checkpoint.svg_checkpoint["png_sha256"].as_string().label("png_digest"),
    ).where(Checkpoint.job_id == job.id, Checkpoint.slide_index == page_index))).one_or_none()
    if selected is None:
        raise HTTPException(404, "draft_preview_unavailable")
    if page_version is not None and page_version != selected.updated_at.isoformat():
        raise HTTPException(409, "draft_preview_revision_changed")
    digest = selected.png_digest
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(404, "draft_preview_unavailable")
    if revision is not None and revision != digest:
        raise HTTPException(409, "draft_preview_revision_changed")
    # Match the existing checkpoint PNG store without its mkdir-on-read helper.
    root = Path(get_authored_checkpoint_directory() or
                Path(get_app_data_directory_env() or os.path.join(tempfile.gettempdir(), "presenton")) / "authored-checkpoints").resolve()
    path = (root / "svg" / digest[:2] / f"{digest}.png").resolve()
    try:
        if not path.is_relative_to(root) or path.stat().st_size > 64 * 1024 * 1024:
            raise OSError("invalid_checkpoint_image")
        payload = path.read_bytes()
    except OSError as error:
        raise HTTPException(404, "draft_preview_unavailable") from error
    if hashlib.sha256(payload).hexdigest() != digest or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(409, "draft_preview_image_invalid")
    return Response(payload, media_type="image/png", headers=_PRIVATE_HEADERS)
