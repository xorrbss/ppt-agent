from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import migrations


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "provider_runtime_budgets",
    "provider_runtime_reservations",
    "provider_runtime_circuits",
}


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_provider_runtime_revision_is_single_head_and_round_trips(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'provider-runtime.db'}"
    config = _config(database_url)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert TABLES.issubset(inspector.get_table_names())
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                migrations.REVISION_PROVIDER_RUNTIME_RESERVATIONS
            )
            budget_foreign_keys = {
                (
                    tuple(item.get("constrained_columns") or ()),
                    item.get("referred_table"),
                    str((item.get("options") or {}).get("ondelete") or "").upper(),
                )
                for item in inspector.get_foreign_keys("provider_runtime_budgets")
            }
            assert (("owner_id",), "user", "CASCADE") in budget_foreign_keys
            assert (
                ("job_id",),
                "presentation_generation_jobs",
                "CASCADE",
            ) in budget_foreign_keys

        command.downgrade(config, migrations.REVISION_AUTHORED_VNEXT_OWNERSHIP)
        with engine.connect() as connection:
            assert not TABLES.intersection(inspect(connection).get_table_names())

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert TABLES.issubset(inspect(connection).get_table_names())
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                migrations.REVISION_PROVIDER_RUNTIME_RESERVATIONS
            )
    finally:
        engine.dispose()
