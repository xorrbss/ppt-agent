from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


PREVIOUS_REVISION = "d2b5e8f0a3c6"
CURRENT_REVISION = "d3e7a9c2b4f6"
TABLE = "provider_usage_receipts"


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_provider_usage_receipt_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'provider-usage-migration.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, CURRENT_REVISION)
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        assert {column["name"] for column in inspector.get_columns(TABLE)} == {
            "id", "job_id", "operation", "stage", "provider", "model",
            "receipt_key_hash", "retry_class", "outcome", "latency_ms", "usage_state",
            "input_tokens", "output_tokens", "request_count",
            "reported_cost_microunits", "estimated_cost_microunits",
            "pricing_version", "created_at", "updated_at",
        }
        assert {index["name"] for index in inspector.get_indexes(TABLE)} >= {
            "ix_provider_usage_receipts_job_id",
            "ix_provider_usage_receipts_job_stage",
        }
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO provider_usage_receipts "
                    "(id, job_id, operation, stage, provider, model, "
                    "receipt_key_hash, retry_class, outcome, latency_ms, "
                    "usage_state, input_tokens, output_tokens, request_count, "
                    "reported_cost_microunits, estimated_cost_microunits, "
                    "pricing_version) VALUES "
                    "(:id, :job_id, 'generation', 'author', 'openai', 'model', "
                    ":digest, 'initial', 'succeeded', 1, 'unavailable', 0, NULL, "
                    "1, NULL, NULL, NULL)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "job_id": uuid.uuid4().hex,
                    "digest": "a" * 64,
                },
            )
        command.downgrade(config, PREVIOUS_REVISION)
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
