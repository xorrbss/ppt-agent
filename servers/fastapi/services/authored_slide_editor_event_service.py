"""Record and aggregate authored-slide editor events.

The write is idempotent on a caller-supplied event key: an editor button that
fires twice, or a request the client retried after a timeout, must not inflate
the edit rate of an archetype.  Owner scoping is central -- the loader-criteria
hook in ``services/database.py`` narrows every SELECT and the ``before_flush``
stamper fills ``owner_id`` -- so nothing here filters by owner.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from models.authored_slide_editor_event import (
    AuthoredSlideEditorEventRequest,
    AuthoredSlideEditorEventResponse,
)
from models.sql.authored_slide_editor_event import AuthoredSlideEditorEvent


def editor_event_key_hash(
    presentation_id: uuid.UUID, request: AuthoredSlideEditorEventRequest
) -> str:
    """Derive the stored idempotency digest.

    The caller's raw key never reaches the database; only a digest of it,
    bound to the deck, the slide and the event type it claimed.
    """

    identity = "|".join(
        (
            str(presentation_id),
            request.event_type,
            "" if request.slide_index is None else str(request.slide_index),
            request.event_key.strip(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _response(
    event: AuthoredSlideEditorEvent, *, deduplicated: bool
) -> AuthoredSlideEditorEventResponse:
    return AuthoredSlideEditorEventResponse(
        id=event.id,
        presentation_id=event.presentation_id,
        slide_index=event.slide_index,
        event_type=event.event_type,
        archetype=event.archetype,
        previous_archetype=event.previous_archetype,
        changed_characters=event.changed_characters,
        created_at=event.created_at,
        deduplicated=deduplicated,
    )


async def _existing_event(
    sql_session: AsyncSession, key_hash: str
) -> AuthoredSlideEditorEvent | None:
    result = await sql_session.execute(
        select(AuthoredSlideEditorEvent).where(
            AuthoredSlideEditorEvent.event_key_hash == key_hash
        )
    )
    return result.scalars().first()


async def record_authored_slide_editor_event(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
    request: AuthoredSlideEditorEventRequest,
) -> AuthoredSlideEditorEventResponse:
    """Append one editor event, returning the existing row on a repeat."""

    key_hash = editor_event_key_hash(presentation_id, request)
    existing = await _existing_event(sql_session, key_hash)
    if existing is not None:
        return _response(existing, deduplicated=True)

    event = AuthoredSlideEditorEvent(
        presentation_id=presentation_id,
        slide_index=request.slide_index,
        event_type=request.event_type,
        archetype=request.archetype,
        previous_archetype=request.previous_archetype,
        changed_characters=request.changed_characters,
        event_key_hash=key_hash,
    )
    sql_session.add(event)
    try:
        await sql_session.commit()
    except IntegrityError:
        # Two concurrent editors sent the same action; the unique constraint
        # is the arbiter, and the loser reports the winner's row.
        await sql_session.rollback()
        settled = await _existing_event(sql_session, key_hash)
        if settled is None:
            raise
        return _response(settled, deduplicated=True)
    await sql_session.refresh(event)
    return _response(event, deduplicated=False)


async def count_editor_events_by_archetype(
    sql_session: AsyncSession, *, since: object = None
) -> Sequence[tuple[str | None, str, int]]:
    """Aggregate ``(archetype, event_type, count)`` for the weekly report."""

    statement = select(
        AuthoredSlideEditorEvent.archetype,
        AuthoredSlideEditorEvent.event_type,
        func.count(),
    ).group_by(
        AuthoredSlideEditorEvent.archetype, AuthoredSlideEditorEvent.event_type
    )
    if since is not None:
        statement = statement.where(AuthoredSlideEditorEvent.created_at >= since)
    result = await sql_session.execute(statement)
    return [(row[0], row[1], int(row[2])) for row in result.all()]


__all__ = [
    "count_editor_events_by_archetype",
    "editor_event_key_hash",
    "record_authored_slide_editor_event",
]
