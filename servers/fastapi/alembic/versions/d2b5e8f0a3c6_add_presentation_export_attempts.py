"""Add durable presentation export attempts and the artifact publication link.

Revision ID: d2b5e8f0a3c6
Revises: d1a4c7e9b2f6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "d2b5e8f0a3c6"
down_revision: str | None = "d1a4c7e9b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ATTEMPTS = "presentation_export_attempts"
ARTIFACTS = "workspace_artifacts"
ATTEMPT_COLUMNS = (
    "id",
    "owner_id",
    "owner_scope",
    "presentation_id",
    "workspace_operation_id",
    "format",
    "state",
    "source_revision",
    "source_snapshot_sha256",
    "request_hash",
    "idempotency_key_hash",
    "requested_adapter",
    "selected_adapter",
    "artifact_sha256",
    "artifact_size_bytes",
    "failure_code",
    "failure_path",
    "provenance_schema_version",
    "provenance",
    "started_at",
    "completed_at",
    "created_at",
    "updated_at",
)
ATTEMPT_NULLABILITY = {
    "id": False,
    "owner_id": True,
    "owner_scope": False,
    "presentation_id": False,
    "workspace_operation_id": True,
    "format": False,
    "state": False,
    "source_revision": False,
    "source_snapshot_sha256": False,
    "request_hash": False,
    "idempotency_key_hash": False,
    "requested_adapter": False,
    "selected_adapter": True,
    "artifact_sha256": True,
    "artifact_size_bytes": True,
    "failure_code": True,
    "failure_path": True,
    "provenance_schema_version": False,
    "provenance": False,
    "started_at": True,
    "completed_at": True,
    "created_at": False,
    "updated_at": False,
}
ATTEMPT_INDEXES = {
    "ix_export_attempts_presentation_created": ("presentation_id", "created_at"),
    "ix_export_attempts_state_created": ("state", "created_at"),
    "ix_export_attempts_operation": ("workspace_operation_id",),
}
ATTEMPT_UNIQUE = "uq_export_attempts_scope_presentation_format_key"
ARTIFACT_UNIQUE = "uq_workspace_artifacts_export_attempt_id"
ATTEMPT_POSTGRES_AUTO_PRIMARY = "presentation_export_attempts_pkey"
ATTEMPT_DEFAULTS = {
    "owner_scope": "anonymous",
    "state": "pending",
    "provenance_schema_version": "1",
    "created_at": "current_timestamp",
    "updated_at": "current_timestamp",
}
ATTEMPT_FOREIGN_KEYS = {
    "fk_export_attempts_owner_id": (
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    "fk_export_attempts_presentation_id": (
        ("presentation_id",),
        "presentations",
        ("id",),
        "CASCADE",
    ),
    "fk_export_attempts_workspace_operation_id": (
        ("workspace_operation_id",),
        "workspace_operations",
        ("id",),
        "SET NULL",
    ),
}


def _sha256_sql(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column}) = 64 AND lower({column}) = {column} "
        f"AND length({remainder}) = 0"
    )


ATTEMPT_CHECKS = {
    "ck_export_attempts_format": "format IN ('pptx', 'pdf')",
    "ck_export_attempts_state": (
        "state IN ('pending', 'running', 'succeeded', 'failed')"
    ),
    "ck_export_attempts_source_revision": "source_revision >= 0",
    "ck_export_attempts_hashes": (
        f"({_sha256_sql('source_snapshot_sha256')}) AND "
        f"({_sha256_sql('request_hash')}) AND "
        f"({_sha256_sql('idempotency_key_hash')})"
    ),
    "ck_export_attempts_artifact_digest": (
        "artifact_sha256 IS NULL OR "
        f"({_sha256_sql('artifact_sha256')})"
    ),
    "ck_export_attempts_artifact_size": (
        "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0"
    ),
    "ck_export_attempts_provenance_schema": "provenance_schema_version >= 1",
    "ck_export_attempts_requested_adapter": (
        "requested_adapter IN "
        "('auto', 'native_clone', 'native_reconstructed', 'general')"
    ),
    "ck_export_attempts_selected_adapter": (
        "selected_adapter IS NULL OR selected_adapter IN "
        "('native_clone', 'native_reconstructed', 'general')"
    ),
    "ck_export_attempts_terminal_state": (
        "(state = 'pending' AND started_at IS NULL AND completed_at IS NULL "
        "AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL "
        "AND failure_code IS NULL) OR "
        "(state = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
        "AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL "
        "AND failure_code IS NULL) OR "
        "(state = 'succeeded' AND started_at IS NOT NULL "
        "AND completed_at IS NOT NULL AND selected_adapter IS NOT NULL "
        "AND artifact_sha256 IS NOT NULL AND artifact_size_bytes IS NOT NULL "
        "AND failure_code IS NULL AND failure_path IS NULL) OR "
        "(state = 'failed' AND started_at IS NOT NULL "
        "AND completed_at IS NOT NULL AND artifact_sha256 IS NULL "
        "AND artifact_size_bytes IS NULL AND failure_code IS NOT NULL)"
    ),
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


def _validate_schema_global_names_available(inspector, names: set[str]) -> None:
    """Reserve relation and index-backed names before the first D2 DDL."""

    table_names = set(inspector.get_table_names())
    try:
        view_names = set(inspector.get_view_names())
    except NotImplementedError:
        view_names = set()
    relation_collision = names & (table_names | view_names)
    if relation_collision:
        raise RuntimeError(
            "Presentation export attempt relation name collision: "
            + ",".join(sorted(relation_collision))
        )
    include_constraint_backing_indexes = inspector.bind.dialect.name == "postgresql"
    for table in table_names:
        for index in inspector.get_indexes(table):
            if index.get("duplicates_constraint"):
                continue
            name = str(index.get("name") or "")
            if name in names:
                raise RuntimeError(
                    f"Presentation export attempt index name collision: {table}.{name}"
                )
        if include_constraint_backing_indexes:
            primary_name = str(inspector.get_pk_constraint(table).get("name") or "")
            if primary_name in names:
                raise RuntimeError(
                    "Presentation export attempt primary-key index name collision: "
                    f"{table}.{primary_name}"
                )
            for constraint in inspector.get_unique_constraints(table):
                name = str(constraint.get("name") or "")
                if name in names:
                    raise RuntimeError(
                        "Presentation export attempt unique index name collision: "
                        f"{table}.{name}"
                    )


def _attempt_create_global_names(inspector) -> set[str]:
    names = {ATTEMPTS, ATTEMPT_UNIQUE, ARTIFACT_UNIQUE, *ATTEMPT_INDEXES}
    if inspector.bind.dialect.name == "postgresql":
        names.add(ATTEMPT_POSTGRES_AUTO_PRIMARY)
    return names


def _type_matches(name: str, value: sa.types.TypeEngine, dialect: str) -> bool:
    if name in {
        "id",
        "owner_id",
        "presentation_id",
        "workspace_operation_id",
    }:
        if isinstance(value, sa.Uuid):
            return True
        return bool(
            dialect == "sqlite"
            and isinstance(value, sa.String)
            and getattr(value, "length", None) == 32
        )
    string_lengths = {
        "owner_scope": 36,
        "format": 8,
        "state": 16,
        "source_snapshot_sha256": 64,
        "request_hash": 64,
        "idempotency_key_hash": 64,
        "requested_adapter": 64,
        "selected_adapter": 64,
        "artifact_sha256": 64,
        "failure_code": 128,
        "failure_path": 512,
    }
    if name in string_lengths:
        return bool(
            isinstance(value, sa.String)
            and getattr(value, "length", None) == string_lengths[name]
        )
    if name in {"source_revision", "artifact_size_bytes"}:
        return isinstance(value, sa.BigInteger)
    if name == "provenance_schema_version":
        return isinstance(value, sa.Integer) and not isinstance(value, sa.BigInteger)
    if name == "provenance":
        return isinstance(value, sa.JSON)
    return isinstance(value, sa.DateTime)


def _create_attempts() -> None:
    op.create_table(
        ATTEMPTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "owner_scope",
            sa.String(length=36),
            server_default="anonymous",
            nullable=False,
        ),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_operation_id", sa.Uuid(), nullable=True),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_adapter", sa.String(length=64), nullable=False),
        sa.Column("selected_adapter", sa.String(length=64), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_path", sa.String(length=512), nullable=True),
        sa.Column(
            "provenance_schema_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        *(
            sa.CheckConstraint(sqltext, name=name)
            for name, sqltext in ATTEMPT_CHECKS.items()
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_export_attempts_owner_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            name="fk_export_attempts_presentation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_operation_id"],
            ["workspace_operations.id"],
            name="fk_export_attempts_workspace_operation_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "format",
            "idempotency_key_hash",
            name="uq_export_attempts_scope_presentation_format_key",
        ),
    )
    for name, columns in ATTEMPT_INDEXES.items():
        op.create_index(name, ATTEMPTS, list(columns), unique=False)


def _add_artifact_link() -> None:
    with op.batch_alter_table(ARTIFACTS) as batch:
        batch.add_column(sa.Column("export_attempt_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_workspace_artifacts_export_attempt_id",
            ATTEMPTS,
            ["export_attempt_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_workspace_artifacts_export_attempt_id",
            ["export_attempt_id"],
        )


def _validate_attempts(inspector) -> None:
    columns = {str(column["name"]): column for column in inspector.get_columns(ATTEMPTS)}
    if set(columns) != set(ATTEMPT_COLUMNS):
        raise RuntimeError("Presentation export attempt columns are incompatible")
    dialect = op.get_bind().dialect.name
    for name, column in columns.items():
        if not _type_matches(name, column["type"], dialect):
            raise RuntimeError(
                f"Presentation export attempt column has an unexpected type: {name}"
            )
        if bool(column.get("nullable")) != ATTEMPT_NULLABILITY[name]:
            raise RuntimeError(
                f"Presentation export attempt column has unexpected nullability: {name}"
            )
        actual_default = _normalized_default(column.get("default"))
        expected_default = ATTEMPT_DEFAULTS.get(name, "")
        if name in {"created_at", "updated_at"}:
            if actual_default not in {"current_timestamp", "now()"}:
                raise RuntimeError(
                    f"Presentation export attempt column has an unexpected default: {name}"
                )
        elif actual_default != expected_default:
            raise RuntimeError(
                f"Presentation export attempt column has an unexpected default: {name}"
            )
    if _columns(inspector.get_pk_constraint(ATTEMPTS), "constrained_columns") != (
        "id",
    ):
        raise RuntimeError("Presentation export attempt primary key is incompatible")

    uniques = inspector.get_unique_constraints(ATTEMPTS)
    if len(uniques) != 1 or not (
        uniques[0].get("name") == ATTEMPT_UNIQUE
        and _columns(uniques[0], "column_names")
        == ("owner_scope", "presentation_id", "format", "idempotency_key_hash")
    ):
        raise RuntimeError("Presentation export attempt uniqueness is incompatible")

    actual_checks = {
        str(constraint.get("name") or ""): _normalized_sql(
            constraint.get("sqltext")
        )
        for constraint in inspector.get_check_constraints(ATTEMPTS)
    }
    expected_checks = {
        name: _normalized_sql(sqltext) for name, sqltext in ATTEMPT_CHECKS.items()
    }
    if actual_checks != expected_checks:
        raise RuntimeError("Presentation export attempt checks are incompatible")

    actual_foreign_keys: dict[
        str, tuple[tuple[str, ...], str, tuple[str, ...], str]
    ] = {}
    for constraint in inspector.get_foreign_keys(ATTEMPTS):
        options = constraint.get("options") or {}
        actual_foreign_keys[str(constraint.get("name") or "")] = (
            _columns(constraint, "constrained_columns"),
            str(constraint.get("referred_table") or ""),
            _columns(constraint, "referred_columns"),
            str(options.get("ondelete") or "").upper(),
        )
    if actual_foreign_keys != ATTEMPT_FOREIGN_KEYS:
        raise RuntimeError("Presentation export attempt foreign keys are incompatible")

    physical_indexes = [
        index
        for index in inspector.get_indexes(ATTEMPTS)
        if not index.get("duplicates_constraint")
    ]
    actual_indexes = {
        str(index.get("name") or ""): _columns(index, "column_names")
        for index in physical_indexes
        if not bool(index.get("unique"))
    }
    if actual_indexes != ATTEMPT_INDEXES:
        raise RuntimeError("Presentation export attempt indexes are incompatible")
    if any(bool(index.get("unique")) for index in physical_indexes):
        raise RuntimeError("Presentation export attempt indexes are incompatible")


def _validate_artifact_link(inspector) -> None:
    columns = {
        str(column["name"]): column for column in inspector.get_columns(ARTIFACTS)
    }
    column = columns.get("export_attempt_id")
    if column is None or not bool(column.get("nullable")):
        raise RuntimeError("Workspace artifact export-attempt column is incompatible")
    if not _type_matches("id", column["type"], op.get_bind().dialect.name):
        raise RuntimeError("Workspace artifact export-attempt type is incompatible")
    if _normalized_default(column.get("default")):
        raise RuntimeError("Workspace artifact export-attempt default is incompatible")

    matching_indexes = [
        index
        for index in inspector.get_indexes(ARTIFACTS)
        if not index.get("duplicates_constraint")
        if "export_attempt_id" in _columns(index, "column_names")
    ]
    if matching_indexes:
        raise RuntimeError("Workspace artifact export-attempt index is incompatible")

    matching_unique = [
        constraint
        for constraint in inspector.get_unique_constraints(ARTIFACTS)
        if "export_attempt_id" in _columns(constraint, "column_names")
    ]
    if len(matching_unique) != 1 or not (
        matching_unique[0].get("name")
        == ARTIFACT_UNIQUE
        and _columns(matching_unique[0], "column_names") == ("export_attempt_id",)
    ):
        raise RuntimeError("Workspace artifact export-attempt uniqueness is incompatible")

    matching_foreign_keys = [
        constraint
        for constraint in inspector.get_foreign_keys(ARTIFACTS)
        if "export_attempt_id" in _columns(constraint, "constrained_columns")
    ]
    if len(matching_foreign_keys) != 1:
        raise RuntimeError("Workspace artifact export-attempt foreign key is incompatible")
    constraint = matching_foreign_keys[0]
    options = constraint.get("options") or {}
    if not (
        constraint.get("name") == "fk_workspace_artifacts_export_attempt_id"
        and _columns(constraint, "constrained_columns") == ("export_attempt_id",)
        and constraint.get("referred_table") == ATTEMPTS
        and _columns(constraint, "referred_columns") == ("id",)
        and str(options.get("ondelete") or "").upper() == "SET NULL"
    ):
        raise RuntimeError("Workspace artifact export-attempt foreign key is incompatible")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    missing_predecessors = {
        "user",
        "presentations",
        "workspace_operations",
        ARTIFACTS,
    } - tables
    if missing_predecessors:
        raise RuntimeError(
            "Presentation export attempt predecessor tables are missing: "
            + ",".join(sorted(missing_predecessors))
        )
    attempt_exists = ATTEMPTS in tables
    artifact_columns = {
        column["name"] for column in inspector.get_columns(ARTIFACTS)
    }
    link_exists = "export_attempt_id" in artifact_columns
    if attempt_exists != link_exists:
        raise RuntimeError("Presentation export attempt schema is only partially applied")
    if attempt_exists:
        _validate_attempts(inspector)
        _validate_artifact_link(inspector)
        return

    _validate_schema_global_names_available(
        inspector,
        _attempt_create_global_names(inspector),
    )
    _create_attempts()
    _add_artifact_link()


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if ATTEMPTS not in tables:
        return
    if op.get_bind().execute(sa.text(f"SELECT 1 FROM {ATTEMPTS} LIMIT 1")).first():
        raise RuntimeError(
            "Presentation export attempt downgrade would discard durable attempts"
        )
    artifact_columns = {
        column["name"] for column in inspector.get_columns(ARTIFACTS)
    }
    if "export_attempt_id" in artifact_columns:
        if op.get_bind().execute(
            sa.text(
                f"SELECT 1 FROM {ARTIFACTS} "
                "WHERE export_attempt_id IS NOT NULL LIMIT 1"
            )
        ).first():
            raise RuntimeError(
                "Presentation export attempt downgrade would detach an artifact"
            )
        with op.batch_alter_table(ARTIFACTS) as batch:
            batch.drop_constraint(
                "uq_workspace_artifacts_export_attempt_id",
                type_="unique",
            )
            batch.drop_constraint(
                "fk_workspace_artifacts_export_attempt_id",
                type_="foreignkey",
            )
            batch.drop_column("export_attempt_id")
    op.drop_table(ATTEMPTS)
