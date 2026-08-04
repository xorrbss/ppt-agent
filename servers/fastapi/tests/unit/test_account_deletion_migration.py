from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import migrations
from alembic import command

FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_active_user(engine, *, user_id: str = "11" * 16) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                'INSERT INTO "user" '
                "(id, username, hashed_password, is_active, is_superuser, "
                "is_verified, auth_version) "
                "VALUES (:id, :username, 'test-only', 1, 0, 1, 7)"
            ),
            {"id": user_id, "username": f"user-{user_id[:8]}"},
        )


def test_sqlite_account_deletion_migration_defaults_constraints_and_cycle(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'account-deletion-migration.db'}"
    engine = create_engine(database_url)
    config = _alembic_config(database_url)
    try:
        command.upgrade(config, migrations.REVISION_DURABLE_DELETION)
        _insert_active_user(engine)
        command.upgrade(config, "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = {
                column["name"]: column
                for column in inspector.get_columns("user")
            }
            assert migrations.ACCOUNT_DELETION_USER_COLUMNS.issubset(columns)
            assert columns["deletion_state"]["nullable"] is False
            assert columns["deletion_generation"]["nullable"] is False
            assert str(columns["deletion_generation"]["type"]).upper() == "BIGINT"
            assert migrations.ACCOUNT_DELETION_USER_CHECKS.issubset(
                {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("user")
                }
            )
            assert migrations.ACCOUNT_DELETION_USER_INDEXES.issubset(
                {index["name"] for index in inspector.get_indexes("user")}
            )
            assert connection.execute(
                text(
                    'SELECT deletion_state, deletion_generation, '
                    'deletion_requested_at FROM "user"'
                )
            ).one() == ("active", 0, None)
            assert migrations._infer_revision_from_schema(
                inspector,
                set(inspector.get_table_names()),
                "future-head",
            ) == migrations.REVISION_SLIDESPEC_PUBLICATION_ATTEMPTS

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text('UPDATE "user" SET deletion_state = \'deleting\'')
                )

        command.downgrade(config, migrations.REVISION_DURABLE_DELETION)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert migrations.ACCOUNT_DELETION_USER_COLUMNS.isdisjoint(
                {column["name"] for column in inspector.get_columns("user")}
            )
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migrations.REVISION_DURABLE_DELETION

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    'SELECT deletion_state, deletion_generation, '
                    'deletion_requested_at FROM "user"'
                )
            ).one() == ("active", 0, None)
    finally:
        engine.dispose()


def test_sqlite_account_deletion_downgrade_refuses_live_marker_before_ddl(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'account-deletion-downgrade.db'}"
    engine = create_engine(database_url)
    config = _alembic_config(database_url)
    try:
        command.upgrade(config, "head")
        _insert_active_user(engine, user_id="22" * 16)
        with engine.begin() as connection:
            connection.execute(
                text(
                    'UPDATE "user" SET is_active = 0, auth_version = 8, '
                    "deletion_state = 'deleting', deletion_generation = 1, "
                    "deletion_requested_at = CURRENT_TIMESTAMP"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="cannot downgrade account deletion fence while deletion markers exist",
        ):
            command.downgrade(config, migrations.REVISION_DURABLE_DELETION)

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert migrations.ACCOUNT_DELETION_USER_COLUMNS.issubset(
                {column["name"] for column in inspector.get_columns("user")}
            )
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migrations.REVISION_ACCOUNT_DELETION_FENCE
            assert connection.execute(
                text('SELECT deletion_generation FROM "user"')
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_sqlite_account_deletion_upgrade_rejects_partial_physical_schema(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'account-deletion-partial.db'}"
    engine = create_engine(database_url)
    config = _alembic_config(database_url)
    try:
        command.upgrade(config, migrations.REVISION_DURABLE_DELETION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    'ALTER TABLE "user" ADD COLUMN deletion_state '
                    "VARCHAR(24) NOT NULL DEFAULT 'active'"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="account deletion fence columns are only partially applied",
        ):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migrations.REVISION_DURABLE_DELETION
    finally:
        engine.dispose()
