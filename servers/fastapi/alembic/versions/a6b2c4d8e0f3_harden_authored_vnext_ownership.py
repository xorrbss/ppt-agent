"""Harden authored-vNext ownership and account-deletion cascades.

Revision ID: a6b2c4d8e0f3
Revises: f5a1c3e7b9d2
"""

from collections.abc import Sequence
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "a6b2c4d8e0f3"
down_revision: str | None = "f5a1c3e7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "authored_vnext_master_revisions",
    "authored_vnext_slide_revisions",
    "authored_vnext_deck_revisions",
    "authored_vnext_entity_heads",
    "authored_vnext_candidates",
    "authored_vnext_quality_decisions",
    "authored_vnext_export_jobs",
    "authored_vnext_export_artifact_receipts",
    "authored_vnext_render_receipts",
    "authored_vnext_publication_pointers",
)
ORIGINAL_OWNER_TABLES = frozenset(
    {
        "authored_vnext_master_revisions",
        "authored_vnext_slide_revisions",
        "authored_vnext_deck_revisions",
        "authored_vnext_export_jobs",
    }
)


def _uuid(value: object) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError("authored_vnext_owner_backfill_requires_uuid_scope") from error


def _database_uuid(bind: sa.Connection, value: uuid.UUID) -> object:
    return value.hex if bind.dialect.name == "sqlite" else value


def _add_missing_owner_columns(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    for table in TABLES:
        columns = {item["name"] for item in inspector.get_columns(table)}
        if "owner_id" not in columns:
            op.add_column(table, sa.Column("owner_id", sa.Uuid(), nullable=True))


def _backfill_owner_ids(bind: sa.Connection) -> None:
    metadata = sa.MetaData()
    user = sa.Table("user", metadata, autoload_with=bind)
    valid_users = {_uuid(value) for value in bind.execute(sa.select(user.c.id)).scalars()}
    scopes: dict[tuple[str, str], uuid.UUID] = {}

    reflected = {
        name: sa.Table(name, metadata, autoload_with=bind) for name in TABLES
    }
    for name in ORIGINAL_OWNER_TABLES:
        table = reflected[name]
        rows = bind.execute(
            sa.select(table.c.owner_scope, table.c.tenant_id, table.c.owner_id).where(
                table.c.owner_id.is_not(None)
            )
        )
        for owner_scope, tenant_id, raw_owner in rows:
            owner = _uuid(raw_owner)
            key = (str(owner_scope), str(tenant_id))
            previous = scopes.setdefault(key, owner)
            if previous != owner:
                raise RuntimeError("authored_vnext_owner_backfill_scope_is_ambiguous")

    for table in reflected.values():
        rows = bind.execute(
            sa.select(table.c.owner_scope, table.c.tenant_id).where(
                table.c.owner_id.is_(None)
            ).distinct()
        )
        for owner_scope, tenant_id in rows:
            key = (str(owner_scope), str(tenant_id))
            owner = scopes.get(key)
            if owner is None:
                owner = _uuid(owner_scope)
                scopes[key] = owner
            if owner not in valid_users:
                raise RuntimeError("authored_vnext_owner_backfill_user_missing")
            bind.execute(
                table.update()
                .where(
                    table.c.owner_id.is_(None),
                    table.c.owner_scope == owner_scope,
                    table.c.tenant_id == tenant_id,
                )
                .values(owner_id=_database_uuid(bind, owner))
            )

    for table in reflected.values():
        if bind.scalar(
            sa.select(sa.func.count()).select_from(table).where(table.c.owner_id.is_(None))
        ):
            raise RuntimeError("authored_vnext_owner_backfill_incomplete")


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if not set(TABLES).issubset(existing):
        raise RuntimeError("authored_vnext_ownership_requires_complete_schema")
    _add_missing_owner_columns(bind)
    _backfill_owner_ids(bind)

    export_jobs = "authored_vnext_export_jobs"
    export_columns = {item["name"] for item in sa.inspect(bind).get_columns(export_jobs)}
    if "account_deletion_generation" not in export_columns:
        op.add_column(
            export_jobs,
            sa.Column(
                "account_deletion_generation",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )
        jobs = sa.Table(export_jobs, sa.MetaData(), autoload_with=bind)
        users = sa.Table("user", sa.MetaData(), autoload_with=bind)
        for job_id, owner_id in bind.execute(sa.select(jobs.c.id, jobs.c.owner_id)):
            generation = bind.scalar(
                sa.select(users.c.deletion_generation).where(users.c.id == owner_id)
            )
            if generation is None:
                raise RuntimeError("authored_vnext_export_owner_missing")
            bind.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .values(account_deletion_generation=generation)
            )

    for table in TABLES:
        inspector = sa.inspect(bind)
        owner_column = next(
            item
            for item in inspector.get_columns(table)
            if item["name"] == "owner_id"
        )
        foreign_keys = {
            item.get("name"): item
            for item in inspector.get_foreign_keys(table)
        }
        expected_foreign_key = f"fk_{table}_owner_id_user"
        indexes = {
            item.get("name"): item for item in inspector.get_indexes(table)
        }
        expected_index = f"ix_{table}_owner_id"
        needs_owner_not_null = bool(owner_column.get("nullable", True))
        needs_foreign_key = expected_foreign_key not in foreign_keys
        needs_index = expected_index not in indexes
        needs_export_check = False
        if table == "authored_vnext_export_jobs":
            checks = {
                item.get("name"): str(item.get("sqltext") or "")
                for item in inspector.get_check_constraints(table)
            }
            needs_export_check = (
                "account_deletion_generation"
                not in checks.get("ck_avnext_export_counters", "")
            )
        if not any(
            (
                needs_owner_not_null,
                needs_foreign_key,
                needs_index,
                needs_export_check,
            )
        ):
            continue
        with op.batch_alter_table(table) as batch:
            if needs_export_check:
                batch.drop_constraint(
                    "ck_avnext_export_counters", type_="check"
                )
                batch.create_check_constraint(
                    "ck_avnext_export_counters",
                    "attempt_count >= 0 AND fence_token >= 0 "
                    "AND operation_fence >= 1 "
                    "AND account_deletion_generation >= 0",
                )
            if needs_owner_not_null:
                batch.alter_column(
                    "owner_id", existing_type=sa.Uuid(), nullable=False
                )
            if needs_foreign_key:
                batch.create_foreign_key(
                    expected_foreign_key,
                    "user",
                    ["owner_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            if needs_index:
                batch.create_index(expected_index, ["owner_id"], unique=False)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in reversed(TABLES):
        if table not in existing:
            continue
        with op.batch_alter_table(table) as batch:
            if table == "authored_vnext_export_jobs":
                batch.drop_constraint(
                    "ck_avnext_export_counters", type_="check"
                )
                batch.create_check_constraint(
                    "ck_avnext_export_counters",
                    "attempt_count >= 0 AND fence_token >= 0 "
                    "AND operation_fence >= 1",
                )
            batch.drop_index(f"ix_{table}_owner_id")
            batch.drop_constraint(f"fk_{table}_owner_id_user", type_="foreignkey")
            if table in ORIGINAL_OWNER_TABLES:
                batch.alter_column("owner_id", existing_type=sa.Uuid(), nullable=True)
            else:
                batch.drop_column("owner_id")
    if "authored_vnext_export_jobs" in existing:
        export_columns = {
            item["name"]
            for item in sa.inspect(op.get_bind()).get_columns("authored_vnext_export_jobs")
        }
        if "account_deletion_generation" in export_columns:
            op.drop_column(
                "authored_vnext_export_jobs", "account_deletion_generation"
            )
