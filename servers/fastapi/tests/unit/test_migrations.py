import ast
import json
import logging.config
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import migrations
import template_v2_schema_contract
from alembic import command


def test_template_v2_revision_owns_a_frozen_application_free_contract():
    revision_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "a4b5c6d7e8f9_add_template_v2_phase_one.py"
    )
    tree = ast.parse(revision_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_modules <= {
        "__future__",
        "alembic",
        "dataclasses",
        "json",
        "re",
        "sqlalchemy",
        "sqlmodel",
        "typing",
        "uuid",
    }


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _script_head(database_url: str) -> str:
    # The current alembic head, resolved from the migration scripts — so these
    # "upgrade to head" assertions don't go stale each time a migration is added.
    return ScriptDirectory.from_config(_alembic_config(database_url)).get_current_head()


def test_durable_deletion_upgrade_rejects_dangling_attachment_asset(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'dangling-attachment.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, migrations.REVISION_ASYNC_PRESENTATION_TASK_TZ)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                text(
                    """
                    INSERT INTO workspace_message_attachments
                        (id, message_id, asset_id, display_name, asset_state)
                    VALUES
                        (:id, :message_id, :asset_id, 'orphaned.pdf', 'ready')
                    """
                ),
                {
                    "id": "11111111111111111111111111111111",
                    "message_id": "22222222222222222222222222222222",
                    "asset_id": "33333333333333333333333333333333",
                },
            )
            connection.commit()

        with pytest.raises(RuntimeError, match="missing live asset"):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            versions = set(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
            assert migrations.REVISION_ASYNC_PRESENTATION_TASK_TZ in versions
            assert _script_head(database_url) not in versions
            assert "asset_id_snapshot" not in {
                column["name"]
                for column in inspect(connection).get_columns(
                    "workspace_message_attachments"
                )
            }
    finally:
        engine.dispose()


def test_alembic_ini_preserves_existing_application_loggers(tmp_path, monkeypatch):
    """Loading the CLI config must not disable loggers imported by the app."""
    fastapi_root = Path(__file__).resolve().parents[2]
    config = Config(str(fastapi_root / "alembic.ini"))
    config.set_main_option("script_location", str(fastapi_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{tmp_path / 'logging-config.db'}",
    )
    disable_existing_loggers_values = []

    def record_file_config(_filename, *args, **kwargs):
        disable_existing_loggers_values.append(
            kwargs.get("disable_existing_loggers", True)
        )

    monkeypatch.setattr(logging.config, "fileConfig", record_file_config)

    command.upgrade(config, "head")

    assert disable_existing_loggers_values == [False]


def test_postgresql_migration_owner_lock_unlocks_and_disposes_on_failure(
    monkeypatch,
):
    events = []

    class FakeConnection:
        def __enter__(self):
            events.append("connect")
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback):
            events.append("connection_close")

        def execution_options(self, *, isolation_level):
            assert isolation_level == "AUTOCOMMIT"
            return self

        def execute(self, statement, parameters):
            sql = str(statement)
            if "pg_advisory_unlock" in sql:
                events.append("unlock")
            else:
                assert "pg_try_advisory_lock" in sql
                events.append("lock")
            assert parameters == {
                "lock_id": migrations.MIGRATION_ADVISORY_LOCK_ID
            }
            return MockResult()

    class MockResult:
        def scalar_one(self):
            return True

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            events.append("dispose")

    monkeypatch.setattr(
        migrations,
        "create_engine",
        lambda _database_url: FakeEngine(),
    )

    with (
        pytest.raises(RuntimeError, match="migration failed"),
        migrations._migration_owner_lock(
            "postgresql://presenton:secret@database/presenton"
        ),
    ):
        events.append("migration")
        raise RuntimeError("migration failed")

    assert events == [
        "connect",
        "lock",
        "migration",
        "unlock",
        "connection_close",
        "dispose",
    ]


def test_postgresql_migration_owner_lock_times_out(monkeypatch):
    events = []

    class MockResult:
        def scalar_one(self):
            return False

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback):
            events.append("connection_close")

        def execution_options(self, *, isolation_level):
            assert isolation_level == "AUTOCOMMIT"
            return self

        def execute(self, statement, parameters):
            assert "pg_try_advisory_lock" in str(statement)
            assert parameters["lock_id"] == migrations.MIGRATION_ADVISORY_LOCK_ID
            events.append("try_lock")
            return MockResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            events.append("dispose")

    monotonic_values = iter([0.0, 31.0])
    monkeypatch.setattr(migrations, "create_engine", lambda _url: FakeEngine())
    monkeypatch.setattr(
        migrations.time, "monotonic", lambda: next(monotonic_values)
    )
    monkeypatch.setattr(migrations.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        RuntimeError, match="postgresql_migration_lock_timeout"
    ):
        with migrations._migration_owner_lock(
            "postgresql://presenton:secret@database/presenton"
        ):
            pytest.fail("timed-out lock must not enter migration body")

    assert events == ["try_lock", "connection_close", "dispose"]


def test_migration_failure_is_not_masked_when_unlock_fails(
    monkeypatch, capsys
):
    class MockResult:
        def scalar_one(self):
            return True

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback):
            pass

        def execution_options(self, *, isolation_level):
            return self

        def execute(self, statement, _parameters):
            if "pg_advisory_unlock" in str(statement):
                raise RuntimeError("unlock failed")
            return MockResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    monkeypatch.setattr(migrations, "create_engine", lambda _url: FakeEngine())

    with pytest.raises(RuntimeError, match="migration failed"):
        with migrations._migration_owner_lock(
            "postgresql://presenton:secret@database/presenton"
        ):
            raise RuntimeError("migration failed")

    assert "preserving the migration failure" in capsys.readouterr().out


def test_non_postgresql_migration_owner_lock_does_not_create_engine(
    monkeypatch,
):
    monkeypatch.setattr(
        migrations,
        "create_engine",
        lambda _database_url: pytest.fail("SQLite must not acquire a lock"),
    )

    with migrations._migration_owner_lock("sqlite:///local.db"):
        pass


def test_legacy_database_with_theme_is_stamped_past_theme_migration(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE presentations (id TEXT PRIMARY KEY, theme JSON)")
            )
            connection.execute(
                text("CREATE TABLE slides (id TEXT PRIMARY KEY, html_content TEXT)")
            )
    finally:
        engine.dispose()

    stamped_revisions = []
    monkeypatch.setattr(
        migrations.command,
        "stamp",
        lambda _config, revision: stamped_revisions.append(revision),
    )

    migrations._stamp_legacy_database_if_needed(
        _alembic_config(database_url), database_url
    )

    assert stamped_revisions == [migrations.REVISION_BEFORE_TEMPLATE_CREATE_INFO]


def test_upgrade_from_baseline_stamp_skips_existing_theme_column(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'baseline-stamped.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE presentations (id TEXT PRIMARY KEY, theme JSON)")
            )
            connection.execute(
                text("CREATE TABLE slides (id TEXT PRIMARY KEY, html_content TEXT)")
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.LEGACY_BASELINE_REVISION},
            )

        command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(presentations)"))
            }

        assert version == _script_head(database_url)
        assert "theme" in columns
    finally:
        engine.dispose()


def test_upgrade_from_theme_stamp_skips_existing_template_create_infos_table(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-table-exists.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE presentations (id TEXT PRIMARY KEY, theme JSON)")
            )
            connection.execute(
                text("CREATE TABLE slides (id TEXT PRIMARY KEY, html_content TEXT)")
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE template_create_infos (
                        id CHAR(32) NOT NULL,
                        fonts JSON,
                        pptx_url VARCHAR,
                        slide_htmls JSON NOT NULL,
                        slide_image_urls JSON NOT NULL,
                        created_at DATETIME NOT NULL,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_BEFORE_TEMPLATE_CREATE_INFO},
            )

        command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }

        assert version == _script_head(database_url)
        assert "template_create_infos" in tables
    finally:
        engine.dispose()


def test_upgrade_from_template_stamp_skips_existing_chat_history_table(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'chat-table-exists.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE presentations (id TEXT PRIMARY KEY)"))
            connection.execute(
                text("CREATE TABLE slides (id TEXT PRIMARY KEY, html_content TEXT)")
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE chat_history_messages (
                        id CHAR(32) NOT NULL,
                        presentation_id CHAR(32) NOT NULL,
                        conversation_id CHAR(32) NOT NULL,
                        position INTEGER NOT NULL,
                        role VARCHAR NOT NULL,
                        content TEXT NOT NULL,
                        created_at DATETIME NOT NULL,
                        tool_calls JSON,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_TEMPLATE_CREATE_INFO},
            )

        command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            indexes = {
                row[1]
                for row in connection.execute(
                    text("PRAGMA index_list(chat_history_messages)")
                )
            }

        assert version == _script_head(database_url)
        assert {
            "ix_chat_history_messages_conversation_id",
            "ix_chat_history_messages_position",
            "ix_chat_history_messages_presentation_id",
        }.issubset(indexes)
    finally:
        engine.dispose()


def test_head_repair_adds_missing_chat_template_scope_without_losing_rows(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'chat-template-scope-repair.db'}"
    engine = create_engine(database_url)
    config = _alembic_config(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE user ("
                    "id CHAR(32) PRIMARY KEY, "
                    "is_active BOOLEAN NOT NULL DEFAULT 1"
                    ")"
                )
            )
            connection.execute(
                text("CREATE TABLE presentations (id CHAR(32) PRIMARY KEY)")
            )
            connection.execute(
                text("CREATE TABLE template_v2 (id VARCHAR PRIMARY KEY)")
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE chat_history_messages (
                        id CHAR(32) PRIMARY KEY,
                        conversation_id CHAR(32) NOT NULL,
                        position INTEGER NOT NULL,
                        role VARCHAR NOT NULL,
                        content TEXT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO chat_history_messages (
                        id, conversation_id, position, role, content, created_at
                    ) VALUES (
                        'message-1', 'conversation-1', 1, 'user', 'keep me',
                        '2026-07-31 00:00:00'
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_PRIMARY_ADMIN},
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "template_v2_id" in {
                column["name"]
                for column in inspector.get_columns("chat_history_messages")
            }
            assert "ix_chat_history_messages_template_v2_id" in {
                index["name"]
                for index in inspector.get_indexes("chat_history_messages")
            }
            assert any(
                foreign_key["constrained_columns"] == ["template_v2_id"]
                and foreign_key["referred_table"] == "template_v2"
                and foreign_key["referred_columns"] == ["id"]
                for foreign_key in inspector.get_foreign_keys(
                    "chat_history_messages"
                )
            )
            assert connection.execute(
                text(
                    "SELECT content FROM chat_history_messages WHERE id = 'message-1'"
                )
            ).scalar_one() == "keep me"
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == _script_head(database_url)

        command.downgrade(config, migrations.REVISION_PRIMARY_ADMIN)
        command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == _script_head(database_url)
            assert "template_v2_id" in {
                column["name"]
                for column in inspect(connection).get_columns(
                    "chat_history_messages"
                )
            }
    finally:
        engine.dispose()


def test_template_v2_phase_one_migration_is_additive_from_actual_previous_head(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'template-v2.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id TEXT PRIMARY KEY,
                        share_token TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE slides (
                        id TEXT PRIMARY KEY,
                        html_content TEXT
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            db_inspector = inspect(connection)
            tables = set(db_inspector.get_table_names())
            presentation_columns = {
                column["name"]
                for column in db_inspector.get_columns("presentations")
            }
            slide_columns = {
                column["name"] for column in db_inspector.get_columns("slides")
            }
            check_names = {
                constraint["name"]
                for constraint in db_inspector.get_check_constraints("slides")
            }
            version_default = next(
                column["default"]
                for column in db_inspector.get_columns("presentations")
                if column["name"] == "version"
            )
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            template_columns = {
                column["name"]
                for column in db_inspector.get_columns("template_v2")
            }
            template_foreign_keys = db_inspector.get_foreign_keys("template_v2")
            local_state_foreign_keys = db_inspector.get_foreign_keys(
                "template_v2_local_state"
            )
            template_indexes = db_inspector.get_indexes("template_v2")
            workspace_event_columns = {
                column["name"]: column
                for column in db_inspector.get_columns(
                    "workspace_operation_events"
                )
            }
            workspace_artifact_columns = {
                column["name"]
                for column in db_inspector.get_columns("workspace_artifacts")
            }
            workspace_operation_unique_columns = {
                frozenset(constraint["column_names"])
                for constraint in db_inspector.get_unique_constraints(
                    "workspace_operations"
                )
            }
            workspace_receipt_unique_columns = {
                frozenset(constraint["column_names"])
                for constraint in db_inspector.get_unique_constraints(
                    "workspace_action_receipts"
                )
            }
            workspace_receipt_foreign_keys = db_inspector.get_foreign_keys(
                "workspace_action_receipts"
            )

        assert revision == _script_head(database_url)
        assert "template_v2" in tables
        assert "template_v2_local_state" in tables
        assert "template_v2_pptx_imports" in tables
        assert "presentation_generation_jobs" in tables
        assert "version" in presentation_columns
        assert "lifecycle_status" in presentation_columns
        assert "ui" in slide_columns
        assert "ck_slides_native_ui_or_authored_html" in check_names
        assert "v1-standard" in version_default
        assert "presentation_id" in template_columns
        assert "revision" in template_columns
        assert any(
            foreign_key["name"]
            == "fk_template_v2_presentation_id_presentations"
            and foreign_key["options"]["ondelete"] == "RESTRICT"
            for foreign_key in template_foreign_keys
        )
        assert any(
            foreign_key["name"]
            == (
                "fk_template_v2_local_state_presentation_id_presentations"
            )
            and foreign_key["options"]["ondelete"] == "RESTRICT"
            for foreign_key in local_state_foreign_keys
        )
        assert any(
            index["name"] == "ix_template_v2_presentation_id"
            for index in template_indexes
        )
        assert workspace_event_columns["operation_version"]["nullable"] is False
        assert migrations.WORKSPACE_TABLE_COLUMNS[
            "workspace_artifacts"
        ].issubset(workspace_artifact_columns)
        assert frozenset(
            {
                "owner_scope",
                "presentation_id",
                "kind",
                "idempotency_key_hash",
            }
        ) in workspace_operation_unique_columns
        assert frozenset(
            {
                "owner_scope",
                "operation_id",
                "action_type",
                "idempotency_key_hash",
            }
        ) in workspace_receipt_unique_columns
        assert any(
            foreign_key["constrained_columns"] == ["result_operation_id"]
            and foreign_key["referred_table"] == "workspace_operations"
            and foreign_key["options"].get("ondelete") == "SET NULL"
            for foreign_key in workspace_receipt_foreign_keys
        )
    finally:
        engine.dispose()


class _SchemaInspector:
    def __init__(
        self,
        columns: dict[str, set[str]],
        *,
        primary_keys: dict[str, set[str]] | None = None,
        check_constraints: dict[str, set[str]] | None = None,
        unique_constraints: dict[str, set[str]] | None = None,
        indexes: dict[str, dict[str, bool]] | None = None,
        foreign_keys: dict[
            str, set[tuple[tuple[str, ...], str, str]]
        ] | None = None,
    ):
        self._columns = columns
        self._primary_keys = primary_keys or {}
        self._check_constraints = check_constraints or {}
        self._unique_constraints = unique_constraints or {}
        self._indexes = indexes or {}
        self._foreign_keys = foreign_keys or {}

    def get_columns(self, table_name: str):
        return [{"name": name} for name in self._columns.get(table_name, set())]

    def get_pk_constraint(self, table_name: str):
        return {
            "constrained_columns": list(
                self._primary_keys.get(table_name, set())
            )
        }

    def get_check_constraints(self, table_name: str):
        return [
            {"name": name}
            for name in self._check_constraints.get(table_name, set())
        ]

    def get_unique_constraints(self, table_name: str):
        return [
            {"name": name}
            for name in self._unique_constraints.get(table_name, set())
        ]

    def get_indexes(self, table_name: str):
        return [
            {"name": name, "unique": unique}
            for name, unique in self._indexes.get(table_name, {}).items()
        ]

    def get_foreign_keys(self, table_name: str):
        return [
            {
                "constrained_columns": list(columns),
                "referred_table": referred_table,
                "options": {"ondelete": ondelete},
            }
            for columns, referred_table, ondelete in self._foreign_keys.get(
                table_name, set()
            )
        ]


def _complete_workspace_schema_inspector(
    columns: dict[str, set[str]] | None = None,
) -> _SchemaInspector:
    workspace_columns = columns or {
        table: set(required_columns)
        for table, required_columns in migrations.WORKSPACE_TABLE_COLUMNS.items()
    }
    checks = {
        table: set(required)
        for table, required in migrations.WORKSPACE_CHECK_CONSTRAINTS.items()
    }
    checks["presentations"] = {"ck_presentations_revision_nonnegative"}
    indexes = {
        table: {name: False for name in required}
        for table, required in migrations.WORKSPACE_INDEXES.items()
    }
    indexes["presentation_generation_jobs"] = {
        "ix_presentation_generation_jobs_workspace_operation_id": True
    }
    return _SchemaInspector(
        workspace_columns,
        check_constraints=checks,
        unique_constraints={
            table: set(required)
            for table, required in migrations.WORKSPACE_UNIQUE_CONSTRAINTS.items()
        },
        indexes=indexes,
        foreign_keys={
            **{
                table: set(required)
                for table, required in migrations.WORKSPACE_FOREIGN_KEYS.items()
            },
            "presentation_generation_jobs": {
                (
                    ("workspace_operation_id",),
                    "workspace_operations",
                    "SET NULL",
                )
            },
        },
    )


@pytest.mark.parametrize(
    ("table", "missing_column"),
    (
        ("workspace_operations", "lease_expires_at"),
        ("workspace_operations", "retry_count"),
        ("workspace_outbox", "available_at"),
        ("workspace_outbox", "lease_expires_at"),
        ("workspace_artifacts", "cleanup_lease_expires_at"),
        ("workspace_artifacts", "cleanup_attempts"),
        ("workspace_artifacts", "cleanup_error"),
        ("workspace_artifacts", "deleted_at"),
    ),
)
def test_workspace_schema_inference_rejects_missing_contract_column(
    table,
    missing_column,
):
    columns = {
        table: set(required_columns)
        for table, required_columns in migrations.WORKSPACE_TABLE_COLUMNS.items()
    }
    columns[table].remove(missing_column)
    inspector = _complete_workspace_schema_inspector(columns)

    with pytest.raises(
        RuntimeError,
        match=f"workspace table {table} is only partially applied",
    ):
        migrations._workspace_schema_applied(
            inspector,
            set(migrations.WORKSPACE_TABLE_COLUMNS),
            {"revision"},
            {"workspace_operation_id"},
        )


@pytest.mark.parametrize(
    ("contract", "table", "missing_name", "error"),
    (
        (
            "check",
            "workspace_operations",
            "ck_workspace_operations_state",
            "missing check constraints",
        ),
        (
            "unique",
            "workspace_operations",
            "uq_workspace_operations_command_idempotency",
            "missing unique constraints",
        ),
        (
            "index",
            "workspace_operations",
            "ix_workspace_operations_dispatch",
            "missing indexes",
        ),
        (
            "foreign_key",
            "workspace_operations",
            (("conversation_id",), "workspace_conversations", "CASCADE"),
            "missing foreign keys",
        ),
        (
            "check",
            "workspace_outbox",
            "ck_workspace_outbox_status",
            "missing check constraints",
        ),
        (
            "index",
            "workspace_outbox",
            "ix_workspace_outbox_delivery",
            "missing indexes",
        ),
    ),
)
def test_workspace_schema_inference_rejects_missing_runner_contract(
    contract,
    table,
    missing_name,
    error,
):
    inspector = _complete_workspace_schema_inspector()
    if contract == "check":
        inspector._check_constraints[table].remove(missing_name)
    elif contract == "unique":
        inspector._unique_constraints[table].remove(missing_name)
    elif contract == "index":
        del inspector._indexes[table][missing_name]
    else:
        inspector._foreign_keys[table].remove(missing_name)

    with pytest.raises(RuntimeError, match=error):
        migrations._workspace_schema_applied(
            inspector,
            set(migrations.WORKSPACE_TABLE_COLUMNS),
            {"revision"},
            {"workspace_operation_id"},
        )


@pytest.mark.parametrize(
    ("contract", "error"),
    (
        ("presentation_check", "presentations revision constraint"),
        ("job_index", "generation job operation index"),
        ("job_foreign_key", "generation job operation foreign key"),
    ),
)
def test_workspace_schema_inference_rejects_missing_publication_bridge_contract(
    contract,
    error,
):
    inspector = _complete_workspace_schema_inspector()
    if contract == "presentation_check":
        inspector._check_constraints["presentations"].clear()
    elif contract == "job_index":
        inspector._indexes["presentation_generation_jobs"].clear()
    else:
        inspector._foreign_keys["presentation_generation_jobs"].clear()

    with pytest.raises(RuntimeError, match=error):
        migrations._workspace_schema_applied(
            inspector,
            set(migrations.WORKSPACE_TABLE_COLUMNS),
            {"revision"},
            {"workspace_operation_id"},
        )


@pytest.mark.parametrize(
    ("timezone_flags", "expected_revision"),
    (
        (
            (False, False),
            migrations.REVISION_CHAT_FIRST_WORKSPACE,
        ),
        (
            (True, True),
            migrations.REVISION_ASYNC_PRESENTATION_TASK_TZ,
        ),
    ),
)
def test_async_task_timestamp_revision_requires_uniform_postgresql_types(
    timezone_flags,
    expected_revision,
):
    class TimestampInspector:
        bind = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        )

        def get_columns(self, table_name):
            assert table_name == migrations.ASYNC_PRESENTATION_TASK_TABLE
            return [
                {
                    "name": name,
                    "type": sa.DateTime(timezone=timezone_enabled),
                }
                for name, timezone_enabled in zip(
                    migrations.ASYNC_PRESENTATION_TASK_TIMESTAMP_COLUMNS,
                    timezone_flags,
                    strict=True,
                )
            ]

    assert (
        migrations._async_task_timestamp_revision(
            TimestampInspector(),
            {migrations.ASYNC_PRESENTATION_TASK_TABLE},
        )
        == expected_revision
    )


def test_async_task_timestamp_revision_rejects_mixed_postgresql_types():
    class TimestampInspector:
        bind = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        )

        def get_columns(self, _table_name):
            return [
                {"name": "created_at", "type": sa.DateTime(timezone=True)},
                {"name": "updated_at", "type": sa.DateTime(timezone=False)},
            ]

    with pytest.raises(RuntimeError, match="only partially applied"):
        migrations._async_task_timestamp_revision(
            TimestampInspector(),
            {migrations.ASYNC_PRESENTATION_TASK_TABLE},
        )


@pytest.mark.parametrize(
    ("direction", "timestamp_state"),
    (("upgrade", "naive"), ("downgrade", "aware")),
)
def test_async_task_timezone_migration_locks_before_preflight(
    monkeypatch,
    direction,
    timestamp_state,
):
    revision_module = ScriptDirectory.from_config(
        _alembic_config("sqlite://")
    ).get_revision(migrations.REVISION_ASYNC_PRESENTATION_TASK_TZ).module
    events = []
    bind = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )

    monkeypatch.setattr(revision_module.op, "get_bind", lambda: bind)
    monkeypatch.setattr(revision_module, "_table_exists", lambda: True)
    monkeypatch.setattr(
        revision_module,
        "_lock_table",
        lambda: events.append("lock"),
    )
    monkeypatch.setattr(
        revision_module,
        "_timestamp_state",
        lambda: events.append("type") or timestamp_state,
    )
    monkeypatch.setattr(
        revision_module,
        "_legacy_timezone_literal",
        lambda **_kwargs: events.append("timezone") or "'UTC'",
    )
    monkeypatch.setattr(
        revision_module.op,
        "alter_column",
        lambda *_args, **_kwargs: events.append("alter"),
    )

    getattr(revision_module, direction)()

    assert events[:3] == ["lock", "type", "timezone"]


def test_async_task_timezone_migration_uses_access_exclusive_lock(monkeypatch):
    revision_module = ScriptDirectory.from_config(
        _alembic_config("sqlite://")
    ).get_revision(migrations.REVISION_ASYNC_PRESENTATION_TASK_TZ).module
    statements = []
    monkeypatch.setattr(
        revision_module.op,
        "execute",
        lambda statement: statements.append(str(statement)),
    )

    revision_module._lock_table()

    assert statements == [
        "LOCK TABLE async_presentation_generation_tasks "
        "IN ACCESS EXCLUSIVE MODE"
    ]


def test_schema_inference_never_stamps_future_head_from_old_share_token_marker():
    tables = {
        "presentations",
        "slides",
        "template_create_infos",
        "chat_history_messages",
        "presentation_versions",
    }
    inspector = _SchemaInspector(
        {
            "presentations": {
                "theme",
                "deck_plan",
                "mode",
                "share_token",
            },
            "slides": set(),
        }
    )

    assert (
        migrations._infer_revision_from_schema(inspector, tables, "future-head")
        == migrations.REVISION_SHARE_TOKEN
    )


def test_schema_inference_requires_complete_template_v2_marker():
    tables = {
        "presentations",
        "slides",
        "template_create_infos",
        "chat_history_messages",
        "presentation_versions",
        "template_v2",
    }
    inspector = _SchemaInspector(
        {
            "presentations": {
                "theme",
                "deck_plan",
                "mode",
                "share_token",
                "version",
            },
            "slides": {"ui"},
            "template_v2": set(migrations.TEMPLATE_V2_EXPECTED_COLUMNS),
        },
        primary_keys={"template_v2": {"id"}},
        check_constraints={
            "slides": {migrations.SLIDE_UI_CHECK_CONSTRAINT}
        },
    )

    assert (
        migrations._infer_revision_from_schema(inspector, tables, "future-head")
        == migrations.REVISION_TEMPLATE_V2_PHASE_ONE
    )

    tables.remove("presentation_versions")
    assert (
        migrations._infer_revision_from_schema(inspector, tables, "future-head")
        == migrations.REVISION_PRESENTATION_MODE
    )


@pytest.mark.parametrize(
    "missing_column",
    sorted(migrations.TEMPLATE_V2_EXPECTED_COLUMNS),
)
def test_schema_inference_rejects_each_partial_template_v2_column(
    missing_column,
):
    tables = {
        "presentations",
        "slides",
        "template_create_infos",
        "chat_history_messages",
        "presentation_versions",
        "template_v2",
    }
    inspector = _SchemaInspector(
        {
            "presentations": {
                "theme",
                "deck_plan",
                "mode",
                "share_token",
                "version",
            },
            "slides": {"ui"},
            "template_v2": (
                set(migrations.TEMPLATE_V2_EXPECTED_COLUMNS) - {missing_column}
            ),
        },
        primary_keys={"template_v2": {"id"}},
        check_constraints={
            "slides": {migrations.SLIDE_UI_CHECK_CONSTRAINT}
        },
    )

    assert (
        migrations._infer_revision_from_schema(inspector, tables, "future-head")
        == migrations.REVISION_SHARE_TOKEN
    )


@pytest.mark.parametrize(
    ("primary_key", "check_constraints"),
    [
        (set(), {migrations.SLIDE_UI_CHECK_CONSTRAINT}),
        ({"id", "name"}, {migrations.SLIDE_UI_CHECK_CONSTRAINT}),
        ({"id"}, set()),
        ({"id"}, {"unnamed-equivalent-check"}),
    ],
)
def test_schema_inference_requires_exact_pk_and_named_slide_constraint(
    primary_key,
    check_constraints,
):
    tables = {
        "presentations",
        "slides",
        "template_create_infos",
        "chat_history_messages",
        "presentation_versions",
        "template_v2",
    }
    inspector = _SchemaInspector(
        {
            "presentations": {
                "theme",
                "deck_plan",
                "mode",
                "share_token",
                "version",
            },
            "slides": {"ui"},
            "template_v2": set(migrations.TEMPLATE_V2_EXPECTED_COLUMNS),
        },
        primary_keys={"template_v2": primary_key},
        check_constraints={"slides": check_constraints},
    )

    assert (
        migrations._infer_revision_from_schema(inspector, tables, "future-head")
        == migrations.REVISION_SHARE_TOKEN
    )


def test_partial_template_v2_table_stops_before_any_phase_one_ddl(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-partial.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id TEXT PRIMARY KEY,
                        share_token TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE slides (
                        id TEXT PRIMARY KEY,
                        html_content TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE template_v2 (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        with pytest.raises(
            RuntimeError,
            match="Template V2 Phase 1 schema is incompatible",
        ):
            command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            presentation_columns = {
                column["name"]
                for column in inspect(connection).get_columns("presentations")
            }
            slide_columns = {
                column["name"]
                for column in inspect(connection).get_columns("slides")
            }
            check_names = {
                constraint["name"]
                for constraint in inspect(connection).get_check_constraints("slides")
            }

        assert revision == migrations.REVISION_SHARE_TOKEN
        assert "version" not in presentation_columns
        assert "ui" not in slide_columns
        assert migrations.SLIDE_UI_CHECK_CONSTRAINT not in check_names
    finally:
        engine.dispose()


def test_missing_constraint_dependency_stops_before_any_phase_one_ddl(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-missing-html.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id TEXT PRIMARY KEY,
                        share_token TEXT
                    )
                    """
                )
            )
            connection.execute(text("CREATE TABLE slides (id TEXT PRIMARY KEY)"))
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        with pytest.raises(
            RuntimeError,
            match="slides missing base columns html_content",
        ):
            command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            presentation_columns = {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            slide_columns = {
                column["name"] for column in inspector.get_columns("slides")
            }
            table_names = set(inspector.get_table_names())

        assert revision == migrations.REVISION_SHARE_TOKEN
        assert "version" not in presentation_columns
        assert "ui" not in slide_columns
        assert "template_v2" not in table_names
    finally:
        engine.dispose()


def _create_complete_phase_one_schema(
    connection,
    *,
    slide_check_sql: str = (
        "ui IS NULL OR html_content IS NULL OR html_content = ''"
    ),
    slide_ui_sql: str = "ui JSON",
    template_name_sql: str = "name VARCHAR NOT NULL",
    presentation_version_sql: str = (
        "version VARCHAR DEFAULT 'v1-standard' NOT NULL"
    ),
) -> None:
    connection.execute(
        text(
            f"""
            CREATE TABLE presentations (
                id CHAR(32) NOT NULL,
                theme JSON,
                deck_plan JSON,
                mode VARCHAR,
                share_token VARCHAR,
                {presentation_version_sql},
                PRIMARY KEY (id)
            )
            """
        )
    )
    connection.execute(
        text(
            f"""
            CREATE TABLE slides (
                id CHAR(32) NOT NULL,
                html_content TEXT,
                {slide_ui_sql},
                PRIMARY KEY (id),
                CONSTRAINT {migrations.SLIDE_UI_CHECK_CONSTRAINT}
                    CHECK ({slide_check_sql})
            )
            """
        )
    )
    for table_name in (
        "template_create_infos",
        "chat_history_messages",
        "presentation_versions",
    ):
        connection.execute(
            text(f"CREATE TABLE {table_name} (id CHAR(32) PRIMARY KEY)")
        )
    connection.execute(
        text(
            f"""
            CREATE TABLE template_v2 (
                id VARCHAR NOT NULL,
                presentation_id CHAR(32) NOT NULL,
                {template_name_sql},
                description VARCHAR,
                raw_layouts JSON,
                components JSON,
                merged_components JSON,
                layouts JSON,
                assets JSON,
                is_default BOOLEAN DEFAULT 0 NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT fk_template_v2_presentation_id_presentations FOREIGN KEY(presentation_id) REFERENCES presentations (id) ON DELETE CASCADE,
                PRIMARY KEY (id)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX ix_template_v2_presentation_id
            ON template_v2 (presentation_id)
            """
        )
    )


def _valid_native_ui() -> dict:
    return {
        "id": "title",
        "description": "Valid native title layout",
        "components": [],
    }


def test_complete_unversioned_phase_one_schema_is_stamped_at_phase_one_head(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'complete-unversioned.db'}"
    engine = create_engine(database_url)
    source_id = "11111111111141118111111111111111"
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (id, mode, version)
                    VALUES (:id, 'template', 'v2-standard')
                    """
                ),
                {"id": source_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO slides (id, ui)
                    VALUES ('valid-slide', :ui)
                    """
                ),
                {"ui": json.dumps(_valid_native_ui())},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO template_v2
                        (id, presentation_id, name)
                    VALUES ('valid-template', :source_id, 'Valid template')
                    """
                ),
                {"source_id": source_id},
            )
            report = migrations.validate_template_v2_phase_one_schema(connection)
            assert report.complete, report

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda _config, revision: stamp_calls.append(revision),
        )
        migrations._stamp_legacy_database_if_needed(
            _alembic_config(database_url),
            database_url,
        )
        assert stamp_calls == [migrations.REVISION_TEMPLATE_V2_PHASE_ONE]
    finally:
        engine.dispose()


def test_unversioned_slide_ui_server_default_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'ui-default-unversioned.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(
                connection,
                slide_ui_sql="ui JSON DEFAULT '{}'",
            )
            report = migrations.validate_template_v2_phase_one_schema(connection)
            assert not report.complete
            assert (
                "slides.ui must not have a server default"
                in report.incompatible
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(
            RuntimeError,
            match=r"slides\.ui must not have a server default",
        ):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
        with engine.connect() as connection:
            assert "alembic_version" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_null_provenance_mode_stops_f3_upgrade_before_any_ddl(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'null-provenance-preflight.db'}"
    engine = create_engine(database_url)
    source_id = "11111111111141118111111111111111"
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(connection)
            connection.exec_driver_sql("DROP TABLE slides")
            connection.exec_driver_sql(
                """
                CREATE TABLE slides (
                    id CHAR(32) NOT NULL,
                    html_content TEXT,
                    PRIMARY KEY (id)
                )
                """
            )
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (id, mode, version)
                    VALUES (:id, NULL, 'v2-standard')
                    """
                ),
                {"id": source_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO template_v2
                        (id, presentation_id, name)
                    VALUES ('null-mode', :source_id, 'Null mode provenance')
                    """
                ),
                {"source_id": source_id},
            )
            report = migrations.validate_template_v2_phase_one_schema(connection)
            assert not report.complete
            assert (
                "template_v2 contains invalid presentation provenance"
                in report.incompatible
            )
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) VALUES (:revision)"
                ),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        with pytest.raises(
            RuntimeError,
            match="invalid presentation provenance",
        ):
            command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_SHARE_TOKEN
            )
            assert "ui" not in {
                column["name"]
                for column in inspector.get_columns("slides")
            }
            assert migrations.SLIDE_UI_CHECK_CONSTRAINT not in {
                constraint.get("name")
                for constraint in inspector.get_check_constraints("slides")
            }
            assert not any(
                name.startswith("_alembic_tmp")
                for name in inspector.get_table_names()
            )
    finally:
        engine.dispose()


def test_unversioned_fake_named_check_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'fake-check-unversioned.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(
                connection,
                slide_check_sql="1 = 1",
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(RuntimeError, match="incorrect SQL semantics"):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "stored_ui",
    [
        "not-json",
        json.dumps("scalar"),
        json.dumps({"id": "incomplete"}),
    ],
    ids=["invalid-json", "json-scalar", "invalid-layout"],
)
def test_unversioned_corrupt_native_ui_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
    stored_ui,
):
    database_url = f"sqlite:///{tmp_path / 'corrupt-ui-unversioned.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(connection)
            connection.execute(
                text("INSERT INTO slides (id, ui) VALUES ('corrupt', :ui)"),
                {"ui": stored_ui},
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(RuntimeError, match="invalid native UI payload"):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


def test_unversioned_invalid_provenance_uuid_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'invalid-uuid-unversioned.db'}"
    engine = create_engine(database_url)
    source_id = "not-a-uuid"
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (id, mode, version)
                    VALUES (:id, 'template', 'v2-standard')
                    """
                ),
                {"id": source_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO template_v2
                        (id, presentation_id, name)
                    VALUES ('corrupt', :source_id, 'Corrupt provenance')
                    """
                ),
                {"source_id": source_id},
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(RuntimeError, match="invalid presentation UUID"):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda connection: connection.execute(
                text("ALTER TABLE template_v2 ADD COLUMN foreign_payload JSON")
            ),
            "unexpected columns foreign_payload",
        ),
        (
            None,
            r"template_v2\.name has an incompatible type",
        ),
    ],
    ids=["unexpected-column", "bounded-string"],
)
def test_unversioned_alien_template_schema_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
    mutation,
    expected_error,
):
    database_url = f"sqlite:///{tmp_path / 'alien-template-unversioned.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(
                connection,
                **(
                    {}
                    if mutation is not None
                    else {"template_name_sql": "name VARCHAR(1) NOT NULL"}
                ),
            )
            if mutation is not None:
                mutation(connection)

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(RuntimeError, match=expected_error):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


def test_unversioned_unexpected_template_default_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'unexpected-default-unversioned.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(
                connection,
                template_name_sql=(
                    "name VARCHAR DEFAULT 'unexpected' NOT NULL"
                ),
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(RuntimeError, match="incorrect server default"):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


def test_postgres_timestamp_contract_requires_timezone():
    assert template_v2_schema_contract._is_datetime(
        {"type": sa.DateTime(timezone=True)},
        dialect_name="postgresql",
    )
    assert not template_v2_schema_contract._is_datetime(
        {"type": sa.DateTime(timezone=False)},
        dialect_name="postgresql",
    )
    # SQLite reflection cannot preserve timezone intent.
    assert template_v2_schema_contract._is_datetime(
        {"type": sa.DateTime(timezone=False)},
        dialect_name="sqlite",
    )


def test_unversioned_null_template_required_field_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'null-template-unversioned.db'}"
    engine = create_engine(database_url)
    source_id = "11111111111141118111111111111111"
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(
                connection,
                template_name_sql="name VARCHAR",
            )
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (id, mode, version)
                    VALUES (:id, 'template', 'v2-standard')
                    """
                ),
                {"id": source_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO template_v2
                        (id, presentation_id, name)
                    VALUES ('corrupt', :source_id, NULL)
                    """
                ),
                {"source_id": source_id},
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(
            RuntimeError,
            match="NULL required fields|incorrect nullability",
        ):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


def test_unversioned_null_presentation_version_is_rejected_before_stamp(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'null-version-unversioned.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _create_complete_phase_one_schema(
                connection,
                presentation_version_sql=(
                    "version VARCHAR DEFAULT 'v1-standard'"
                ),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (id, mode, version)
                    VALUES ('null-version', 'template', NULL)
                    """
                )
            )

        stamp_calls = []
        monkeypatch.setattr(
            migrations.command,
            "stamp",
            lambda *_args: stamp_calls.append(_args),
        )
        with pytest.raises(RuntimeError, match="NULL version rows|NOT NULL"):
            migrations._stamp_legacy_database_if_needed(
                _alembic_config(database_url),
                database_url,
            )
        assert stamp_calls == []
    finally:
        engine.dispose()


def test_mixed_ui_html_row_stops_f3_upgrade_before_any_ddl(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'mixed-row-preflight.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id CHAR(32) NOT NULL,
                        share_token VARCHAR,
                        version VARCHAR DEFAULT 'v1-standard' NOT NULL,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    CREATE TABLE slides (
                        id CHAR(32) NOT NULL,
                        html_content TEXT,
                        ui JSON,
                        PRIMARY KEY (id),
                        CONSTRAINT {migrations.SLIDE_UI_CHECK_CONSTRAINT}
                            CHECK (
                                ui IS NULL
                                OR html_content IS NULL
                                OR html_content = ''
                            )
                    )
                    """
                )
            )
            connection.execute(text("PRAGMA ignore_check_constraints = ON"))
            connection.execute(
                text(
                    """
                    INSERT INTO slides (id, html_content, ui)
                    VALUES ('mixed', '<section>bad</section>', '{"id":"bad"}')
                    """
                )
            )
            connection.execute(text("PRAGMA ignore_check_constraints = OFF"))
            connection.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) VALUES (:revision)"
                ),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        with pytest.raises(RuntimeError, match="mixed native UI"):
            command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_SHARE_TOKEN
            )
            assert "template_v2" not in inspector.get_table_names()
            assert not any(
                name.startswith("_alembic_tmp")
                for name in inspector.get_table_names()
            )
    finally:
        engine.dispose()


def test_frozen_revision_rejects_invalid_native_ui_before_any_ddl(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'invalid-native-ui-at-f3.db'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id TEXT PRIMARY KEY,
                        share_token TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    CREATE TABLE slides (
                        id TEXT PRIMARY KEY,
                        html_content TEXT,
                        ui JSON,
                        CONSTRAINT {migrations.SLIDE_UI_CHECK_CONSTRAINT}
                            CHECK (
                                ui IS NULL
                                OR html_content IS NULL
                                OR html_content = ''
                            )
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO slides (id, ui)
                    VALUES ('invalid-native', '{"id":"incomplete"}')
                    """
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES (:revision)"
                ),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        with pytest.raises(RuntimeError, match="invalid native UI payload"):
            command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_SHARE_TOKEN
            )
            assert "version" not in {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            assert "template_v2" not in inspector.get_table_names()
    finally:
        engine.dispose()


def test_template_v2_upgrade_downgrade_upgrade_cycle(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-cycle.db'}"
    engine = create_engine(database_url)
    config = _alembic_config(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id TEXT PRIMARY KEY,
                        share_token TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE slides (
                        id TEXT PRIMARY KEY,
                        html_content TEXT
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == _script_head(database_url)
            )
            assert "presentation_generation_jobs" in inspector.get_table_names()
            assert "lifecycle_status" in {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            assert migrations.TEMPLATE_V2_EXPECTED_COLUMNS.issubset(
                {
                    column["name"]
                    for column in inspector.get_columns("template_v2")
                }
            )
            assert set(
                inspector.get_pk_constraint("template_v2")[
                    "constrained_columns"
                ]
            ) == {"id"}

        command.downgrade(config, migrations.REVISION_SHARE_TOKEN)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_SHARE_TOKEN
            )
            assert "template_v2" not in inspector.get_table_names()
            assert "version" not in {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            assert "ui" not in {
                column["name"] for column in inspector.get_columns("slides")
            }

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == _script_head(database_url)
            )
            assert "template_v2" in inspector.get_table_names()
            assert "presentation_generation_jobs" in inspector.get_table_names()
            assert migrations.SLIDE_UI_CHECK_CONSTRAINT in {
                constraint["name"]
                for constraint in inspector.get_check_constraints("slides")
            }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("mutation_sql", "parameters", "expected_reason"),
    [
        (
            """
            INSERT INTO presentations (id)
            VALUES (:presentation_id)
            """,
            {"presentation_id": "11111111111111111111111111111111"},
            "template_v2 contains rows",
        ),
        (
            """
            INSERT INTO slides (id, ui)
            VALUES (:slide_id, :ui)
            """,
            {
                "slide_id": "native-slide",
                "ui": json.dumps(
                    {
                        "id": "native-layout",
                        "description": "A persisted native slide layout",
                        "components": [],
                    }
                ),
            },
            "slides.ui contains native layouts",
        ),
        (
            """
            INSERT INTO presentations (id, version)
            VALUES (:presentation_id, 'v2-standard')
            """,
            {"presentation_id": "22222222222222222222222222222222"},
            "presentations.version contains non-legacy values",
        ),
    ],
    ids=["template-row", "native-slide-ui", "non-legacy-version"],
)
def test_template_v2_downgrade_refuses_data_loss_before_any_ddl(
    tmp_path,
    mutation_sql,
    parameters,
    expected_reason,
):
    database_url = f"sqlite:///{tmp_path / f'downgrade-{expected_reason}.db'}"
    engine = create_engine(database_url)
    config = _alembic_config(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE presentations (
                        id TEXT PRIMARY KEY,
                        share_token TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE slides (
                        id TEXT PRIMARY KEY,
                        html_content TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES (:revision)"
                ),
                {"revision": migrations.REVISION_SHARE_TOKEN},
            )

        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text(mutation_sql), parameters)
            if expected_reason == "template_v2 contains rows":
                connection.execute(
                    text(
                        """
                        INSERT INTO template_v2
                            (id, presentation_id, name)
                        VALUES ('owned-template', :presentation_id, 'Owned')
                        """
                    ),
                    parameters,
                )

        with pytest.raises(RuntimeError, match=expected_reason):
            command.downgrade(config, migrations.REVISION_SHARE_TOKEN)

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_TEMPLATE_V2_PHASE_ONE
            )
            assert "template_v2" in inspector.get_table_names()
            assert "version" in {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            assert "ui" in {
                column["name"] for column in inspector.get_columns("slides")
            }
            assert migrations.SLIDE_UI_CHECK_CONSTRAINT in {
                constraint["name"]
                for constraint in inspector.get_check_constraints("slides")
            }
    finally:
        engine.dispose()
