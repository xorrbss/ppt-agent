from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

import migrations
from models.sql.provider_settings import ProviderSettings
from services.database import _ensure_provider_settings_revision_column


FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_provider_settings_revision_upgrade_downgrade_and_schema_inference(
    tmp_path,
):
    database_url = f"sqlite:///{(tmp_path / 'revision.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(
        config,
        migrations.REVISION_PRESENTATION_SEARCH_AND_SHARE_METRICS,
    )
    engine = create_engine(database_url)
    try:
        assert "revision" not in {
            column["name"]
            for column in inspect(engine).get_columns("provider_settings")
        }

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            revision_column = next(
                column
                for column in inspector.get_columns("provider_settings")
                if column["name"] == "revision"
            )
            assert revision_column["nullable"] is False
            assert "BIGINT" in str(revision_column["type"]).upper()
            tables = set(inspector.get_table_names())
            inferred = migrations._infer_revision_from_schema(
                inspector,
                tables,
                migrations.HEAD_REVISION,
            )
        assert inferred == migrations.HEAD_REVISION

        command.downgrade(
            config,
            migrations.REVISION_PRESENTATION_SEARCH_AND_SHARE_METRICS,
        )
        assert "revision" not in {
            column["name"]
            for column in inspect(engine).get_columns("provider_settings")
        }
    finally:
        engine.dispose()


def test_provider_settings_revision_compiles_as_postgresql_bigint():
    sql = str(
        CreateColumn(ProviderSettings.__table__.c.revision).compile(
            dialect=postgresql.dialect()
        )
    ).upper()

    assert "BIGINT" in sql
    assert "DEFAULT 0" in sql
    assert "NOT NULL" in sql


def test_create_all_compatibility_tops_up_legacy_provider_settings(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'create-all.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE provider_settings ("
                    "id INTEGER PRIMARY KEY, config JSON NOT NULL, "
                    "updated_at DATETIME NOT NULL)"
                )
            )
            _ensure_provider_settings_revision_column(connection)
        revision = next(
            column
            for column in inspect(engine).get_columns("provider_settings")
            if column["name"] == "revision"
        )
        assert revision["nullable"] is False
        assert str(revision["default"]).strip("'\"()") == "0"
    finally:
        engine.dispose()


def test_create_all_compatibility_rejects_incompatible_revision(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'create-all-partial.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE provider_settings ("
                    "id INTEGER PRIMARY KEY, config JSON NOT NULL, "
                    "updated_at DATETIME NOT NULL, "
                    "revision INTEGER NULL DEFAULT 1)"
                )
            )
            with pytest.raises(
                RuntimeError,
                match="Provider settings revision column is incompatible",
            ):
                _ensure_provider_settings_revision_column(connection)
    finally:
        engine.dispose()


def test_pre_c4_create_all_topup_does_not_skip_cumulative_migrations(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pre-c4.db'}"
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_SLIDESPEC_PUBLICATION_ATTEMPTS)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _ensure_provider_settings_revision_column(connection)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                migrations._infer_revision_from_schema(
                    inspector,
                    set(inspector.get_table_names()),
                    migrations.HEAD_REVISION,
                )
                == migrations.REVISION_SLIDESPEC_PUBLICATION_ATTEMPTS
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                migrations._infer_revision_from_schema(
                    inspector,
                    set(inspector.get_table_names()),
                    migrations.HEAD_REVISION,
                )
                == migrations.HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_migration_fails_closed_for_incompatible_existing_revision(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'partial-revision.db'}"
    config = _config(database_url)
    command.upgrade(
        config,
        migrations.REVISION_PRESENTATION_SEARCH_AND_SHARE_METRICS,
    )
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE provider_settings "
                    "ADD COLUMN revision BIGINT"
                )
            )
        with pytest.raises(
            RuntimeError,
            match="provider_settings revision column is incompatible",
        ):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migrations.REVISION_PRESENTATION_SEARCH_AND_SHARE_METRICS
    finally:
        engine.dispose()


def test_migration_repairs_missing_provider_settings_table(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'missing-table.db'}"
    config = _config(database_url)
    command.upgrade(
        config,
        migrations.REVISION_PRESENTATION_SEARCH_AND_SHARE_METRICS,
    )
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE provider_settings"))

        command.upgrade(config, "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = {
                column["name"]: column
                for column in inspector.get_columns("provider_settings")
            }
            assert set(columns) == {"id", "config", "revision", "updated_at"}
            assert columns["revision"]["nullable"] is False
            assert "BIGINT" in str(columns["revision"]["type"]).upper()
            assert str(columns["revision"]["default"]).strip("'\"()") == "0"
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migrations.HEAD_REVISION
    finally:
        engine.dispose()
