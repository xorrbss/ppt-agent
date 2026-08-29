"""Bounded fan-out of detached one-page remake previews for a page selection.

This service owns orchestration only.  Every candidate is produced by the
existing single-page preview path so publication keeps validating a real
per-slide preview task by id, owner and source digest.  Nothing here mutates a
live slide, and no deck lock is taken: the per-slide preview acquires its own
reader.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_batch_remake import AuthoredBatchRemakePreviewRequest
from models.presentation_from_template import AuthoredPageRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_page_remake_service import _run_preview_background
from services.database import async_session_maker
from services.workspace_authored_async_admission import admit_workspace_authored_task
from services import presentation_version_service as version_service
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)

BATCH_PREVIEW_KIND = "authored_batch_remake_preview"
BATCH_PREVIEW_CONCURRENCY = 4
MAX_BATCH_SLIDES = 20

#: Process-wide, not per-request. A semaphore built inside the fan-out would
#: cap each batch at four while two concurrent batches ran eight previews,
#: which is the cap the user actually asked for being silently exceeded.
#: Created lazily so it binds to the running loop.
_preview_semaphore: asyncio.Semaphore | None = None


def _get_preview_semaphore() -> asyncio.Semaphore:
    global _preview_semaphore
    if _preview_semaphore is None:
        _preview_semaphore = asyncio.Semaphore(BATCH_PREVIEW_CONCURRENCY)
    return _preview_semaphore


def _pending_entry(slide_index: int, child_task_id: str) -> dict[str, Any]:
    return {
        "slide_index": slide_index,
        "status": "pending",
        "preview_task_id": child_task_id,
    }


def _child_outcome_entry(
    slide_index: int,
    child_task_id: str,
    task: AsyncPresentationGenerationTaskModel | None,
) -> dict[str, Any]:
    """Project one settled child task into the parent's progress envelope."""

    entry = _pending_entry(slide_index, child_task_id)
    data = task.data if task is not None and isinstance(task.data, dict) else {}
    if (
        task is not None
        and task.status == "completed"
        and data.get("phase") == "preview_ready"
    ):
        validation = data.get("validation")
        entry.update(
            {
                "status": "previewed",
                "candidate_image": data.get("candidate_image"),
                "candidate_digest": data.get("candidate_digest"),
                "source_digest": data.get("source_digest"),
                "warning": isinstance(validation, dict)
                and validation.get("candidate_dom") == "warning",
            }
        )
        return entry
    error = task.error if task is not None and isinstance(task.error, dict) else {}
    reason_code = error.get("detail")
    entry.update(
        {
            "status": "failed",
            "reason_code": (
                reason_code
                if isinstance(reason_code, str) and reason_code
                else "authored_page_remake_failed"
            ),
            "stage": (
                error.get("stage") if isinstance(error.get("stage"), str) else "unknown"
            ),
            "retryable": bool(error.get("retryable")),
        }
    )
    return entry


async def _record_child_outcome(
    parent_task_id: str, slide_index: int, child_task_id: str
) -> None:
    """Publish one slide's outcome so the polling client sees it land."""

    async with async_session_maker() as session:
        parent = await session.get(AsyncPresentationGenerationTaskModel, parent_task_id)
        if parent is None:
            return
        child = await session.get(AsyncPresentationGenerationTaskModel, child_task_id)
        entry = _child_outcome_entry(slide_index, child_task_id, child)
        if entry["status"] == "failed":
            LOGGER.warning(
                "Authored batch remake preview failed (slide_index=%s, reason=%s)",
                slide_index,
                entry["reason_code"],
            )
        data = dict(parent.data) if isinstance(parent.data, dict) else {}
        raw_slides = data.get("slides")
        slides = [dict(item) for item in raw_slides if isinstance(item, dict)] if isinstance(raw_slides, list) else []
        for position, item in enumerate(slides):
            if item.get("slide_index") == slide_index:
                slides[position] = entry
                break
        completed = sum(1 for item in slides if item.get("status") != "pending")
        total = int(data.get("total") or len(slides))
        data["slides"] = slides
        data["completed"] = completed
        if completed >= total:
            data["phase"] = "preview_ready"
            parent.status = "completed"
            parent.message = f"{total}개 페이지의 새 버전 미리보기가 준비되었습니다."
        else:
            data["phase"] = "previewing"
            parent.status = "processing"
            parent.message = f"{total}개 페이지 중 {completed}개의 새 버전을 만들었습니다."
        parent.data = data
        parent.updated_at = get_current_utc_datetime()
        session.add(parent)
        await session.commit()


async def _run_batch_preview_background(
    presentation_id: uuid.UUID,
    request: AuthoredBatchRemakePreviewRequest,
    parent_task_id: str,
    pairs: tuple[tuple[int, str], ...],
) -> None:
    page_request = AuthoredPageRemakePreviewRequest(instruction=request.instruction)
    semaphore = _get_preview_semaphore()
    # Parent-task updates only.  Candidate generation stays parallel; the
    # progress envelope is a single row and must be written one writer at a time.
    progress_lock = asyncio.Lock()

    async def _one(slide_index: int, child_task_id: str) -> None:
        try:
            async with semaphore:
                await _run_preview_background(
                    presentation_id, slide_index, page_request, child_task_id
                )
        finally:
            # Recorded unconditionally: a slide whose outcome is never written
            # leaves the parent stuck below `total`, and the polling client
            # would wait out its whole deadline for a batch that is over.
            async with progress_lock:
                await _record_child_outcome(parent_task_id, slide_index, child_task_id)

    # `_run_preview_background` already converts every failure into an error
    # child task, but a cancelled sibling must never take the batch down with it.
    await asyncio.gather(
        *(_one(index, child_task_id) for index, child_task_id in pairs),
        return_exceptions=True,
    )


async def _load_batch_targets(
    session: AsyncSession, presentation_id: uuid.UUID
) -> list[SlideModel]:
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    rows = await session.execute(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    return list(rows.scalars().all())


async def snapshot_slides_for_batch(
    session: AsyncSession, presentation_id: uuid.UUID
) -> None:
    """Take the batch's single undo point, before any page is rewritten.

    Publication normally snapshots per page. A batch publishes up to
    MAX_BATCH_SLIDES pages inside one transaction and one revision bump, and the
    version store keeps only MAX_VERSIONS_PER_PRESENTATION entries, so per-page
    snapshots here would evict the deck's entire real history and leave twenty
    near-identical restore points in its place.
    """
    rows = await session.execute(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    await version_service.snapshot_slides(
        session,
        presentation_id,
        [item.model_dump(mode="json") for item in rows.scalars().all()],
        label="페이지 일괄 다시 만들기",
        force=True,
    )


async def queue_authored_batch_remake_preview(
    presentation_id: uuid.UUID,
    request: AuthoredBatchRemakePreviewRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> AsyncPresentationGenerationTaskModel:
    """Create one child preview task per page plus the parent progress task."""

    slide_indexes = sorted(request.slide_indexes)
    if not slide_indexes or len(slide_indexes) > MAX_BATCH_SLIDES:
        raise HTTPException(
            status_code=400, detail="Select between 1 and 20 pages to remake"
        )
    slides = await _load_batch_targets(session, presentation_id)
    for slide_index in slide_indexes:
        if slide_index < 0 or slide_index >= len(slides):
            raise HTTPException(
                status_code=400, detail="Slide index is outside the presentation"
            )
        if not slides[slide_index].html_content:
            raise HTTPException(status_code=400, detail="Slide has no authored HTML")

    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    parent = await admit_workspace_authored_task(
        session,
        presentation_id=presentation_id,
        operation_kind="slide.regenerate.batch.preview",
        expected_revision=int(presentation.revision),
        command=f"선택한 {len(slide_indexes)}개 페이지 다시 만들기 미리보기",
        context={
            "slide_indexes": slide_indexes,
            "instruction": request.instruction,
        },
        task_id_prefix="workspace-batch-preview",
        pending_message=f"{len(slide_indexes)}개 페이지의 새 버전 작업을 대기열에 추가했습니다.",
    )
    operation_suffix = parent.id.removeprefix("workspace-batch-preview-")
    pairs: list[tuple[int, str]] = []
    for slide_index in slide_indexes:
        child_task_id = f"workspace-page-preview-{operation_suffix}-{slide_index}"
        child = await session.get(AsyncPresentationGenerationTaskModel, child_task_id)
        if child is None:
            child = AsyncPresentationGenerationTaskModel(
                id=child_task_id,
                owner_id=parent.owner_id,
                status="pending",
                message=f"{slide_index + 1}페이지의 새 버전 작업을 대기열에 추가했습니다.",
                data=None,
            )
            session.add(child)
        pairs.append((slide_index, child_task_id))

    total = len(pairs)
    parent.status = "processing"
    parent.message = f"{total}개 페이지의 새 버전을 만들고 있습니다."
    parent.data = {
        "kind": BATCH_PREVIEW_KIND,
        "phase": "previewing",
        "presentation_id": str(presentation_id),
        "total": total,
        "completed": 0,
        "slides": [
            _pending_entry(slide_index, child_task_id)
            for slide_index, child_task_id in pairs
        ],
    }
    session.add(parent)
    await session.commit()
    return parent
