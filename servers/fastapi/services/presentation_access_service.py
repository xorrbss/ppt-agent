"""Shared access checks for presentation tombstones and retired drivers.

Durable workspace deletion intentionally retains a minimal presentation row so
the delete operation and timeline remain replayable.  That row is not a live
deck and must never be exposed to, or mutated by, legacy presentation routes.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_profiles import (
    PresentationProfileError,
    resolve_presentation_profile,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)

UNSUPPORTED_PRESENTATION_DRIVER = "unsupported_presentation_driver"


async def require_live_presentation(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
) -> PresentationModel:
    """Return an owner-scoped live deck, hiding durable delete tombstones."""

    presentation = await sql_session.get(PresentationModel, presentation_id)
    if (
        presentation is None
        or presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")
    return presentation


# Workspace operation kinds a deck with a removed driver may still run:
# deletion, and generation, whose own admission resolves the requested driver.
UNSUPPORTED_DRIVER_PERMITTED_OPERATION_KINDS = frozenset(
    {"presentation.delete", "presentation.generate"}
)


def has_unsupported_presentation_driver(presentation: PresentationModel) -> bool:
    """True when no registered driver claims the row's persisted identity."""

    try:
        resolve_presentation_profile(presentation)
    except PresentationProfileError as error:
        return error.code == UNSUPPORTED_PRESENTATION_DRIVER
    return False


def require_supported_presentation_driver(presentation: PresentationModel) -> None:
    """Refuse a deck whose persisted identity no registered driver claims.

    A pre-removal row (a Template V2 deck, or an authored deck since the
    authored driver was removed on 2026-11-10) can still be listed and
    deleted, but a route that reads or acts on its content answers the same
    422 ``unsupported_presentation_driver`` as ``GET /presentation/{id}``
    instead of reaching retired code or an unhandled 500.
    """

    try:
        resolve_presentation_profile(presentation)
    except PresentationProfileError as error:
        raise HTTPException(status_code=422, detail=error.code) from error


async def require_supported_live_presentation(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
) -> PresentationModel:
    """``require_live_presentation`` plus the supported-driver check."""

    presentation = await require_live_presentation(sql_session, presentation_id)
    require_supported_presentation_driver(presentation)
    return presentation
