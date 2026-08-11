"""Add durable deletion cleanup and immutable attachment snapshots.

Revision ID: f8b0d3e5a7c9
Revises: e7a9c2d4f6b8
"""

from collections.abc import Sequence
import re

import sqlalchemy as sa

from alembic import op

revision: str = "f8b0d3e5a7c9"
down_revision: str | None = "e7a9c2d4f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ATTACHMENTS = "workspace_message_attachments"
ASSETS = "workspace_assets"
CLEANUP_JOURNAL = "workspace_cleanup_journal"


def _normalized_check(value: object) -> str:
    normalized = str(value or "").lower().replace('"', "")
    normalized = re.sub(r"::[a-z ]+(?:\[\])?", "", normalized)
    return re.sub(r"[\s()]", "", normalized)


def _check_matches(name: str, actual: object, expected: str) -> bool:
    normalized = _normalized_check(actual)
    if name != "ck_workspace_cleanup_journal_status":
        return normalized == _normalized_check(expected)
    expected_values = {"pending", "claimed", "retry_wait", "succeeded", "dead_letter"}
    actual_values = set(re.findall(r"'([^']+)'", str(actual or "").lower()))
    if actual_values != expected_values or "status" not in normalized:
        return False
    # SQLite preserves IN; PostgreSQL commonly reflects it as = ANY(ARRAY[]).
    return ("statusin" in normalized) or (
        "status=any" in normalized and "or" not in normalized
    )


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> dict[str, dict]:
    return {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns(table)}


def _attachment_snapshot_columns() -> list[sa.Column]:
    return [
        sa.Column("asset_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("asset_sha256", sa.String(length=64), nullable=True),
        sa.Column("asset_failure_code", sa.String(length=128), nullable=True),
        sa.Column("asset_failure_detail", sa.JSON(), nullable=True),
    ]


def _cleanup_journal_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("cleanup_token", sa.String(length=128), nullable=True),
        sa.Column("cleanup_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("8")),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("redrive_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_redriven_at", sa.DateTime(timezone=True), nullable=True),
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
    ]


def _attachment_constraint_state() -> tuple[str, str]:
    inspector = sa.inspect(op.get_bind())
    unique_name = "uq_workspace_message_attachments_asset"
    unique = next(
        (
            constraint
            for constraint in inspector.get_unique_constraints(ATTACHMENTS)
            if constraint.get("name") == unique_name
        ),
        None,
    )
    unique_columns = tuple(unique.get("column_names") or ()) if unique else ()
    if unique is None:
        unique_state = "missing"
    elif unique_columns == ("message_id", "asset_id"):
        unique_state = "legacy"
    elif unique_columns == ("message_id", "asset_id_snapshot"):
        unique_state = "target"
    else:
        raise RuntimeError(f"{unique_name} has unexpected columns {unique_columns!r}")

    foreign_key_name = "fk_workspace_message_attachments_asset_id"
    foreign_key = next(
        (
            constraint
            for constraint in inspector.get_foreign_keys(ATTACHMENTS)
            if constraint.get("name") == foreign_key_name
        ),
        None,
    )
    if foreign_key is None:
        foreign_key_state = "missing"
    else:
        constrained_columns = tuple(foreign_key.get("constrained_columns") or ())
        referred_table = foreign_key.get("referred_table")
        ondelete = (foreign_key.get("options") or {}).get("ondelete")
        ondelete = ondelete.upper() if ondelete else "RESTRICT"
        if constrained_columns != ("asset_id",) or referred_table != ASSETS:
            raise RuntimeError(f"{foreign_key_name} has an unexpected attachment target")
        if ondelete in {"RESTRICT", "NO ACTION"}:
            foreign_key_state = "legacy"
        elif ondelete == "SET NULL":
            foreign_key_state = "target"
        else:
            raise RuntimeError(f"{foreign_key_name} has unexpected ON DELETE {ondelete!r}")
    return unique_state, foreign_key_state


def _upgrade_attachments() -> None:
    if not _table_exists(ASSETS):
        raise RuntimeError("workspace attachment snapshot migration requires workspace_assets")

    existing_columns = _columns(ATTACHMENTS)
    required_legacy_columns = {"asset_id", "message_id"}
    if missing := required_legacy_columns - existing_columns.keys():
        raise RuntimeError(
            "workspace attachment snapshot migration found a partially applied "
            f"legacy table missing columns {sorted(missing)!r}"
        )

    missing_live_asset = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT COUNT(*) FROM {ATTACHMENTS} AS attachment "
                f"LEFT JOIN {ASSETS} AS asset ON asset.id = attachment.asset_id "
                "WHERE attachment.asset_id IS NOT NULL AND asset.id IS NULL"
            )
        )
        .scalar_one()
    )
    if missing_live_asset:
        raise RuntimeError("workspace attachment snapshot backfill found a missing live asset")

    for column in _attachment_snapshot_columns():
        if column.name not in existing_columns:
            op.add_column(ATTACHMENTS, column)

    # Only rows that still have a live pointer need recovery.  Rows whose live
    # asset was legitimately deleted after a previous successful run already
    # carry their immutable snapshot and must remain untouched.
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
            ") WHERE attachment.asset_id_snapshot IS NULL "
            "AND attachment.asset_id IS NOT NULL"
        )
    )
    missing_snapshot = (
        op.get_bind()
        .execute(sa.text(f"SELECT COUNT(*) FROM {ATTACHMENTS} WHERE asset_id_snapshot IS NULL"))
        .scalar_one()
    )
    if missing_snapshot:
        raise RuntimeError("workspace attachment snapshot backfill found a missing live asset")

    unique_state, foreign_key_state = _attachment_constraint_state()
    columns = _columns(ATTACHMENTS)
    asset_id_nullable = bool(columns["asset_id"]["nullable"])
    snapshot_nullable = bool(columns["asset_id_snapshot"]["nullable"])
    if unique_state == "target" and foreign_key_state == "target" and asset_id_nullable and not snapshot_nullable:
        return

    with op.batch_alter_table(ATTACHMENTS) as batch:
        if unique_state == "legacy":
            batch.drop_constraint("uq_workspace_message_attachments_asset", type_="unique")
        if foreign_key_state == "legacy":
            batch.drop_constraint("fk_workspace_message_attachments_asset_id", type_="foreignkey")
        if not asset_id_nullable:
            batch.alter_column(
                "asset_id",
                existing_type=sa.Uuid(),
                existing_nullable=False,
                nullable=True,
            )
        if snapshot_nullable:
            batch.alter_column(
                "asset_id_snapshot",
                existing_type=sa.Uuid(),
                existing_nullable=True,
                nullable=False,
            )
        if foreign_key_state != "target":
            batch.create_foreign_key(
                "fk_workspace_message_attachments_asset_id",
                ASSETS,
                ["asset_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if unique_state != "target":
            batch.create_unique_constraint(
                "uq_workspace_message_attachments_asset",
                ["message_id", "asset_id_snapshot"],
            )


def _ensure_cleanup_journal_contract() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns(CLEANUP_JOURNAL)}
    missing_columns = [column for column in _cleanup_journal_columns() if column.name not in existing_columns]
    if missing_columns:
        row_count = op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {CLEANUP_JOURNAL}")).scalar_one()
        unsafe = [
            column.name
            for column in missing_columns
            if row_count and not column.nullable and column.server_default is None
        ]
        if unsafe:
            raise RuntimeError(
                f"{CLEANUP_JOURNAL} is only partially applied; cannot safely backfill non-null columns {unsafe!r}"
            )
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(CLEANUP_JOURNAL, recreate="always") as batch:
                for column in missing_columns:
                    batch.add_column(column)
        else:
            for column in missing_columns:
                op.add_column(CLEANUP_JOURNAL, column)

    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(CLEANUP_JOURNAL)}
    primary_key = tuple(inspector.get_pk_constraint(CLEANUP_JOURNAL).get("constrained_columns") or ())
    if primary_key and primary_key != ("id",):
        raise RuntimeError(f"{CLEANUP_JOURNAL} has an unexpected primary key {primary_key!r}")

    expected_checks = {
        "ck_workspace_cleanup_journal_status": (
            "status IN ('pending', 'claimed', 'retry_wait', 'succeeded', 'dead_letter')"
        ),
        "ck_workspace_cleanup_journal_size": "size_bytes IS NULL OR size_bytes >= 0",
        "ck_workspace_cleanup_journal_attempts": "cleanup_attempts >= 0",
        "ck_workspace_cleanup_journal_max_attempts": "max_attempts >= 1",
        "ck_workspace_cleanup_journal_fencing_token": "fencing_token >= 0",
    }
    if "redrive_count" in columns:
        expected_checks["ck_workspace_cleanup_journal_redrive_count"] = "redrive_count >= 0"
    existing_checks = {
        constraint.get("name"): constraint.get("sqltext")
        for constraint in inspector.get_check_constraints(CLEANUP_JOURNAL)
    }
    for name, expected in expected_checks.items():
        if name in existing_checks and not _check_matches(
            name,
            existing_checks[name],
            expected,
        ):
            raise RuntimeError(f"{name} has an unexpected expression")
    existing_uniques = {
        constraint.get("name"): tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(CLEANUP_JOURNAL)
    }
    unique_name = "uq_workspace_cleanup_journal_idempotency"
    if unique_name in existing_uniques and existing_uniques[unique_name] != ("idempotency_key_hash",):
        raise RuntimeError(f"{unique_name} has unexpected columns")

    missing_checks = {
        name: condition
        for name, condition in expected_checks.items()
        if name not in existing_checks
    }
    unique_missing = unique_name not in existing_uniques
    if not primary_key or missing_checks or unique_missing:
        with op.batch_alter_table(CLEANUP_JOURNAL) as batch:
            if not primary_key:
                batch.create_primary_key(f"pk_{CLEANUP_JOURNAL}", ["id"])
            for name, condition in missing_checks.items():
                batch.create_check_constraint(name, condition)
            if unique_missing:
                batch.create_unique_constraint(unique_name, ["idempotency_key_hash"])

    expected_indexes = {
        "ix_workspace_cleanup_journal_dispatch": (
            "status",
            "next_attempt_at",
            "cleanup_lease_expires_at",
        ),
        "ix_workspace_cleanup_journal_presentation": (
            "presentation_id_snapshot",
            "created_at",
        ),
        "ix_workspace_cleanup_journal_owner": ("owner_id_snapshot", "created_at"),
    }
    indexes = {
        index.get("name"): tuple(index.get("column_names") or ())
        for index in sa.inspect(op.get_bind()).get_indexes(CLEANUP_JOURNAL)
    }
    for name, expected_columns in expected_indexes.items():
        if name in indexes and indexes[name] != expected_columns:
            raise RuntimeError(f"{name} has unexpected columns {indexes[name]!r}")
        if name not in indexes:
            op.create_index(name, CLEANUP_JOURNAL, list(expected_columns))


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
        presentation_columns = _columns("presentations")
        if "deleted_at" not in presentation_columns:
            op.add_column(
                "presentations",
                sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "deletion_operation_id" not in presentation_columns:
            op.add_column(
                "presentations",
                sa.Column("deletion_operation_id", sa.Uuid(), nullable=True),
            )

    if _table_exists(ATTACHMENTS):
        _upgrade_attachments()

    if not _table_exists(CLEANUP_JOURNAL):
        _create_cleanup_journal()
    else:
        _ensure_cleanup_journal_contract()


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
        missing_live_asset = (
            op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {ATTACHMENTS} WHERE asset_id IS NULL")).scalar_one()
        )
        if missing_live_asset:
            raise RuntimeError("cannot downgrade immutable attachments after their live assets were removed")

        with op.batch_alter_table(ATTACHMENTS) as batch:
            batch.drop_constraint("uq_workspace_message_attachments_asset", type_="unique")
            batch.drop_constraint("fk_workspace_message_attachments_asset_id", type_="foreignkey")
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
