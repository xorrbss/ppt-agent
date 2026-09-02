"""Add the authored-slide editor event ledger.

Revision ID: f2b6d8a0c4e7
Revises: e1a7c9d4b2f8
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f2b6d8a0c4e7"
down_revision: str | None = "e1a7c9d4b2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVENTS = "authored_slide_editor_events"
EVENT_TYPES = (
    "slide_regenerated",
    "archetype_switched",
    "bulk_text_edit",
    "slide_deleted",
    "export_completed",
)
_EVENT_TYPE_SQL = ", ".join(f"'{name}'" for name in EVENT_TYPES)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if EVENTS in set(inspector.get_table_names()):
        return
    op.create_table(
        EVENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("slide_index", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("archetype", sa.String(length=32), nullable=True),
        sa.Column("previous_archetype", sa.String(length=32), nullable=True),
        sa.Column("changed_characters", sa.Integer(), nullable=True),
        sa.Column("event_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"event_type IN ({_EVENT_TYPE_SQL})",
            name="ck_authored_slide_editor_events_type",
        ),
        sa.CheckConstraint(
            "(event_type IN ('export_completed')) OR slide_index IS NOT NULL",
            name="ck_authored_slide_editor_events_slide_scope",
        ),
        sa.CheckConstraint(
            "slide_index IS NULL OR slide_index >= 0",
            name="ck_authored_slide_editor_events_slide_index",
        ),
        sa.CheckConstraint(
            "changed_characters IS NULL OR changed_characters >= 0",
            name="ck_authored_slide_editor_events_changed_characters",
        ),
        sa.CheckConstraint(
            "event_type != 'archetype_switched' OR "
            "(archetype IS NOT NULL AND previous_archetype IS NOT NULL)",
            name="ck_authored_slide_editor_events_switch_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            name="fk_authored_slide_editor_events_presentation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_key_hash", name="uq_authored_slide_editor_events_key_hash"
        ),
    )
    op.create_index(
        "ix_authored_slide_editor_events_owner_id", EVENTS, ["owner_id"], unique=False
    )
    op.create_index(
        "ix_authored_slide_editor_events_created_at",
        EVENTS,
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_authored_slide_editor_events_archetype_window",
        EVENTS,
        ["archetype", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_authored_slide_editor_events_presentation",
        EVENTS,
        ["presentation_id", "slide_index", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if EVENTS not in set(inspector.get_table_names()):
        return
    for index in (
        "ix_authored_slide_editor_events_presentation",
        "ix_authored_slide_editor_events_archetype_window",
        "ix_authored_slide_editor_events_created_at",
        "ix_authored_slide_editor_events_owner_id",
    ):
        op.drop_index(index, table_name=EVENTS)
    op.drop_table(EVENTS)
