"""One-page remake: deck load, digests, task writes, dispatch to svg.

Split out of ``authored_page_remake_service``, which was deleted with the
authored generation pipeline. Preview and publication load the deck here and
send the page to ``services.svg_page_remake``; a page or deck that is not svg
(an authored HTML deck) is refused with
``authored_page_remake_unsupported_presentation``. The names, error codes and
task payloads are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import weakref
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from constants.user_messages import (
    UserMessageKey,
    UserMessageLike,
    set_record_message,
    user_message,
)
from models.presentation_from_template import AuthoredPageRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import async_session_maker
from services.svg_page_remake import preview_svg, publish_svg_candidate
from services.workspace_authored_async_admission import admit_workspace_authored_task
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import sha256_prefixed

LOGGER = logging.getLogger(__name__)

PREVIEW_KIND = "authored_page_remake_preview"

#: One mutex per deck. Page-remake publication and batch publication rewrite
#: the same slides, so they take turns. Preview generation deliberately does
#: NOT take it -- see the note there. Moved unchanged from the deleted
#: ``authored_quality_review_service``.
_ANALYZE_AND_FIX_LOCKS: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


class AuthoredPageRemakeError(RuntimeError):
    """Stable fail-closed error suitable for a task or operation result."""

    def __init__(self, code: str, *, stage: str, retryable: bool = False,
                 preview_evidence: dict[str, Any] | None = None) -> None:
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.preview_evidence = preview_evidence
        super().__init__(code)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256_prefixed(encoded)


def authored_page_source_digest(slide: SlideModel) -> str:
    """Bind a preview to every persisted field replaced by publication."""

    return _canonical_digest(
        {
            "slide_id": str(slide.id),
            "index": slide.index,
            "html": slide.html_content,
            **({"svg": slide.svg_content, "source_format": "svg"} if slide.is_svg() else {}),
            "content": slide.content,
            "properties": slide.properties,
        }
    )


async def _set_task(
    session: AsyncSession,
    task: AsyncPresentationGenerationTaskModel,
    *,
    status: str,
    message: UserMessageLike,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    task.status = status
    set_record_message(task, message)
    task.data = data
    task.error = error
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.commit()


async def _load_deck(
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int | None = None,
) -> tuple[PresentationModel, list[SlideModel]]:
    """Load the deck and refuse a target this service cannot remake.

    Only svg decks and svg pages are remade (authored HTML remake was removed
    with the authored pipeline). The requirement is on the page being remade,
    not on its neighbours: everything downstream is index-addressed, so a
    non-svg sibling does not affect the candidate for ``slide_index``.

    ``slide_index`` is validated only when it is in range; callers keep raising
    their own out-of-range errors so the reported codes stay unchanged.
    """

    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise AuthoredPageRemakeError(
            "presentation_not_found", stage="source_validation"
        )
    rows = await session.execute(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    slides = list(rows.scalars().all())
    unsupported_target = (
        slide_index is not None
        and 0 <= slide_index < len(slides)
        and not slides[slide_index].is_svg()
    )
    if not slides or not presentation.is_svg() or unsupported_target:
        raise AuthoredPageRemakeError(
            "authored_page_remake_unsupported_presentation",
            stage="source_validation",
        )
    return presentation, slides


async def run_authored_page_remake_preview(
    presentation_id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    """Generate and validate one detached candidate; never mutate a live slide."""

    # Deliberately unlocked, so a batch can preview four pages at once. A
    # process-local lock could not exclude publication anyway — the workspace
    # worker has its own OS process — so a deck that moves underneath is caught
    # by the candidate's source digest at publish time, not by exclusion here.
    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_slide_out_of_range", stage="source_validation"
        )
    return await preview_svg(presentation, slides, slide_index, request, task, session)


def _failed_preview_breadcrumb(
    presentation_id: uuid.UUID, slide_index: int
) -> dict[str, Any]:
    """Identify a failed attempt without implying a usable candidate.

    History is listed by reading task payloads, so an attempt that stores nothing
    is invisible afterwards.  The phase is deliberately not ``preview_ready``:
    publication validates that field and must keep rejecting this task.
    """

    return {
        "kind": PREVIEW_KIND,
        "phase": "failed",
        "presentation_id": str(presentation_id),
        "slide_index": slide_index,
    }


async def _run_preview_background(
    presentation_id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    task_id: str,
) -> None:
    async with async_session_maker() as session:
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            return
        try:
            await run_authored_page_remake_preview(
                presentation_id, slide_index, request, task, session
            )
        except AuthoredPageRemakeError as error:
            await session.rollback()
            await _set_task(
                session,
                task,
                status="error",
                message=user_message(UserMessageKey.REMAKE_PAGE_FAILED),
                data={**(error.preview_evidence or {}),
                      **_failed_preview_breadcrumb(presentation_id, slide_index)},
                error={
                    "detail": error.code,
                    "stage": error.stage,
                    "retryable": error.retryable,
                },
            )
        except Exception as error:  # noqa: BLE001 - background task boundary
            LOGGER.warning(
                "Authored page remake preview failed (%s)",
                type(error).__name__,
            )
            await session.rollback()
            await _set_task(
                session,
                task,
                status="error",
                message=user_message(UserMessageKey.REMAKE_PAGE_FAILED),
                data=_failed_preview_breadcrumb(presentation_id, slide_index),
                error={
                    "detail": "authored_page_remake_failed",
                    "stage": "unknown",
                    "retryable": True,
                },
            )


async def queue_authored_page_remake_preview(
    presentation_id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    *,
    allow_local_development: bool = False,
) -> AsyncPresentationGenerationTaskModel:
    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise HTTPException(
            status_code=400, detail="Slide index is outside the presentation"
        )
    if not slides[slide_index].html_content and not slides[slide_index].is_svg():
        raise HTTPException(status_code=400, detail="Slide has no authored HTML")
    # The stored workspace request is the restart-safe source of truth.  The
    # legacy task remains the polling/read model consumed by the current UI.
    return await admit_workspace_authored_task(
        session,
        presentation_id=presentation_id,
        operation_kind="slide.regenerate.preview",
        expected_revision=int(presentation.revision),
        command=user_message(UserMessageKey.REMAKE_PAGE_COMMAND, page=slide_index + 1),
        context={
            "slide_index": slide_index,
            "instruction": request.instruction,
            **({"mode": request.mode, "source_digest": request.source_digest,
                **({"svg_content": request.svg_content} if request.mode == "local_svg" else {})}
               if request.mode != "author" else {}),
        },
        task_id_prefix="workspace-page-preview",
        pending_message=user_message(
            UserMessageKey.REMAKE_PAGE_QUEUED, page=slide_index + 1
        ),
        allow_local_development=allow_local_development,
    )


async def publish_authored_page_remake(
    *,
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int,
    preview_task_id: str,
    snapshot: bool = True,
    operation_id: uuid.UUID | None = None,
    attempt_token: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Stage one preview publication in the caller-owned transaction."""

    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_slide_out_of_range", stage="publish_validation"
        )
    task = await session.get(AsyncPresentationGenerationTaskModel, preview_task_id)
    if task is None:
        raise AuthoredPageRemakeError(
            "authored_page_remake_preview_not_found", stage="publish_validation"
        )
    return await publish_svg_candidate(session, presentation, slides, slide_index, task, snapshot=snapshot,
        operation_id=operation_id, attempt_token=attempt_token, expected_revision=expected_revision)


async def publish_authored_page_remake_legacy(
    *,
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int,
    preview_task_id: str,
) -> dict[str, Any]:
    """Publish one preview through the HTTP route and commit it under the deck lock."""

    lock = _ANALYZE_AND_FIX_LOCKS.setdefault(presentation_id, asyncio.Lock())
    async with lock:
        try:
            result = await publish_authored_page_remake(
                session=session,
                presentation_id=presentation_id,
                slide_index=slide_index,
                preview_task_id=preview_task_id,
            )
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


def remake_http_error(error: AuthoredPageRemakeError) -> HTTPException:
    if error.code in {
        "presentation_not_found",
        "authored_page_remake_preview_not_found",
    }:
        status = 404
    elif error.code in {
        "authored_page_remake_source_changed",
        "authored_page_remake_publish_cas_conflict",
    }:
        status = 409
    else:
        status = 400
    return HTTPException(
        status_code=status,
        detail={"code": error.code, "stage": error.stage},
    )


__all__ = [
    "PREVIEW_KIND",
    "AuthoredPageRemakeError",
    "authored_page_source_digest",
    "publish_authored_page_remake",
    "publish_authored_page_remake_legacy",
    "queue_authored_page_remake_preview",
    "remake_http_error",
    "run_authored_page_remake_preview",
]
