"""Per-slide editor events for the infographic measurement loop.

One table, append-only.  It answers a single question the automatic metrics
cannot: after a deck ships, which figures does a human keep and which does a
human fix?  A slide regenerated, an archetype switched away from, a page
rewritten wholesale or deleted are all edit signals against the archetype the
plan chose; an export tells us the deck was actually used.

The row is deliberately scalar.  It carries no slide HTML, no prompt, no edited
text -- only which archetype the event was about and how large the edit was.
Owner scoping is applied centrally by ``services/database.py``'s
``do_orm_execute`` loader-criteria hook and its ``before_flush`` stamper, so no
query in this codebase filters ``owner_id`` by hand.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime

#: The closed vocabulary of measurable editor actions.  Extending it requires a
#: migration, which is the point: an open string column would silently collect
#: whatever a future client invented and make every historical rate wrong.
AUTHORED_SLIDE_EDITOR_EVENT_TYPES: tuple[str, ...] = (
    "slide_regenerated",
    "archetype_switched",
    "bulk_text_edit",
    "slide_deleted",
    "export_completed",
)

#: ``export_completed`` is the one deck-scoped event; every other event names a
#: slide.  Enforced by a check constraint rather than convention.
_DECK_SCOPED_EVENT_TYPES: tuple[str, ...] = ("export_completed",)

_EVENT_TYPE_SQL = ", ".join(f"'{name}'" for name in AUTHORED_SLIDE_EDITOR_EVENT_TYPES)
_DECK_SCOPED_SQL = ", ".join(f"'{name}'" for name in _DECK_SCOPED_EVENT_TYPES)


class AuthoredSlideEditorEvent(SQLModel, table=True):
    """One recorded editor action against one slide (or the whole deck)."""

    __tablename__ = "authored_slide_editor_events"
    __table_args__ = (
        UniqueConstraint(
            "event_key_hash", name="uq_authored_slide_editor_events_key_hash"
        ),
        CheckConstraint(
            f"event_type IN ({_EVENT_TYPE_SQL})",
            name="ck_authored_slide_editor_events_type",
        ),
        CheckConstraint(
            f"(event_type IN ({_DECK_SCOPED_SQL})) OR slide_index IS NOT NULL",
            name="ck_authored_slide_editor_events_slide_scope",
        ),
        CheckConstraint(
            "slide_index IS NULL OR slide_index >= 0",
            name="ck_authored_slide_editor_events_slide_index",
        ),
        CheckConstraint(
            "changed_characters IS NULL OR changed_characters >= 0",
            name="ck_authored_slide_editor_events_changed_characters",
        ),
        CheckConstraint(
            "event_type != 'archetype_switched' OR "
            "(archetype IS NOT NULL AND previous_archetype IS NOT NULL)",
            name="ck_authored_slide_editor_events_switch_evidence",
        ),
        Index(
            "ix_authored_slide_editor_events_archetype_window",
            "archetype",
            "event_type",
            "created_at",
        ),
        Index(
            "ix_authored_slide_editor_events_presentation",
            "presentation_id",
            "slide_index",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    # Stamped centrally on flush; never set or filtered by a call site.
    owner_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(Uuid, nullable=True, index=True)
    )
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentations.id",
                name="fk_authored_slide_editor_events_presentation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    #: NULL only for a deck-scoped event.
    slide_index: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    event_type: str = Field(sa_column=Column(String(32), nullable=False))
    #: The archetype the slide carried when the event happened; for
    #: ``archetype_switched`` this is the archetype switched TO.
    archetype: str | None = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    previous_archetype: str | None = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    #: How much text a ``bulk_text_edit`` moved, as a character count.  It is
    #: the size of the edit, not its content.
    changed_characters: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    #: SHA-256 of the caller's idempotency identity.  A double-clicked editor
    #: button must not inflate the edit rate of an archetype.
    event_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
            index=True,
        ),
    )


__all__ = [
    "AUTHORED_SLIDE_EDITOR_EVENT_TYPES",
    "AuthoredSlideEditorEvent",
]
