from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import uuid

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import migrations
from models.sql.presentation_export_attempt import (
    PresentationExportAttemptProvenance,
)


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
RECEIPTS = "asset_gateway_idempotency_receipts"
ATTEMPTS = "presentation_export_attempts"


def _load_revision_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        FASTAPI_ROOT / "alembic" / "versions" / filename,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _BackingNameInspector:
    def __init__(self, dialect: str, collision_kind: str | None = None) -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.collision_kind = collision_kind

    def get_table_names(self) -> list[str]:
        return ["imageasset", "collision_holder"]

    def get_view_names(self) -> list[str]:
        return []

    def get_indexes(self, table: str) -> list[dict[str, object]]:
        if table == "collision_holder" and self.collision_kind == "index":
            return [{"name": "uq_imageasset_canonical_asset_id"}]
        return []

    def get_pk_constraint(self, table: str) -> dict[str, object]:
        if table == "collision_holder" and self.collision_kind == "primary":
            return {"name": "uq_imageasset_canonical_asset_id"}
        return {"name": None}

    def get_unique_constraints(self, table: str) -> list[dict[str, object]]:
        if table == "collision_holder" and self.collision_kind == "unique":
            return [{"name": "uq_imageasset_canonical_asset_id"}]
        return []


def _config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _database_url(tmp_path: Path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def _version(connection) -> str:
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _table_snapshot(connection, table: str) -> tuple[object, ...]:
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE tbl_name = :table ORDER BY type, name"
            ),
            {"table": table},
        )
    )
    rows = tuple(
        tuple(row)
        for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY rowid"))
    )
    return (_version(connection), schema, rows)


def _rewrite_sqlite_table_sql(
    connection,
    table: str,
    old: str,
    new: str,
) -> None:
    original = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    ).scalar_one()
    rewritten = original.replace(old, new, 1)
    assert rewritten != original
    schema_version = connection.exec_driver_sql("PRAGMA schema_version").scalar_one()
    connection.exec_driver_sql("PRAGMA writable_schema = ON")
    connection.execute(
        text("UPDATE sqlite_master SET sql = :sql WHERE type = 'table' AND name = :table"),
        {"sql": rewritten, "table": table},
    )
    connection.exec_driver_sql("PRAGMA writable_schema = OFF")
    connection.exec_driver_sql(f"PRAGMA schema_version = {schema_version + 1}")


def _create_partial_receipts(
    connection,
    *,
    full: bool,
    wrong_id_type: bool = False,
    wrong_index: bool = False,
    wrong_foreign_key: bool = False,
    wrong_unique: bool = False,
    extra_column: bool = False,
    required_nullable: bool = False,
    wrong_check: bool = False,
    wrong_primary: bool = False,
    wrong_primary_name: bool = False,
) -> None:
    connection.execute(text(f"DROP TABLE {RECEIPTS}"))
    if not full:
        id_type = "INTEGER" if wrong_id_type else "CHAR(32)"
        connection.execute(
            text(f"CREATE TABLE {RECEIPTS} (id {id_type} NOT NULL PRIMARY KEY)")
        )
        return
    extras = []
    if wrong_foreign_key:
        extras.append(
            "CONSTRAINT fk_asset_gateway_receipts_asset_id "
            "FOREIGN KEY (asset_id) REFERENCES presentations(id) ON DELETE CASCADE"
        )
    if wrong_unique:
        extras.append(
            "CONSTRAINT uq_asset_gateway_receipts_owner_operation_key "
            "UNIQUE (owner_scope, operation, request_sha256)"
        )
    if wrong_check:
        extras.append(
            "CONSTRAINT ck_asset_gateway_receipts_state "
            "CHECK (state IN ('pending', 'complete', 'corrupt'))"
        )
    if wrong_primary:
        extras.append("PRIMARY KEY (id, owner_scope)")
    if wrong_primary_name:
        extras.append("CONSTRAINT wrong_receipt_primary PRIMARY KEY (id)")
    extra_sql = ", " + ", ".join(extras) if extras else ""
    owner_scope_sql = (
        "owner_scope VARCHAR(36)"
        if required_nullable
        else "owner_scope VARCHAR(36) NOT NULL"
    )
    unexpected_sql = ", unexpected_column VARCHAR(8)" if extra_column else ""
    connection.execute(
        text(
            f"CREATE TABLE {RECEIPTS} ("
            "id CHAR(32) NOT NULL, owner_id CHAR(32), "
            f"{owner_scope_sql}, operation VARCHAR(64) NOT NULL, "
            "key_hash VARCHAR(64) NOT NULL, request_sha256 VARCHAR(64) NOT NULL, "
            "state VARCHAR(16) NOT NULL DEFAULT 'pending', response_payload JSON, "
            "asset_id CHAR(32), snapshot_id CHAR(32), "
            "expires_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            f"{unexpected_sql}"
            f"{extra_sql})"
        )
    )
    if wrong_index:
        connection.execute(
            text(
                f"CREATE INDEX ix_asset_gateway_receipts_expiry "
                f"ON {RECEIPTS} (state)"
            )
        )


def _insert_receipt(
    connection,
    *,
    identifier: str,
    key_hash: str = "a" * 64,
    state: str = "pending",
    asset_id: str | None = None,
) -> None:
    connection.execute(
        text(
            f"INSERT INTO {RECEIPTS} ("
            "id, owner_scope, operation, key_hash, request_sha256, state, "
            "asset_id, expires_at, created_at, updated_at) VALUES ("
            ":id, 'anonymous', 'upload', :key_hash, :request_sha256, :state, "
            ":asset_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": identifier,
            "key_hash": key_hash,
            "request_sha256": "b" * 64,
            "state": state,
            "asset_id": asset_id,
        },
    )


def test_postgresql_auto_primary_backing_names_are_preflighted() -> None:
    inspector = _BackingNameInspector("postgresql")
    d1 = _load_revision_module(
        "d1a4c7e9b2f6_reconcile_post_c0_schema.py",
        "test_d1_postgresql_names",
    )
    d2 = _load_revision_module(
        "d2b5e8f0a3c6_add_presentation_export_attempts.py",
        "test_d2_postgresql_names",
    )
    assert d1.RECEIPT_POSTGRES_AUTO_PRIMARY in d1._receipt_create_global_names(
        inspector
    )
    assert d2.ATTEMPT_POSTGRES_AUTO_PRIMARY in d2._attempt_create_global_names(
        inspector
    )
    assert d1.RECEIPT_POSTGRES_AUTO_PRIMARY in d1.RECEIPT_ACCEPTED_PRIMARY_NAMES


@pytest.mark.parametrize("collision_kind", ["index", "primary", "unique"])
def test_a8_postgresql_unique_backing_name_collision_is_fail_closed(
    collision_kind: str,
) -> None:
    a8 = _load_revision_module(
        "a8c1e4f7b2d9_add_asset_profile_compatibility_bridges.py",
        f"test_a8_postgresql_{collision_kind}",
    )
    with pytest.raises(RuntimeError, match="backing-index name collides"):
        a8._validate_postgresql_backing_name_available(
            _BackingNameInspector("postgresql", collision_kind),
            "uq_imageasset_canonical_asset_id",
            owner_table="imageasset",
        )


def test_a8_sqlite_does_not_apply_postgresql_constraint_name_namespace() -> None:
    a8 = _load_revision_module(
        "a8c1e4f7b2d9_add_asset_profile_compatibility_bridges.py",
        "test_a8_sqlite_names",
    )
    a8._validate_postgresql_backing_name_available(
        _BackingNameInspector("sqlite", "index"),
        "uq_imageasset_canonical_asset_id",
        owner_table="imageasset",
    )


@pytest.mark.parametrize(
    "starting_revision",
    [
        None,
        migrations.REVISION_ASSET_PROFILE_COMPATIBILITY,
        migrations.REVISION_PRESENTATION_REFERENCE_SNAPSHOTS,
        migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY,
    ],
)
def test_empty_and_post_a8_entry_points_upgrade_to_single_head(
    tmp_path: Path,
    starting_revision: str | None,
) -> None:
    database_url = _database_url(
        tmp_path,
        f"entry-{starting_revision or 'empty'}.db",
    )
    config = _config(database_url)
    if starting_revision is not None:
        command.upgrade(config, starting_revision)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert _version(connection) == migrations.HEAD_REVISION
            assert ATTEMPTS in inspector.get_table_names()
            artifact_columns = {
                column["name"]
                for column in inspector.get_columns("workspace_artifacts")
            }
            assert "export_attempt_id" in artifact_columns
    finally:
        engine.dispose()
    assert ScriptDirectory.from_config(config).get_heads() == [migrations.HEAD_REVISION]


def test_metadata_created_a8_unique_index_is_reconciled_without_row_loss(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path, "metadata-a8.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_REFERENCE_SNAPSHOTS)
    engine = sa.create_engine(database_url)
    image_id = uuid.uuid4().hex
    asset_id = uuid.uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workspace_assets ("
                    "id, state, original_name, size_bytes, storage_key, version, "
                    "created_at, updated_at) VALUES ("
                    ":id, 'ready', 'legacy.png', 1, :storage_key, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": asset_id, "storage_key": f"legacy/{asset_id}.png"},
            )
            connection.execute(
                text(
                    "INSERT INTO imageasset "
                    "(id, created_at, is_uploaded, path, owner_id) VALUES "
                    "(:id, CURRENT_TIMESTAMP, 1, 'legacy/image.png', NULL)"
                ),
                {"id": image_id},
            )
            connection.execute(
                text("ALTER TABLE imageasset ADD COLUMN canonical_asset_id CHAR(32)")
            )
            connection.execute(
                text(
                    "UPDATE imageasset SET canonical_asset_id = :asset_id "
                    "WHERE id = :image_id"
                ),
                {"asset_id": asset_id, "image_id": image_id},
            )
            connection.execute(
                text(
                    "ALTER TABLE imageasset ADD COLUMN "
                    "canonicalization_attempts INTEGER NOT NULL DEFAULT 0"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE imageasset ADD COLUMN "
                    "canonicalization_error_code VARCHAR"
                )
            )
            connection.execute(
                text("ALTER TABLE imageasset ADD COLUMN canonicalized_at DATETIME")
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX ix_imageasset_canonical_asset_id "
                    "ON imageasset (canonical_asset_id)"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE presentations ADD COLUMN "
                    "profile_driver_id VARCHAR(32)"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE presentations ADD COLUMN "
                    "profile_schema_version INTEGER"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE presentations ADD COLUMN "
                    "profile_backfilled_at DATETIME"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_presentations_profile_driver_id "
                    "ON presentations (profile_driver_id)"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert connection.execute(
                text(
                    "SELECT path FROM imageasset "
                    "WHERE id = :id AND canonical_asset_id = :asset_id"
                ),
                {"id": image_id, "asset_id": asset_id},
            ).scalar_one() == "legacy/image.png"
            canonical_index = next(
                index
                for index in inspector.get_indexes("imageasset")
                if index["name"] == "ix_imageasset_canonical_asset_id"
            )
            assert not canonical_index["unique"]
            assert {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("imageasset")
            } >= {"uq_imageasset_canonical_asset_id"}
    finally:
        engine.dispose()


def test_a8_rejects_preexisting_unconstrained_canonical_columns(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path, "ambiguous-preexisting-a8.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_REFERENCE_SNAPSHOTS)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            for statement in (
                "ALTER TABLE imageasset ADD COLUMN canonical_asset_id CHAR(32)",
                "ALTER TABLE imageasset ADD COLUMN "
                "canonicalization_attempts INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE imageasset ADD COLUMN canonicalization_error_code VARCHAR",
                "ALTER TABLE imageasset ADD COLUMN canonicalized_at DATETIME",
                "ALTER TABLE presentations ADD COLUMN profile_driver_id VARCHAR(32)",
                "ALTER TABLE presentations ADD COLUMN profile_schema_version INTEGER",
                "ALTER TABLE presentations ADD COLUMN profile_backfilled_at DATETIME",
                "CREATE INDEX ix_presentations_profile_driver_id "
                "ON presentations (profile_driver_id)",
            ):
                connection.execute(text(statement))
        with engine.connect() as connection:
            before = _table_snapshot(connection, "imageasset")
        with pytest.raises(RuntimeError, match="partially or ambiguously applied"):
            command.upgrade(config, migrations.REVISION_ASSET_PROFILE_COMPATIBILITY)
        with engine.connect() as connection:
            assert _table_snapshot(connection, "imageasset") == before
    finally:
        engine.dispose()


def test_d1_repairs_empty_partial_and_valid_populated_c0_receipts(
    tmp_path: Path,
) -> None:
    for populated in (False, True):
        database_url = _database_url(tmp_path, f"partial-c0-{populated}.db")
        config = _config(database_url)
        command.upgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
        engine = sa.create_engine(database_url)
        try:
            with engine.begin() as connection:
                _create_partial_receipts(connection, full=populated)
                if populated:
                    _insert_receipt(connection, identifier="1" * 32)
            command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
            with engine.connect() as connection:
                inspector = inspect(connection)
                assert {
                    column["name"] for column in inspector.get_columns(RECEIPTS)
                } == {
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
                }
                assert connection.execute(
                    text(f"SELECT COUNT(*) FROM {RECEIPTS}")
                ).scalar_one() == int(populated)
                assert {
                    index["name"] for index in inspector.get_indexes(RECEIPTS)
                } == {
                    "ix_asset_gateway_receipts_expiry",
                    "ix_asset_gateway_receipts_asset",
                    "ix_asset_gateway_receipts_snapshot",
                }
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_column", "unexpected type"),
        ("duplicate_id", "duplicate identities"),
        ("duplicate", "duplicate keys"),
        ("invalid_state", "invalid state"),
        ("orphan", "orphaned asset_id"),
        ("wrong_index", "index is incompatible"),
        ("wrong_foreign_key", "foreign key is incompatible"),
        ("wrong_unique", "uniqueness is incompatible"),
        ("extra_column", "unexpected columns"),
        ("required_null", "unexpected nullability"),
        ("wrong_check", "state check is incompatible"),
        ("wrong_primary", "primary key is incompatible"),
        ("wrong_primary_name", "primary key is incompatible"),
    ],
)
def test_d1_rejects_ambiguous_c0_shapes_before_repair(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    database_url = _database_url(tmp_path, f"ambiguous-{case}.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_partial_receipts(
                connection,
                full=case != "wrong_column",
                wrong_id_type=case == "wrong_column",
                wrong_index=case == "wrong_index",
                wrong_foreign_key=case == "wrong_foreign_key",
                wrong_unique=case == "wrong_unique",
                extra_column=case == "extra_column",
                required_nullable=case == "required_null",
                wrong_check=case == "wrong_check",
                wrong_primary=case == "wrong_primary",
                wrong_primary_name=case == "wrong_primary_name",
            )
            if case == "duplicate":
                _insert_receipt(connection, identifier="1" * 32)
                _insert_receipt(connection, identifier="2" * 32)
            elif case == "duplicate_id":
                _insert_receipt(connection, identifier="1" * 32)
                _insert_receipt(
                    connection,
                    identifier="1" * 32,
                    key_hash="c" * 64,
                )
            elif case == "invalid_state":
                _insert_receipt(
                    connection,
                    identifier="1" * 32,
                    state="corrupt",
                )
            elif case == "orphan":
                _insert_receipt(
                    connection,
                    identifier="1" * 32,
                    asset_id="f" * 32,
                )
            elif case == "required_null":
                connection.execute(
                    text(
                        f"INSERT INTO {RECEIPTS} ("
                        "id, owner_scope, operation, key_hash, request_sha256, state, "
                        "expires_at, created_at, updated_at) VALUES ("
                        ":id, NULL, 'upload', :key_hash, :request_sha256, 'pending', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": "1" * 32,
                        "key_hash": "a" * 64,
                        "request_sha256": "b" * 64,
                    },
                )
        with engine.connect() as connection:
            before = _table_snapshot(connection, RECEIPTS)
        with pytest.raises(RuntimeError, match=message):
            command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
        with engine.connect() as connection:
            assert _table_snapshot(connection, RECEIPTS) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("collision_name", "partial"),
    [
        ("uq_asset_gateway_receipts_owner_operation_key", True),
        ("pk_asset_gateway_idempotency_receipts", False),
    ],
)
def test_d1_name_collision_fails_before_repair_and_preserves_source(
    tmp_path: Path,
    collision_name: str,
    partial: bool,
) -> None:
    database_url = _database_url(tmp_path, f"d1-collision-{collision_name}.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_partial_receipts(connection, full=not partial)
            if not partial:
                _insert_receipt(connection, identifier="1" * 32)
            connection.execute(text("CREATE TABLE d1_collision_holder (value INTEGER)"))
            connection.execute(
                text(
                    f"CREATE INDEX {collision_name} "
                    "ON d1_collision_holder (value)"
                )
            )
        with engine.connect() as connection:
            before = _table_snapshot(connection, RECEIPTS)
        with pytest.raises(RuntimeError, match="name collision"):
            command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
        with engine.connect() as connection:
            assert _table_snapshot(connection, RECEIPTS) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "collision_name",
    [
        "uq_export_attempts_scope_presentation_format_key",
        "uq_workspace_artifacts_export_attempt_id",
        "ix_export_attempts_state_created",
    ],
)
def test_d2_name_collision_fails_before_first_ddl(
    tmp_path: Path,
    collision_name: str,
) -> None:
    database_url = _database_url(tmp_path, f"d2-collision-{collision_name}.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE d2_collision_holder (value INTEGER)"))
            connection.execute(
                text(
                    f"CREATE INDEX {collision_name} "
                    "ON d2_collision_holder (value)"
                )
            )
        with engine.connect() as connection:
            before = _table_snapshot(connection, "workspace_artifacts")
        with pytest.raises(RuntimeError, match="name collision"):
            command.upgrade(config, migrations.REVISION_PRESENTATION_EXPORT_ATTEMPTS)
        with engine.connect() as connection:
            assert _table_snapshot(connection, "workspace_artifacts") == before
            assert ATTEMPTS not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_d2_adds_only_attempt_table_and_nullable_unique_artifact_link(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path, "d2-delta.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            before_inspector = inspect(connection)
            before_tables = set(before_inspector.get_table_names())
            before_columns = {
                table: {
                    column["name"] for column in before_inspector.get_columns(table)
                }
                for table in before_tables
            }
        command.upgrade(config, migrations.REVISION_PRESENTATION_EXPORT_ATTEMPTS)
        with engine.connect() as connection:
            inspector = inspect(connection)
            after_tables = set(inspector.get_table_names())
            assert after_tables - before_tables == {ATTEMPTS}
            for table in before_tables - {"alembic_version", "workspace_artifacts"}:
                assert {
                    column["name"] for column in inspector.get_columns(table)
                } == before_columns[table]
            artifact_columns = {
                column["name"]: column
                for column in inspector.get_columns("workspace_artifacts")
            }
            assert set(artifact_columns) - before_columns["workspace_artifacts"] == {
                "export_attempt_id"
            }
            assert artifact_columns["export_attempt_id"]["nullable"] is True
            assert {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("workspace_artifacts")
            } >= {"uq_workspace_artifacts_export_attempt_id"}
            assert {
                constraint["name"]
                for constraint in inspector.get_foreign_keys("workspace_artifacts")
            } >= {"fk_workspace_artifacts_export_attempt_id"}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("attempt_default", "column has an unexpected default"),
        ("artifact_default", "export-attempt default is incompatible"),
        ("artifact_index", "export-attempt index is incompatible"),
    ],
)
def test_d2_existing_shape_requires_exact_defaults_and_artifact_link(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    database_url = _database_url(tmp_path, f"d2-existing-{case}.db")
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_PRESENTATION_EXPORT_ATTEMPTS)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            if case == "attempt_default":
                _rewrite_sqlite_table_sql(
                    connection,
                    ATTEMPTS,
                    "DEFAULT 'pending'",
                    "DEFAULT 'running'",
                )
            elif case == "artifact_default":
                _rewrite_sqlite_table_sql(
                    connection,
                    "workspace_artifacts",
                    "export_attempt_id CHAR(32)",
                    "export_attempt_id CHAR(32) DEFAULT '00000000000000000000000000000000'",
                )
            else:
                connection.execute(
                    text(
                        "CREATE INDEX ix_workspace_artifacts_export_attempt_unexpected "
                        "ON workspace_artifacts (export_attempt_id)"
                    )
                )
            connection.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION},
            )
        with engine.connect() as connection:
            before_attempts = _table_snapshot(connection, ATTEMPTS)
            before_artifacts = _table_snapshot(connection, "workspace_artifacts")
        with pytest.raises(RuntimeError, match=message):
            command.upgrade(config, migrations.REVISION_PRESENTATION_EXPORT_ATTEMPTS)
        with engine.connect() as connection:
            assert _table_snapshot(connection, ATTEMPTS) == before_attempts
            assert _table_snapshot(connection, "workspace_artifacts") == before_artifacts
    finally:
        engine.dispose()


def test_d2_database_checks_idempotency_and_downgrade_data_safety(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path, "d2-checks.db")
    config = _config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    attempt_id = uuid.uuid4().hex
    pending_values = {
        "id": attempt_id,
        "presentation_id": uuid.uuid4().hex,
        "state": "pending",
        "source_snapshot_sha256": "a" * 64,
        "request_hash": "b" * 64,
        "idempotency_key_hash": "c" * 64,
    }
    insert_pending = text(
        f"INSERT INTO {ATTEMPTS} ("
        "id, owner_scope, presentation_id, format, state, source_revision, "
        "source_snapshot_sha256, request_hash, idempotency_key_hash, "
        "requested_adapter, provenance) VALUES ("
        ":id, 'anonymous', :presentation_id, 'pptx', :state, 0, "
        ":source_snapshot_sha256, :request_hash, :idempotency_key_hash, "
        "'auto', '{}')"
    )
    try:
        with engine.begin() as connection:
            connection.execute(insert_pending, pending_values)
            with pytest.raises(IntegrityError):
                connection.execute(
                    insert_pending,
                    pending_values | {"id": uuid.uuid4().hex},
                )
        invalid_rows = (
            {"request_hash": "short"},
            {"request_hash": "z" * 64},
            {"state": "corrupt"},
            {"state": "succeeded"},
        )
        for invalid in invalid_rows:
            with engine.begin() as connection:
                with pytest.raises(IntegrityError):
                    connection.execute(
                        insert_pending,
                        pending_values
                        | {
                            "id": uuid.uuid4().hex,
                            "presentation_id": uuid.uuid4().hex,
                            "idempotency_key_hash": uuid.uuid4().hex * 2,
                        }
                        | invalid,
                    )
        with pytest.raises(
            RuntimeError,
            match="downgrade would discard durable attempts",
        ):
            command.downgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
        with engine.connect() as connection:
            assert _version(connection) == migrations.REVISION_PRESENTATION_EXPORT_ATTEMPTS
            assert connection.execute(
                text(f"SELECT COUNT(*) FROM {ATTEMPTS}")
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_head_c0_head_cycle_and_alembic_check_are_clean(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path, "cycle-and-check.db")
    config = _config(database_url)
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert ATTEMPTS not in inspector.get_table_names()
            assert "export_attempt_id" not in {
                column["name"]
                for column in inspector.get_columns("workspace_artifacts")
            }
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert _version(connection) == migrations.HEAD_REVISION
    finally:
        engine.dispose()


def test_export_attempt_provenance_is_closed_bounded_and_digest_validated() -> None:
    valid = {
        "schema_version": 1,
        "adapter_chain": ["native_clone", "general"],
        "capability_evidence": {"fixed_cardinality": False},
        "fallback_reason": "unsupported_capability",
        "asset_digests": {"logo": "a" * 64},
        "compiler_runtime": {
            "compiler": "presentation-export",
            "compiler_version": "0.4.3",
            "runtime": "node",
            "runtime_version": "22",
        },
        "freshness": {
            "source_revision": 7,
            "source_snapshot_sha256": "b" * 64,
            "package_sha256": "c" * 64,
            "asset_digests_match": True,
            "package_digest_match": True,
        },
    }
    assert PresentationExportAttemptProvenance.model_validate(valid).schema_version == 1
    with pytest.raises(ValueError):
        PresentationExportAttemptProvenance.model_validate(
            valid
            | {
                "adapter_chain": [
                    "native_clone",
                    "native_reconstructed",
                    "general",
                    "native_clone",
                ]
            }
        )
    with pytest.raises(ValueError):
        PresentationExportAttemptProvenance.model_validate(
            valid | {"asset_digests": {"logo": "not-a-digest"}}
        )
    with pytest.raises(ValueError):
        PresentationExportAttemptProvenance.model_validate(
            valid | {"fallback_reason": None}
        )
    with pytest.raises(ValueError):
        PresentationExportAttemptProvenance.model_validate(valid | {"secret": True})
    with pytest.raises(ValueError):
        PresentationExportAttemptProvenance.model_validate(
            valid
            | {"capability_evidence": {"probe": {"api-key": "must-not-store"}}}
        )
    for sensitive_key in (
        "apiKey",
        "accessToken",
        "refreshToken",
        "privateKey",
        "secretKey",
        "sessionCookie",
        "API key",
        "access.token",
        "nested-refresh/token",
    ):
        with pytest.raises(ValueError):
            PresentationExportAttemptProvenance.model_validate(
                valid
                | {
                    "capability_evidence": {
                        "nested": [{"decision": {sensitive_key: "must-not-store"}}]
                    }
                }
            )
