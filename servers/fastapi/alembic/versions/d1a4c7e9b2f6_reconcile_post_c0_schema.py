"""Reconcile the post-c0 physical schema without accepting ambiguous DDL.

Revision ID: d1a4c7e9b2f6
Revises: c0e3f5a7b9d1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "d1a4c7e9b2f6"
down_revision: str | None = "c0e3f5a7b9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RECEIPTS = "asset_gateway_idempotency_receipts"
RECEIPT_COLUMNS = (
    "id",
    "owner_id",
    "owner_scope",
    "operation",
    "key_hash",
    "request_sha256",
    "state",
    "response_payload",
    "asset_id",
    "snapshot_id",
    "expires_at",
    "created_at",
    "updated_at",
)
RECEIPT_NULLABILITY = {
    "id": False,
    "owner_id": True,
    "owner_scope": False,
    "operation": False,
    "key_hash": False,
    "request_sha256": False,
    "state": False,
    "response_payload": True,
    "asset_id": True,
    "snapshot_id": True,
    "expires_at": False,
    "created_at": False,
    "updated_at": False,
}
RECEIPT_INDEXES = {
    "ix_asset_gateway_receipts_expiry": ("expires_at", "created_at"),
    "ix_asset_gateway_receipts_asset": ("owner_scope", "asset_id"),
    "ix_asset_gateway_receipts_snapshot": ("owner_scope", "snapshot_id"),
}
RECEIPT_FOREIGN_KEYS = {
    "fk_asset_gateway_receipts_asset_id": (
        ("asset_id",),
        "workspace_assets",
        ("id",),
        "CASCADE",
    ),
    "fk_asset_gateway_receipts_owner_id": (
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    "fk_asset_gateway_receipts_snapshot_id": (
        ("snapshot_id",),
        "reference_snapshots",
        ("id",),
        "CASCADE",
    ),
}
RECEIPT_UNIQUE = "uq_asset_gateway_receipts_owner_operation_key"
RECEIPT_REPAIR_PRIMARY = "pk_asset_gateway_idempotency_receipts"
RECEIPT_POSTGRES_AUTO_PRIMARY = "asset_gateway_idempotency_receipts_pkey"
RECEIPT_ACCEPTED_PRIMARY_NAMES = {
    None,
    RECEIPT_REPAIR_PRIMARY,
    RECEIPT_POSTGRES_AUTO_PRIMARY,
}


def _columns(value: dict[str, object], key: str) -> tuple[str, ...]:
    return tuple(str(item) for item in value.get(key) or ())


def _normalized_sql(value: object) -> str:
    normalized = "".join(str(value or "").lower().split())
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1]
    return normalized.replace('"', "").replace("`", "")


def _normalized_default(value: object) -> str:
    normalized = _normalized_sql(value)
    normalized = normalized.replace("::charactervarying", "").replace("::text", "")
    return normalized.strip("'")


def _type_matches(name: str, value: sa.types.TypeEngine, dialect: str) -> bool:
    if name in {"id", "owner_id", "asset_id", "snapshot_id"}:
        if isinstance(value, sa.Uuid):
            return True
        return bool(
            dialect == "sqlite"
            and isinstance(value, sa.String)
            and getattr(value, "length", None) == 32
        )
    string_lengths = {
        "owner_scope": 36,
        "operation": 64,
        "key_hash": 64,
        "request_sha256": 64,
        "state": 16,
    }
    if name in string_lengths:
        return bool(
            isinstance(value, sa.String)
            and getattr(value, "length", None) == string_lengths[name]
        )
    if name == "response_payload":
        return isinstance(value, sa.JSON)
    return isinstance(value, sa.DateTime)


def _validate_receipt_columns(
    columns: list[dict[str, object]],
    *,
    dialect: str,
) -> set[str]:
    by_name = {str(column["name"]): column for column in columns}
    names = set(by_name)
    unexpected = names - set(RECEIPT_COLUMNS)
    if unexpected:
        raise RuntimeError(
            "Asset Gateway idempotency schema has unexpected columns: "
            + ",".join(sorted(unexpected))
        )
    for name, column in by_name.items():
        if not _type_matches(name, column["type"], dialect):
            raise RuntimeError(
                f"Asset Gateway idempotency column has an unexpected type: {name}"
            )
        if bool(column.get("nullable")) != RECEIPT_NULLABILITY[name]:
            raise RuntimeError(
                f"Asset Gateway idempotency column has unexpected nullability: {name}"
            )
        default = _normalized_default(column.get("default"))
        if name == "state":
            if default not in {"pending", "'pending'"}:
                raise RuntimeError(
                    "Asset Gateway idempotency state default is incompatible"
                )
        elif name in {"created_at", "updated_at"}:
            if default not in {"current_timestamp", "now()"}:
                raise RuntimeError(
                    f"Asset Gateway idempotency timestamp default is incompatible: {name}"
                )
        elif default:
            raise RuntimeError(
                f"Asset Gateway idempotency column has an unexpected default: {name}"
            )
    return names


def _validate_receipt_rows(connection) -> None:
    required = (
        "id",
        "owner_scope",
        "operation",
        "key_hash",
        "request_sha256",
        "state",
        "expires_at",
        "created_at",
        "updated_at",
    )
    null_predicate = " OR ".join(f"{name} IS NULL" for name in required)
    if connection.execute(
        sa.text(f"SELECT 1 FROM {RECEIPTS} WHERE {null_predicate} LIMIT 1")
    ).first():
        raise RuntimeError("Asset Gateway idempotency rows contain required NULLs")
    if connection.execute(
        sa.text(
            f"SELECT 1 FROM {RECEIPTS} "
            "WHERE state NOT IN ('pending', 'complete') LIMIT 1"
        )
    ).first():
        raise RuntimeError("Asset Gateway idempotency rows contain an invalid state")
    if connection.execute(
        sa.text(
            f"SELECT 1 FROM {RECEIPTS} GROUP BY id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first():
        raise RuntimeError("Asset Gateway idempotency rows contain duplicate identities")
    if connection.execute(
        sa.text(
            f"SELECT 1 FROM {RECEIPTS} GROUP BY owner_scope, operation, key_hash "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first():
        raise RuntimeError("Asset Gateway idempotency rows contain duplicate keys")
    for local, target in (
        ("owner_id", "user"),
        ("asset_id", "workspace_assets"),
        ("snapshot_id", "reference_snapshots"),
    ):
        orphan = connection.execute(
            sa.text(
                f"SELECT receipts.{local} FROM {RECEIPTS} AS receipts "
                f"LEFT JOIN {target} AS target ON target.id = receipts.{local} "
                f"WHERE receipts.{local} IS NOT NULL AND target.id IS NULL LIMIT 1"
            )
        ).first()
        if orphan is not None:
            raise RuntimeError(
                f"Asset Gateway idempotency rows contain orphaned {local} values"
            )


def _receipt_contract_state(inspector) -> dict[str, set[str]]:
    primary = inspector.get_pk_constraint(RECEIPTS)
    primary_columns = _columns(primary, "constrained_columns")
    if primary_columns not in {(), ("id",)}:
        raise RuntimeError("Asset Gateway idempotency primary key is incompatible")
    if (
        primary_columns == ("id",)
        and primary.get("name") not in RECEIPT_ACCEPTED_PRIMARY_NAMES
    ):
        raise RuntimeError("Asset Gateway idempotency primary key is incompatible")

    unique_constraints = inspector.get_unique_constraints(RECEIPTS)
    expected_unique = "uq_asset_gateway_receipts_owner_operation_key"
    unique_state: set[str] = set()
    for constraint in unique_constraints:
        name = str(constraint.get("name") or "")
        columns = _columns(constraint, "column_names")
        if name == expected_unique and columns == (
            "owner_scope",
            "operation",
            "key_hash",
        ):
            unique_state.add(expected_unique)
            continue
        raise RuntimeError("Asset Gateway idempotency uniqueness is incompatible")

    check_state: set[str] = set()
    expected_check_sql = _normalized_sql("state IN ('pending', 'complete')")
    for constraint in inspector.get_check_constraints(RECEIPTS):
        name = str(constraint.get("name") or "")
        sqltext = _normalized_sql(constraint.get("sqltext"))
        if name == "ck_asset_gateway_receipts_state" and sqltext == expected_check_sql:
            check_state.add(name)
            continue
        raise RuntimeError("Asset Gateway idempotency state check is incompatible")

    foreign_key_state: set[str] = set()
    for constraint in inspector.get_foreign_keys(RECEIPTS):
        name = str(constraint.get("name") or "")
        expected = RECEIPT_FOREIGN_KEYS.get(name)
        options = constraint.get("options") or {}
        actual = (
            _columns(constraint, "constrained_columns"),
            str(constraint.get("referred_table") or ""),
            _columns(constraint, "referred_columns"),
            str(options.get("ondelete") or "").upper(),
        )
        if expected != actual:
            raise RuntimeError(
                f"Asset Gateway idempotency foreign key is incompatible: {name or '<unnamed>'}"
            )
        foreign_key_state.add(name)

    index_state: set[str] = set()
    for index in inspector.get_indexes(RECEIPTS):
        if index.get("duplicates_constraint"):
            continue
        name = str(index.get("name") or "")
        if (
            RECEIPT_INDEXES.get(name) != _columns(index, "column_names")
            or bool(index.get("unique"))
        ):
            raise RuntimeError(
                f"Asset Gateway idempotency index is incompatible: {name or '<unnamed>'}"
            )
        index_state.add(name)
    return {
        "primary": {"id"} if primary_columns == ("id",) else set(),
        "unique": unique_state,
        "check": check_state,
        "foreign_keys": foreign_key_state,
        "indexes": index_state,
    }


def _create_receipts() -> None:
    op.create_table(
        RECEIPTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_scope", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'complete')",
            name="ck_asset_gateway_receipts_state",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["workspace_assets.id"],
            name="fk_asset_gateway_receipts_asset_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_asset_gateway_receipts_owner_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["reference_snapshots.id"],
            name="fk_asset_gateway_receipts_snapshot_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope",
            "operation",
            "key_hash",
            name="uq_asset_gateway_receipts_owner_operation_key",
        ),
    )
    for name, columns in RECEIPT_INDEXES.items():
        op.create_index(name, RECEIPTS, list(columns), unique=False)


def _validate_schema_global_names_available(
    inspector,
    names: set[str],
    *,
    owner_table: str | None,
) -> None:
    """Fail before DDL when a relation/index-backed name is already owned.

    SQLite table/view/index names and PostgreSQL table/view/index names share a
    schema namespace. PostgreSQL PK/UC names also own backing-index relation
    names; SQLite named constraints do not, so they are intentionally omitted
    there. FK/check names are table-local on both supported dialects.
    """

    if not names:
        return
    table_names = set(inspector.get_table_names())
    try:
        view_names = set(inspector.get_view_names())
    except NotImplementedError:
        view_names = set()
    for relation in table_names | view_names:
        if relation == owner_table:
            continue
        if relation in names:
            raise RuntimeError(
                f"Schema-global relation name collision: {relation}"
            )
    include_constraint_backing_indexes = inspector.bind.dialect.name == "postgresql"
    for table in table_names:
        if table == owner_table:
            continue
        for index in inspector.get_indexes(table):
            if index.get("duplicates_constraint"):
                continue
            name = str(index.get("name") or "")
            if name in names:
                raise RuntimeError(
                    "Schema-global index name collision: " f"{table}.{name}"
                )
        if include_constraint_backing_indexes:
            primary_name = str(inspector.get_pk_constraint(table).get("name") or "")
            if primary_name in names:
                raise RuntimeError(
                    "Schema-global primary-key index name collision: "
                    f"{table}.{primary_name}"
                )
            for constraint in inspector.get_unique_constraints(table):
                name = str(constraint.get("name") or "")
                if name in names:
                    raise RuntimeError(
                        "Schema-global unique index name collision: "
                        f"{table}.{name}"
                    )


def _receipt_create_global_names(inspector) -> set[str]:
    names = {RECEIPT_UNIQUE, *RECEIPT_INDEXES}
    if inspector.bind.dialect.name == "postgresql":
        names.add(RECEIPT_POSTGRES_AUTO_PRIMARY)
    return names


def _reconcile_receipts(inspector) -> None:
    connection = op.get_bind()
    tables = set(inspector.get_table_names())
    predecessor_tables = {"user", "workspace_assets", "reference_snapshots"}
    missing_predecessors = predecessor_tables - tables
    if missing_predecessors:
        raise RuntimeError(
            "Asset Gateway idempotency predecessor tables are missing: "
            + ",".join(sorted(missing_predecessors))
        )
    if RECEIPTS not in tables:
        _validate_schema_global_names_available(
            inspector,
            {RECEIPTS, *_receipt_create_global_names(inspector)},
            owner_table=None,
        )
        _create_receipts()
        return

    names = _validate_receipt_columns(
        inspector.get_columns(RECEIPTS),
        dialect=connection.dialect.name,
    )
    state = _receipt_contract_state(inspector)
    row_count = connection.execute(
        sa.text(f"SELECT COUNT(*) FROM {RECEIPTS}")
    ).scalar_one()
    if names != set(RECEIPT_COLUMNS):
        if row_count:
            raise RuntimeError(
                "Asset Gateway idempotency partial table contains rows and cannot be repaired"
            )
        _validate_schema_global_names_available(
            inspector,
            _receipt_create_global_names(inspector),
            owner_table=RECEIPTS,
        )
        op.drop_table(RECEIPTS)
        _create_receipts()
        return

    _validate_receipt_rows(connection)
    missing_primary = not state["primary"]
    missing_unique = not state["unique"]
    missing_check = not state["check"]
    missing_foreign_keys = set(RECEIPT_FOREIGN_KEYS) - state["foreign_keys"]
    missing_indexes = set(RECEIPT_INDEXES) - state["indexes"]
    schema_global_names = set(missing_indexes)
    if missing_primary:
        schema_global_names.add(RECEIPT_REPAIR_PRIMARY)
    if missing_unique:
        schema_global_names.add(RECEIPT_UNIQUE)
    _validate_schema_global_names_available(
        inspector,
        schema_global_names,
        owner_table=RECEIPTS,
    )

    if missing_primary or missing_unique or missing_check or missing_foreign_keys:
        with op.batch_alter_table(RECEIPTS) as batch:
            if missing_primary:
                batch.create_primary_key(
                    RECEIPT_REPAIR_PRIMARY,
                    ["id"],
                )
            if missing_unique:
                batch.create_unique_constraint(
                    RECEIPT_UNIQUE,
                    ["owner_scope", "operation", "key_hash"],
                )
            if missing_check:
                batch.create_check_constraint(
                    "ck_asset_gateway_receipts_state",
                    "state IN ('pending', 'complete')",
                )
            for name in sorted(missing_foreign_keys):
                local, target, remote, ondelete = RECEIPT_FOREIGN_KEYS[name]
                batch.create_foreign_key(
                    name,
                    target,
                    list(local),
                    list(remote),
                    ondelete=ondelete,
                )
    for name in sorted(missing_indexes):
        op.create_index(name, RECEIPTS, list(RECEIPT_INDEXES[name]), unique=False)


def _validate_canonical_rows(connection) -> None:
    duplicate = connection.execute(
        sa.text(
            "SELECT canonical_asset_id FROM imageasset "
            "WHERE canonical_asset_id IS NOT NULL "
            "GROUP BY canonical_asset_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError("imageasset canonical asset identities are duplicated")
    orphan = connection.execute(
        sa.text(
            "SELECT imageasset.canonical_asset_id FROM imageasset "
            "LEFT JOIN workspace_assets "
            "ON workspace_assets.id = imageasset.canonical_asset_id "
            "WHERE imageasset.canonical_asset_id IS NOT NULL "
            "AND workspace_assets.id IS NULL LIMIT 1"
        )
    ).first()
    if orphan is not None:
        raise RuntimeError("imageasset canonical asset identity is orphaned")


def _reconcile_a8_contract(inspector) -> None:
    tables = set(inspector.get_table_names())
    if not {"imageasset", "workspace_assets", "presentations"}.issubset(tables):
        raise RuntimeError("asset/profile compatibility predecessor tables are missing")
    image_columns = {column["name"] for column in inspector.get_columns("imageasset")}
    profile_columns = {
        column["name"] for column in inspector.get_columns("presentations")
    }
    if not {
        "canonical_asset_id",
        "canonicalization_attempts",
        "canonicalization_error_code",
        "canonicalized_at",
    }.issubset(image_columns):
        raise RuntimeError("Asset/profile compatibility image schema is incomplete")
    if not {
        "profile_driver_id",
        "profile_schema_version",
        "profile_backfilled_at",
    }.issubset(profile_columns):
        raise RuntimeError("Asset/profile compatibility presentation schema is incomplete")

    profile_indexes = {
        str(index.get("name") or ""): index
        for index in inspector.get_indexes("presentations")
        if not index.get("duplicates_constraint")
    }
    profile_index = profile_indexes.get("ix_presentations_profile_driver_id")
    if (
        profile_index is None
        or _columns(profile_index, "column_names") != ("profile_driver_id",)
        or bool(profile_index.get("unique"))
    ):
        raise RuntimeError("Presentation profile compatibility index is incompatible")

    canonical_unique = [
        constraint
        for constraint in inspector.get_unique_constraints("imageasset")
        if "canonical_asset_id" in _columns(constraint, "column_names")
    ]
    canonical_foreign_keys = [
        constraint
        for constraint in inspector.get_foreign_keys("imageasset")
        if "canonical_asset_id" in _columns(constraint, "constrained_columns")
    ]
    canonical_indexes = [
        index
        for index in inspector.get_indexes("imageasset")
        if not index.get("duplicates_constraint")
        if "canonical_asset_id" in _columns(index, "column_names")
    ]
    expected_unique = bool(
        len(canonical_unique) == 1
        and canonical_unique[0].get("name") == "uq_imageasset_canonical_asset_id"
        and _columns(canonical_unique[0], "column_names") == ("canonical_asset_id",)
    )
    expected_foreign_key = False
    if len(canonical_foreign_keys) == 1:
        constraint = canonical_foreign_keys[0]
        options = constraint.get("options") or {}
        expected_foreign_key = bool(
            constraint.get("name") == "fk_imageasset_canonical_asset_id"
            and _columns(constraint, "constrained_columns")
            == ("canonical_asset_id",)
            and constraint.get("referred_table") == "workspace_assets"
            and _columns(constraint, "referred_columns") == ("id",)
            and str(options.get("ondelete") or "").upper() == "SET NULL"
        )
    expected_index = bool(
        len(canonical_indexes) == 1
        and canonical_indexes[0].get("name")
        == "ix_imageasset_canonical_asset_id"
        and _columns(canonical_indexes[0], "column_names")
        == ("canonical_asset_id",)
        and not bool(canonical_indexes[0].get("unique"))
    )
    if expected_unique and expected_foreign_key and expected_index:
        return

    legacy_index = bool(
        not canonical_unique
        and not canonical_foreign_keys
        and len(canonical_indexes) == 1
        and canonical_indexes[0].get("name")
        == "ix_imageasset_canonical_asset_id"
        and _columns(canonical_indexes[0], "column_names")
        == ("canonical_asset_id",)
        and bool(canonical_indexes[0].get("unique"))
    )
    if not legacy_index:
        raise RuntimeError("imageasset canonical asset constraints are ambiguous")

    _validate_schema_global_names_available(
        inspector,
        {
            "uq_imageasset_canonical_asset_id",
            "ix_imageasset_canonical_asset_id",
        },
        owner_table="imageasset",
    )
    _validate_canonical_rows(op.get_bind())
    with op.batch_alter_table("imageasset") as batch:
        batch.create_foreign_key(
            "fk_imageasset_canonical_asset_id",
            "workspace_assets",
            ["canonical_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_imageasset_canonical_asset_id",
            ["canonical_asset_id"],
        )
        batch.drop_index("ix_imageasset_canonical_asset_id")
        batch.create_index(
            "ix_imageasset_canonical_asset_id",
            ["canonical_asset_id"],
            unique=False,
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _reconcile_a8_contract(inspector)
    _reconcile_receipts(sa.inspect(op.get_bind()))


def downgrade() -> None:
    # D1 owns reconciliation only. Downgrading the revision marker must not
    # reintroduce an ambiguous legacy shape or discard receipt data.
    pass
