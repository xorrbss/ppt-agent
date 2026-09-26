"""Presentation readback and deletion helpers shared by routes and workers."""

import uuid

from fastapi import Depends, HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.ppt.endpoints.presentation_helpers import resolve_presentation_fonts
from models.presentation_with_slides import PresentationWithSlides
from models.sql.slide import SlideModel
from services.database import get_async_session
from services.presentation_access_service import require_live_presentation
from services.presentation_deletion_service import (
    PresentationDeleteConflictError,
    PresentationDeleteRetryableError,
    PresentationDeletionService,
    is_retryable_sqlite_lock,
)
from services.presentation_profiles import PresentationProfileError, resolve_presentation_driver


async def _get_presentation_with_slides(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)  # noqa: B008
) -> PresentationWithSlides:
    presentation = await require_live_presentation(sql_session, id)
    slides_result = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == id)
        .order_by(SlideModel.index)
    )
    slides = list(slides_result)
    identity = presentation.model_dump(exclude={"fonts"})
    try:
        resolve_presentation_driver(identity)
    except PresentationProfileError as error:
        # Fail closed with a clean 4xx instead of letting the profile
        # resolution ValueError surface from PresentationWithSlides's model
        # validator as an uncaught 500.
        raise HTTPException(status_code=422, detail=error.code) from error
    fonts = await resolve_presentation_fonts(presentation, slides, sql_session)
    return PresentationWithSlides(**identity, slides=slides, fonts=fonts)


async def apply_presentation_delete(
    id: uuid.UUID,
    sql_session: AsyncSession,
) -> None:
    """Delete a deck and its dependants, mapping service errors to HTTP status.

    This boundary intentionally performs no route/write-gate check: the HTTP
    route applies the runner-only gate, while the durable
    `presentation.delete` operation reaches the same rollback semantics from
    its already-authorized handler.
    """
    try:
        presentation = await require_live_presentation(sql_session, id)
    except OperationalError as error:
        await sql_session.rollback()
        if not is_retryable_sqlite_lock(error, sql_session):
            raise
        raise HTTPException(
            status_code=503,
            detail={
                "code": "presentation_delete_temporarily_unavailable",
                "presentation_id": str(id),
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        ) from error
    try:
        await PresentationDeletionService(sql_session).delete(presentation)
    except PresentationDeleteConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "presentation_delete_dependency_conflict",
                "presentation_id": str(error.presentation_id),
            },
        ) from error
    except PresentationDeleteRetryableError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "presentation_delete_temporarily_unavailable",
                "presentation_id": str(error.presentation_id),
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        ) from error
