"""Add durable deletion cleanup and immutable attachment snapshots.

Revision ID: f8b0d3e5a7c9
Revises: e7a9c2d4f6b8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8b0d3e5a7c9"
down_revision: str | None = "e7a9c2d4f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ATTACHMENTS = "workspace_message_attachments"
ASSETS = "workspace_assets"
CLEANUP_JOURNAL = "workspace_cleanup_journal"


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _attachment_snapshot_columns() -> list[sa.Column]:
    return [
        sa.Column("asset_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("asset_sha256", sa.String(length=64), nullable=True),
        sa.Column("asset_failure_code", sa.String(length=128), nullable=True),
        sa.Column("asset_failure_detail", sa.JSON(), nullable=True),
    ]


def _create_cleanup_journal() -> None:
    op.create_table(
        CLEANUP_JOURNAL,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        # Snapshot identifiers deliberately have no foreign keys.  Cleanup
        # intent must outlive presentation and account cascades.
        sa.Column("owner_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("presentation_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("operation_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("storage_namespace", sa.String(length=64), nullable=False),
        sa.Column("resource_kind", sa.String(length=128), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("resource_metadata", sa.JSON(), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("cleanup_token", sa.String(length=128), nullable=True),
        sa.Column(
            "cleanup_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cleanup_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
        sa.Column(
            "fencing_token",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "redrive_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_redriven_at", sa.DateTime(timezone=True), nullable=True),
        # Audit snapshot deliberately has no FK so it survives administrator
        # and target-account deletion.
        sa.Column("last_redriven_by_snapshot", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'retry_wait', 'succeeded', 'dead_letter')",
            name="ck_workspace_cleanup_journal_status",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_workspace_cleanup_journal_size",
        ),
        sa.CheckConstraint(
            "cleanup_attempts >= 0",
            name="ck_workspace_cleanup_journal_attempts",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_workspace_cleanup_journal_max_attempts",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0",
            name="ck_workspace_cleanup_journal_fencing_token",
        ),
        sa.CheckConstraint(
            "redrive_count >= 0",
            name="ck_workspace_cleanup_journal_redrive_count",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_workspace_cleanup_journal_idempotency",
        ),
    )
    op.create_index(
        "ix_workspace_cleanup_journal_dispatch",
        CLEANUP_JOURNAL,
        ["status", "next_attempt_at", "cleanup_lease_expires_at"],
    )
    op.create_index(
        "ix_workspace_cleanup_journal_presentation",
        CLEANUP_JOURNAL,
        ["presentation_id_snapshot", "created_at"],
    )
    op.create_index(
        "ix_workspace_cleanup_journal_owner",
        CLEANUP_JOURNAL,
        ["owner_id_snapshot", "created_at"],
    )


def upgrade() -> None:
    if _table_exists("presentations"):
        with op.batch_alter_table("presentations") as batch:
            batch.add_column(
                sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch.add_column(
                sa.Column("deletion_operation_id", sa.Uuid(), nullable=True)
            )

    if _table_exists(ATTACHMENTS):
        if not _table_exists(ASSETS):
            raise RuntimeError(
                "workspace attachment snapshot migration requires workspace_assets"
            )
        missing_live_asset = op.get_bind().execute(
            sa.text(
                f"SELECT COUNT(*) FROM {ATTACHMENTS} AS attachment "
                f"LEFT JOIN {ASSETS} AS asset ON asset.id = attachment.asset_id "
                "WHERE attachment.asset_id IS NULL OR asset.id IS NULL"
            )
        ).scalar_one()
        if missing_live_asset:
            raise RuntimeError(
                "workspace attachment snapshot backfill found a missing live asset"
            )
        with op.batch_alter_table(ATTACHMENTS) as batch:
            for column in _attachment_snapshot_columns():
                batch.add_column(column)

        # Correlated scalar subqueries are supported by both SQLite and
        # PostgreSQL and preserve the exact at-attachment hash/failure values.
        op.execute(
            sa.text(
                f"UPDATE {ATTACHMENTS} AS attachment SET "
                "asset_id_snapshot = attachment.asset_id, "
                "asset_sha256 = ("
                f"SELECT asset.sha256 FROM {ASSETS} AS asset "
                "WHERE asset.id = attachment.asset_id"
                "), "
                "asset_failure_code = ("
                f"SELECT asset.failure_code FROM {ASSETS} AS asset "
                "WHERE asset.id = attachment.asset_id"
                "), "
                "asset_failure_detail = ("
                f"SELECT asset.failure_detail FROM {ASSETS} AS asset "
                "WHERE asset.id = attachment.asset_id"
                ")"
            )
        )
        missing_snapshot = op.get_bind().execute(
            sa.text(
                f"SELECT COUNT(*) FROM {ATTACHMENTS} "
                "WHERE asset_id_snapshot IS NULL"
            )
        ).scalar_one()
        if missing_snapshot:
            raise RuntimeError(
                "workspace attachment snapshot backfill found a missing live asset"
            )

        with op.batch_alter_table(ATTACHMENTS) as batch:
            batch.drop_constraint(
                "uq_workspace_message_attachments_asset", type_="unique"
            )
            batch.drop_constraint(
                "fk_workspace_message_attachments_asset_id", type_="foreignkey"
            )
            batch.alter_column(
                "asset_id",
                existing_type=sa.Uuid(),
                existing_nullable=False,
                nullable=True,
            )
            batch.alter_column(
                "asset_id_snapshot",
                existing_type=sa.Uuid(),
                existing_nullable=True,
                nullable=False,
            )
            batch.create_foreign_key(
                "fk_workspace_message_attachments_asset_id",
                ASSETS,
                ["asset_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_unique_constraint(
                "uq_workspace_message_attachments_asset",
                ["message_id", "asset_id_snapshot"],
            )

    if not _table_exists(CLEANUP_JOURNAL):
        _create_cleanup_journal()


def downgrade() -> None:
    if _table_exists(CLEANUP_JOURNAL):
        op.drop_table(CLEANUP_JOURNAL)

    if _table_exists(ATTACHMENTS):
        # Restore live pointers where possible.  Refuse an information-losing
        # downgrade once the asset row is gone: the previous schema cannot
        # represent a historical attachment without a live asset.
        op.execute(
            sa.text(
                f"UPDATE {ATTACHMENTS} AS attachment SET asset_id = asset_id_snapshot "
                "WHERE asset_id IS NULL AND EXISTS ("
                f"SELECT 1 FROM {ASSETS} AS asset "
                "WHERE asset.id = attachment.asset_id_snapshot"
                ")"
            )
        )
        missing_live_asset = op.get_bind().execute(
            sa.text(
                f"SELECT COUNT(*) FROM {ATTACHMENTS} WHERE asset_id IS NULL"
            )
        ).scalar_one()
        if missing_live_asset:
            raise RuntimeError(
                "cannot downgrade immutable attachments after their live assets were removed"
            )

        with op.batch_alter_table(ATTACHMENTS) as batch:
            batch.drop_constraint(
                "uq_workspace_message_attachments_asset", type_="unique"
            )
            batch.drop_constraint(
                "fk_workspace_message_attachments_asset_id", type_="foreignkey"
            )
            batch.alter_column(
                "asset_id",
                existing_type=sa.Uuid(),
                existing_nullable=True,
                nullable=False,
            )
            batch.create_foreign_key(
                "fk_workspace_message_attachments_asset_id",
                ASSETS,
                ["asset_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_unique_constraint(
                "uq_workspace_message_attachments_asset",
                ["message_id", "asset_id"],
            )
            batch.drop_column("asset_failure_detail")
            batch.drop_column("asset_failure_code")
            batch.drop_column("asset_sha256")
            batch.drop_column("asset_id_snapshot")

    if _table_exists("presentations"):
        with op.batch_alter_table("presentations") as batch:
            batch.drop_column("deletion_operation_id")
            batch.drop_column("deleted_at")
