from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


PREVIOUS_REVISION = "b4d6f8a1c3e5"
CURRENT_REVISION = "c5e7f9a1b3d4"
TABLE_NAME = "authored_exact_provenance"


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_exact_provenance_migration_adds_private_one_to_one_cascade_table(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-provenance-migration.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)

    engine = create_engine(database_url)
    try:
        assert TABLE_NAME not in inspect(engine).get_table_names()

        command.upgrade(config, CURRENT_REVISION)
        inspector = inspect(engine)
        assert TABLE_NAME in inspector.get_table_names()
        assert inspector.get_pk_constraint(TABLE_NAME)["constrained_columns"] == [
            "presentation_id"
        ]
        assert {column["name"] for column in inspector.get_columns(TABLE_NAME)} == {
            "presentation_id",
            "provenance",
        }
        foreign_keys = inspector.get_foreign_keys(TABLE_NAME)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["referred_table"] == "presentations"
        assert foreign_keys[0]["constrained_columns"] == ["presentation_id"]
        assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"

        command.downgrade(config, PREVIOUS_REVISION)
        assert TABLE_NAME not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
