"""Read a presentation's slides consistently inside an unflushed chat turn.

A workspace chat turn buffers its mutations: nothing is written to the database
until the fenced publication commits, so the turn's own SQL reads cannot see
its earlier edits.  Three things go stale:

* a pending insert is absent from the query result,
* a pending delete is still present in it,
* ``ORDER BY index`` (and ``WHERE index = ?``) reflect the pre-edit indices.

Reconciling the query result against the session's unit of work fixes all
three without emitting SQL.  Modified existing rows need no help - the ORM
identity map already returns the instance carrying the turn's changes.

Outside a buffered turn the session has no pending work and this is a no-op,
so both the workspace and the legacy chat paths share one behaviour.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from models.sql.slide import SlideModel


def reconcile_pending_slides(
    session: Any,
    presentation_id: uuid.UUID,
    rows: Iterable[SlideModel],
) -> list[SlideModel]:
    """Merge a slide query result with the session's uncommitted unit of work."""

    deleted_ids = {
        instance.id
        for instance in getattr(session, "deleted", ())
        if isinstance(instance, SlideModel)
    }
    merged = [row for row in rows if row.id not in deleted_ids]
    seen_ids = {row.id for row in merged}
    merged.extend(
        instance
        for instance in getattr(session, "new", ())
        if isinstance(instance, SlideModel)
        and instance.presentation == presentation_id
        and instance.id not in seen_ids
        and instance.id not in deleted_ids
    )
    # Sorting on the in-memory attribute is the point: the database still holds
    # the pre-shift indices until this turn publishes.
    return sorted(merged, key=lambda slide: slide.index)


def slide_at_index(slides: Iterable[SlideModel], index: int) -> SlideModel | None:
    """Address a slide by its in-turn index rather than by a stale SQL filter."""

    return next((slide for slide in slides if slide.index == index), None)
