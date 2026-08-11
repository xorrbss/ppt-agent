from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import migrations


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _inferred_revision(database_url: str) -> str:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            presentation_columns = {
                column["name"] for column in inspector.get_columns("presentations")
            }
            return migrations._post_reference_schema_revision(
                inspector,
                tables,
                presentation_columns,
            )
    finally:
        engine.dispose()


def test_post_reference_inference_requires_each_physical_revision_marker(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'post-reference-chain.db').as_posix()}"
    config = _alembic_config(database_url)
    expected_chain = (
        (
            migrations.REVISION_REFERENCE_SNAPSHOTS,
            migrations.REVISION_REFERENCE_SNAPSHOTS,
        ),
        (
            migrations.REVISION_ASSET_PROFILE_COMPATIBILITY,
            migrations.REVISION_ASSET_PROFILE_COMPATIBILITY,
        ),
        (
            migrations.REVISION_PRESENTATION_REFERENCE_SNAPSHOTS,
            migrations.REVISION_PRESENTATION_REFERENCE_SNAPSHOTS,
        ),
        (
            migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY,
            migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY,
        ),
        (
            migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION,
            migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY,
        ),
        (
            migrations.REVISION_PRESENTATION_EXPORT_ATTEMPTS,
            migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY,
        ),
    )

    for revision, inferred_revision in expected_chain:
        command.upgrade(config, revision)
        assert _inferred_revision(database_url) == inferred_revision


def test_post_reference_inference_rejects_partial_asset_profile_schema(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'partial-asset-profile.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, migrations.REVISION_REFERENCE_SNAPSHOTS)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE presentations ADD COLUMN profile_driver_id VARCHAR(32)")
            )
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            presentation_columns = {
                column["name"] for column in inspector.get_columns("presentations")
            }
            with pytest.raises(
                RuntimeError,
                match="Asset/profile compatibility schema is only partially applied",
            ):
                migrations._post_reference_schema_revision(
                    inspector,
                    tables,
                    presentation_columns,
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("marker", ["attempt_table", "artifact_link"])
def test_post_reference_inference_rejects_one_sided_d2_marker(
    tmp_path: Path,
    marker: str,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'partial-d2-{marker}.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            if marker == "attempt_table":
                connection.execute(
                    text("CREATE TABLE presentation_export_attempts (id CHAR(32))")
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE workspace_artifacts "
                        "ADD COLUMN export_attempt_id CHAR(32)"
                    )
                )
        with pytest.raises(RuntimeError, match="only partially applied"):
            _inferred_revision(database_url)
    finally:
        engine.dispose()


def test_unversioned_orphan_receipt_is_stamped_at_c0_and_rejected_by_d1(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'orphan-receipt.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.execute(
                text(
                    "INSERT INTO asset_gateway_idempotency_receipts ("
                    "id, owner_scope, operation, key_hash, request_sha256, state, "
                    "asset_id, expires_at, created_at, updated_at) VALUES ("
                    ":id, 'anonymous', 'upload', :key_hash, :request_sha256, "
                    "'pending', :asset_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "id": "1" * 32,
                    "key_hash": "a" * 64,
                    "request_sha256": "b" * 64,
                    "asset_id": "f" * 32,
                },
            )
            connection.execute(text("DELETE FROM alembic_version"))
            connection.commit()
        assert (
            _inferred_revision(database_url)
            == migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY
        )
        command.stamp(config, migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY)
        with pytest.raises(RuntimeError, match="orphaned asset_id"):
            command.upgrade(config, migrations.REVISION_POST_C0_SCHEMA_RECONCILIATION)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY
            assert connection.execute(
                text(
                    "SELECT asset_id FROM asset_gateway_idempotency_receipts "
                    "WHERE id = :id"
                ),
                {"id": "1" * 32},
            ).scalar_one() == "f" * 32
    finally:
        engine.dispose()
