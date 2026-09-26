"""Drop the Template V2 studio tables and the last of its sidecar columns.

Template V2 (structured templates, the template-preview studio, PPTX
import/generation) was removed entirely on 2026-09-25. This migration only
drops storage; it does not touch any earlier migration's history.

``downgrade()`` recreates the exact pre-removal shape of everything
``upgrade()`` drops. There is no live-data-loss scenario to guard against
here (unlike e.g. ``fa1b2c3d4e5f``'s account-deletion fence): ``upgrade()``
already destroyed any Template V2 data before a downgrade can ever run, so
the recreated tables are always empty and the round trip always succeeds.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "f432fc854225"
down_revision = "f1c3e5a7b9d2"
branch_labels = None
depends_on = None

_CHAT_TEMPLATE_FK = "fk_chat_history_messages_template_v2_id_template_v2"
_CHAT_TEMPLATE_INDEX = "ix_chat_history_messages_template_v2_id"
_TEMPLATE_PRESENTATION_FK = "fk_template_v2_presentation_id_presentations"
_TEMPLATE_PRESENTATION_INDEX = "ix_template_v2_presentation_id"
_LOCAL_STATE_TEMPLATE_FK = "fk_template_v2_local_state_template_id_template_v2"
_LOCAL_STATE_PRESENTATION_FK = (
    "fk_template_v2_local_state_presentation_id_presentations"
)
_LOCAL_STATE_PRESENTATION_INDEX = "ix_template_v2_local_state_presentation_id"
_LOCAL_STATE_REVISION_CHECK = "ck_template_v2_local_state_revision_positive"
_REVISIONS_TEMPLATE_FK = "fk_template_v2_revisions_template_id_template_v2"
_REVISIONS_TEMPLATE_CREATED_INDEX = "ix_template_v2_revisions_template_created"
_REVISIONS_REVISION_CHECK = "ck_template_v2_revisions_revision_positive"


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    inspector = sa.inspect(op.get_bind())
    return column in {row["name"] for row in inspector.get_columns(table)}


def upgrade() -> None:
    if _has_column("chat_history_messages", "template_v2_id"):
        with op.batch_alter_table("chat_history_messages") as batch:
            batch.drop_constraint(_CHAT_TEMPLATE_FK, type_="foreignkey")
            batch.drop_index(_CHAT_TEMPLATE_INDEX)
            batch.drop_column("template_v2_id")
    if _has_column("presentation_generation_jobs", "template_v2_target"):
        op.drop_column("presentation_generation_jobs", "template_v2_target")
    for table in (
        "template_v2_revisions",
        "template_v2_pptx_imports",
        "template_v2_local_state",
        "template_v2",
    ):
        if _has_table(table):
            op.drop_table(table)


def _recreate_template_v2() -> None:
    if _has_table("template_v2"):
        return
    op.create_table(
        "template_v2",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "description", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
        sa.Column("raw_layouts", sa.JSON(), nullable=True),
        sa.Column("components", sa.JSON(), nullable=True),
        sa.Column("merged_components", sa.JSON(), nullable=True),
        sa.Column("layouts", sa.JSON(), nullable=True),
        sa.Column("assets", sa.JSON(), nullable=True),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            name=_TEMPLATE_PRESENTATION_FK,
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        _TEMPLATE_PRESENTATION_INDEX, "template_v2", ["presentation_id"], unique=False
    )


def _recreate_template_v2_pptx_imports() -> None:
    if _has_table("template_v2_pptx_imports"):
        return
    op.create_table(
        "template_v2_pptx_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("requested_template_id", sa.String(), nullable=False),
        sa.Column("draft_template_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("source_filename", sa.String(), nullable=False),
        sa.Column("source_media_type", sa.String(), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(), nullable=False),
        sa.Column("source_storage_key", sa.String(), nullable=False),
        sa.Column(
            "pipeline_version",
            sa.String(),
            nullable=False,
            server_default="template-v2-pptx-ooxml-v1",
        ),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "attempt_number", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("attempt_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_retention_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("source_cleanup_token", sa.String(), nullable=True),
        sa.Column(
            "source_cleanup_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "source_cleanup_attempted_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("source_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "owner_scope",
            sa.String(length=64),
            nullable=False,
            server_default="local-disabled-auth-scope-v1",
        ),
        sa.Column("request_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("analysis_result", sa.JSON(), nullable=True),
        sa.Column("repeat_suggestions", sa.JSON(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["async_presentation_generation_tasks.id"],
            name="fk_template_v2_pptx_imports_task_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_template_id"],
            ["template_v2.id"],
            name="fk_template_v2_pptx_imports_draft_template_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_template_v2_pptx_imports_task_id"),
        sa.UniqueConstraint(
            "source_storage_key",
            name="uq_template_v2_pptx_imports_source_storage_key",
        ),
    )
    op.create_index(
        "ix_template_v2_pptx_imports_task_id",
        "template_v2_pptx_imports",
        ["task_id"],
        unique=True,
    )
    op.create_index(
        "ix_template_v2_pptx_imports_requested_template_id",
        "template_v2_pptx_imports",
        ["requested_template_id"],
        unique=False,
    )
    op.create_index(
        "ix_template_v2_pptx_imports_draft_template_id",
        "template_v2_pptx_imports",
        ["draft_template_id"],
        unique=False,
    )
    op.create_index(
        "ix_template_v2_pptx_imports_dispatch",
        "template_v2_pptx_imports",
        ["state", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_template_v2_pptx_imports_source_cleanup",
        "template_v2_pptx_imports",
        ["state", "source_deleted_at", "source_retention_expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_template_v2_pptx_imports_owner_request_key",
        "template_v2_pptx_imports",
        ["owner_scope", "request_key_hash"],
        unique=True,
    )


def _recreate_template_v2_local_state() -> None:
    if _has_table("template_v2_local_state"):
        return
    op.create_table(
        "template_v2_local_state",
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name=_LOCAL_STATE_REVISION_CHECK),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["template_v2.id"],
            name=_LOCAL_STATE_TEMPLATE_FK,
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            name=_LOCAL_STATE_PRESENTATION_FK,
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("template_id"),
    )
    op.create_index(
        _LOCAL_STATE_PRESENTATION_INDEX,
        "template_v2_local_state",
        ["presentation_id"],
        unique=False,
    )


def _recreate_template_v2_revisions() -> None:
    if _has_table("template_v2_revisions"):
        return
    op.create_table(
        "template_v2_revisions",
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("merged_components", sa.JSON(), nullable=True),
        sa.Column("layouts", sa.JSON(), nullable=True),
        sa.Column("assets", sa.JSON(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name=_REVISIONS_REVISION_CHECK),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["template_v2.id"],
            name=_REVISIONS_TEMPLATE_FK,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("template_id", "revision"),
    )
    op.create_index(
        _REVISIONS_TEMPLATE_CREATED_INDEX,
        "template_v2_revisions",
        ["template_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    _recreate_template_v2()
    _recreate_template_v2_pptx_imports()
    _recreate_template_v2_local_state()
    _recreate_template_v2_revisions()

    if not _has_column("presentation_generation_jobs", "template_v2_target"):
        op.add_column(
            "presentation_generation_jobs",
            sa.Column("template_v2_target", sa.JSON(), nullable=True),
        )
    if not _has_column("chat_history_messages", "template_v2_id"):
        with op.batch_alter_table("chat_history_messages") as batch:
            batch.add_column(
                sa.Column(
                    "template_v2_id",
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=True,
                )
            )
            batch.create_foreign_key(
                _CHAT_TEMPLATE_FK,
                "template_v2",
                ["template_v2_id"],
                ["id"],
                ondelete="CASCADE",
            )
        op.create_index(
            _CHAT_TEMPLATE_INDEX,
            "chat_history_messages",
            ["template_v2_id"],
            unique=False,
        )
