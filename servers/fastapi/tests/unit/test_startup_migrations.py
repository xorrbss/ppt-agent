from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import migrations


FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _schema_signature(engine) -> tuple:
    with engine.connect() as connection:
        inspector = inspect(connection)
        return tuple(
            (
                table_name,
                tuple(
                    sorted(
                        (
                            column["name"],
                            str(column["type"]),
                            bool(column["nullable"]),
                        )
                        for column in inspector.get_columns(table_name)
                    )
                ),
                tuple(
                    sorted(
                        (
                            index.get("name"),
                            tuple(index.get("column_names") or ()),
                            bool(index.get("unique")),
                        )
                        for index in inspector.get_indexes(table_name)
                    )
                ),
            )
            for table_name in sorted(inspector.get_table_names())
        )


def test_startup_migration_upgrades_old_sqlite_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
):
    database_path = tmp_path / "old-startup.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _alembic_config(database_url)

    command.upgrade(config, migrations.REVISION_BEFORE_TEMPLATE_CREATE_INFO)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO keyvaluesqlmodel (id, key, value)
                    VALUES (:id, :key, :value)
                    """
                ),
                {
                    "id": "00000000000000000000000000000001",
                    "key": "startup-migration-sentinel",
                    "value": '{"preserved": true}',
                },
            )

        monkeypatch.setenv("DATABASE_URL", database_url)
        migrations._run_migrations()

        expected_head = ScriptDirectory.from_config(config).get_current_head()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head
            assert connection.execute(
                text(
                    "SELECT value FROM keyvaluesqlmodel "
                    "WHERE key = 'startup-migration-sentinel'"
                )
            ).scalar_one() == '{"preserved": true}'
        first_signature = _schema_signature(engine)

        # A second application startup must be a no-op for both schema and data.
        migrations._run_migrations()

        assert _schema_signature(engine) == first_signature
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head
            assert connection.execute(
                text(
                    "SELECT value FROM keyvaluesqlmodel "
                    "WHERE key = 'startup-migration-sentinel'"
                )
            ).scalar_one() == '{"preserved": true}'
    finally:
        engine.dispose()
