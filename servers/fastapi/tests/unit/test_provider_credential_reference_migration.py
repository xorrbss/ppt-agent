from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


PREVIOUS_REVISION = "e4f8a2c6d0b3"
CURRENT_REVISION = "d4f8a0b2c6e1"
TABLE = "provider_credential_references"


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_provider_credential_reference_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'provider-credential-reference.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, CURRENT_REVISION)
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns(TABLE)} == {
            "id", "tenant_id", "user_id", "provider", "backend",
            "credential_ref", "created_at", "updated_at",
        }
        assert "secret" not in {column["name"] for column in inspector.get_columns(TABLE)}
        assert {index["name"] for index in inspector.get_indexes(TABLE)} >= {
            "ix_provider_credential_references_user_id",
            "uq_provider_credential_references_scope_provider",
        }
        command.downgrade(config, PREVIOUS_REVISION)
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
