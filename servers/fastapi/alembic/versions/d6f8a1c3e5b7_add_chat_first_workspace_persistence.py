"""Add chat-first workspace persistence and publication fencing.

Revision ID: d6f8a1c3e5b7
Revises: c5e7f9a1b3d4
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6f8a1c3e5b7"
down_revision: Union[str, None] = "c5e7f9a1b3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT_CURSOR = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _owner(table: str) -> sa.Column:
    return sa.Column(
        "owner_id",
        sa.Uuid(),
        sa.ForeignKey("user.id", name=f"fk_{table}_owner_id", ondelete="CASCADE"),
        nullable=True,
    )


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "presentations" in existing_tables:
        with op.batch_alter_table("presentations") as batch:
            batch.add_column(sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")))
            batch.create_check_constraint("ck_presentations_revision_nonnegative", "revision >= 0")

    op.create_table(
        "workspace_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_conversations"),
        sa.Column("owner_scope", sa.String(36), nullable=False, server_default="anonymous"),
        sa.Column("presentation_id", sa.Uuid(), sa.ForeignKey("presentations.id", name="fk_workspace_conversations_presentation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("next_position", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("event_retention_floor_cursor", EVENT_CURSOR, nullable=False, server_default=sa.text("0")),
        *_timestamps(),
        sa.CheckConstraint("next_position >= 1", name="ck_workspace_conversations_next_position"),
        sa.CheckConstraint("version >= 0", name="ck_workspace_conversations_version"),
        sa.CheckConstraint("event_retention_floor_cursor >= 0", name="ck_workspace_conversations_event_retention_floor_cursor"),
        sa.UniqueConstraint("owner_scope", "presentation_id", name="uq_workspace_conversations_scope_presentation"),
    )
    op.create_index("ix_workspace_conversations_owner_presentation_updated", "workspace_conversations", ["owner_id", "presentation_id", "updated_at"])

    op.create_table(
        "workspace_messages",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_messages"),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("workspace_conversations.id", name="fk_workspace_messages_conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("position >= 1", name="ck_workspace_messages_position"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="ck_workspace_messages_role"),
        sa.UniqueConstraint("conversation_id", "position", name="uq_workspace_messages_conversation_position"),
    )
    op.create_index("ix_workspace_messages_conversation_id", "workspace_messages", ["conversation_id"])
    op.create_index("ix_workspace_messages_owner_created", "workspace_messages", ["owner_id", "created_at"])

    op.create_table(
        "workspace_assets",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_assets"),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("workspace_conversations.id", name="fk_workspace_assets_conversation_id", ondelete="CASCADE"), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="uploaded"),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("failure_detail", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("state IN ('uploaded', 'scanning', 'processing', 'ready', 'failed', 'quarantined', 'expired', 'deleted')", name="ck_workspace_assets_state"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_workspace_assets_size"),
        sa.CheckConstraint("version >= 0", name="ck_workspace_assets_version"),
        sa.UniqueConstraint("storage_key", name="uq_workspace_assets_storage_key"),
    )
    op.create_index("ix_workspace_assets_owner_state_updated", "workspace_assets", ["owner_id", "state", "updated_at"])
    op.create_index("ix_workspace_assets_expiry", "workspace_assets", ["state", "expires_at"])

    op.create_table(
        "workspace_message_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_message_attachments"),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("workspace_messages.id", name="fk_workspace_message_attachments_message_id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("workspace_assets.id", name="fk_workspace_message_attachments_asset_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("asset_state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ordinal >= 0", name="ck_workspace_message_attachments_ordinal"),
        sa.CheckConstraint("asset_state IN ('uploaded', 'scanning', 'processing', 'ready', 'failed', 'quarantined', 'expired', 'deleted')", name="ck_workspace_message_attachments_asset_state"),
        sa.UniqueConstraint("message_id", "asset_id", name="uq_workspace_message_attachments_asset"),
        sa.UniqueConstraint("message_id", "ordinal", name="uq_workspace_message_attachments_ordinal"),
    )
    op.create_index("ix_workspace_message_attachments_asset", "workspace_message_attachments", ["asset_id"])

    op.create_table(
        "workspace_operations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_operations"),
        sa.Column("owner_scope", sa.String(36), nullable=False, server_default="anonymous"),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("workspace_conversations.id", name="fk_workspace_operations_conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), sa.ForeignKey("presentations.id", name="fk_workspace_operations_presentation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("retry_of_operation_id", sa.Uuid(), sa.ForeignKey("workspace_operations.id", name="fk_workspace_operations_retry_of_operation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("anchor_position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("expected_revision", sa.BigInteger(), nullable=True),
        sa.Column("result_revision", sa.BigInteger(), nullable=True),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("attempt_token", sa.String(128), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("backoff_seconds", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_detail", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("state IN ('queued', 'waiting_for_assets', 'blocked', 'running', 'approval_required', 'awaiting_approval', 'cancelling', 'succeeded', 'failed', 'cancelled', 'dead_letter')", name="ck_workspace_operations_state"),
        sa.CheckConstraint("anchor_position >= 1", name="ck_workspace_operations_anchor_position"),
        sa.CheckConstraint("attempt_number >= 0", name="ck_workspace_operations_attempt_number"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_workspace_operations_fencing_token"),
        sa.CheckConstraint("retry_count >= 0", name="ck_workspace_operations_retry_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_workspace_operations_max_attempts"),
        sa.CheckConstraint("version >= 0", name="ck_workspace_operations_version"),
        sa.CheckConstraint("next_event_sequence >= 1", name="ck_workspace_operations_next_event_sequence"),
        sa.UniqueConstraint("conversation_id", "anchor_position", name="uq_workspace_operations_conversation_position"),
        sa.UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "kind",
            "idempotency_key_hash",
            name="uq_workspace_operations_command_idempotency",
        ),
        sa.UniqueConstraint("retry_of_operation_id", name="uq_workspace_operations_retry_of"),
    )
    op.create_index("ix_workspace_operations_conversation_id", "workspace_operations", ["conversation_id"])
    op.create_index("ix_workspace_operations_presentation_id", "workspace_operations", ["presentation_id"])
    op.create_index("ix_workspace_operations_dispatch", "workspace_operations", ["state", "next_attempt_at", "lease_expires_at"])
    op.create_index("ix_workspace_operations_owner_created", "workspace_operations", ["owner_id", "created_at"])

    op.create_table(
        "workspace_operation_events",
        sa.Column("cursor", EVENT_CURSOR, primary_key=True, autoincrement=True, nullable=False),
        _owner("workspace_operation_events"),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("workspace_operations.id", name="fk_workspace_operation_events_operation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("operation_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("sequence >= 1", name="ck_workspace_operation_events_sequence"),
        sa.CheckConstraint("operation_version >= 0", name="ck_workspace_operation_events_operation_version"),
        sa.UniqueConstraint("operation_id", "sequence", name="uq_workspace_operation_events_operation_sequence"),
    )
    op.create_index("ix_workspace_operation_events_operation_cursor", "workspace_operation_events", ["operation_id", "cursor"])
    op.create_index("ix_workspace_operation_events_owner_cursor", "workspace_operation_events", ["owner_id", "cursor"])

    op.create_table(
        "workspace_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_outbox"),
        sa.Column("owner_scope", sa.String(36), nullable=False, server_default="anonymous"),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("workspace_operations.id", name="fk_workspace_outbox_operation_id", ondelete="CASCADE"), nullable=True),
        sa.Column("event_cursor", EVENT_CURSOR, sa.ForeignKey("workspace_operation_events.cursor", name="fk_workspace_outbox_event_cursor", ondelete="CASCADE"), nullable=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('pending', 'delivering', 'delivered', 'failed', 'dead_letter')", name="ck_workspace_outbox_status"),
        sa.CheckConstraint("delivery_attempts >= 0", name="ck_workspace_outbox_delivery_attempts"),
        sa.UniqueConstraint("owner_scope", "idempotency_key_hash", name="uq_workspace_outbox_scope_idempotency"),
    )
    op.create_index("ix_workspace_outbox_delivery", "workspace_outbox", ["status", "available_at", "lease_expires_at"])

    op.create_table(
        "workspace_action_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_action_receipts"),
        sa.Column("owner_scope", sa.String(36), nullable=False, server_default="anonymous"),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("workspace_operations.id", name="fk_workspace_action_receipts_operation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("result_operation_id", sa.Uuid(), sa.ForeignKey("workspace_operations.id", name="fk_workspace_action_receipts_result_operation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_id", sa.String(128), nullable=False),
        sa.Column("action_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="accepted"),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("result_revision", sa.BigInteger(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('accepted', 'applied', 'rejected', 'duplicate', 'failed')", name="ck_workspace_action_receipts_status"),
        sa.UniqueConstraint(
            "owner_scope",
            "operation_id",
            "action_type",
            "idempotency_key_hash",
            name="uq_workspace_action_receipts_action_idempotency",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "action_type",
            "action_id",
            name="uq_workspace_action_receipts_operation_action",
        ),
    )
    op.create_index("ix_workspace_action_receipts_operation_created", "workspace_action_receipts", ["operation_id", "created_at"])

    op.create_table(
        "workspace_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        _owner("workspace_artifacts"),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("workspace_operations.id", name="fk_workspace_artifacts_operation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), sa.ForeignKey("presentations.id", name="fk_workspace_artifacts_presentation_id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="staging"),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_token", sa.String(128), nullable=True),
        sa.Column("cleanup_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cleanup_error", sa.JSON(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('staging', 'ready', 'cleanup_pending', 'failed', 'deleted')", name="ck_workspace_artifacts_status"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_workspace_artifacts_size"),
        sa.CheckConstraint("cleanup_attempts >= 0", name="ck_workspace_artifacts_cleanup_attempts"),
        sa.UniqueConstraint("storage_key", name="uq_workspace_artifacts_storage_key"),
    )
    op.create_index("ix_workspace_artifacts_cleanup", "workspace_artifacts", ["status", "expires_at", "cleanup_lease_expires_at"])
    op.create_index("ix_workspace_artifacts_operation", "workspace_artifacts", ["operation_id"])

    # Keep this additive operation out of SQLite batch mode. Older supported
    # installations can contain a deliberately incomplete referenced-table
    # graph; reflecting the pre-existing job table would then fail before the
    # nullable bridge column can be added.
    if "presentation_generation_jobs" not in existing_tables:
        return
    if op.get_bind().dialect.name == "sqlite":
        # SQLite accepts a REFERENCES clause on ADD COLUMN but Alembic's
        # generic implementation attempts a second ALTER CONSTRAINT, which
        # SQLite does not support. The inline form preserves the FK without
        # reflecting the legacy table.
        op.execute(
            sa.text(
                "ALTER TABLE presentation_generation_jobs "
                "ADD COLUMN workspace_operation_id CHAR(32) "
                "CONSTRAINT fk_presentation_generation_jobs_workspace_operation_id "
                "REFERENCES workspace_operations (id) ON DELETE SET NULL"
            )
        )
    else:
        op.add_column(
            "presentation_generation_jobs",
            sa.Column(
                "workspace_operation_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "workspace_operations.id",
                    name="fk_presentation_generation_jobs_workspace_operation_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )
    op.create_index(
        "ix_presentation_generation_jobs_workspace_operation_id",
        "presentation_generation_jobs",
        ["workspace_operation_id"],
        unique=True,
    )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "presentation_generation_jobs" in existing_tables:
        job_columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns(
                "presentation_generation_jobs"
            )
        }
        if "workspace_operation_id" in job_columns:
            op.drop_index(
                "ix_presentation_generation_jobs_workspace_operation_id",
                table_name="presentation_generation_jobs",
            )
            op.drop_column("presentation_generation_jobs", "workspace_operation_id")
    for table in (
        "workspace_artifacts",
        "workspace_action_receipts",
        "workspace_outbox",
        "workspace_operation_events",
        "workspace_operations",
        "workspace_message_attachments",
        "workspace_assets",
        "workspace_messages",
        "workspace_conversations",
    ):
        op.drop_table(table)
    if "presentations" in existing_tables:
        presentation_columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns("presentations")
        }
        if "revision" in presentation_columns:
            with op.batch_alter_table("presentations") as batch:
                batch.drop_constraint("ck_presentations_revision_nonnegative", type_="check")
                batch.drop_column("revision")
