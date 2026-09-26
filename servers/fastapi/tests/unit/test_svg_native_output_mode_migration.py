"""``svg-native`` output mode migration: chain position and SQLite round trip."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import migration_revision_contract as revision_contract

FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_identity(connection, output_mode: str) -> None:
    connection.execute(text(
        "INSERT INTO public_export_identities (id, owner_id, tenant_id, presentation_id, revision, "
        "canonical_digest, scene_digest, semantic_digest, quality_digest, exporter_digest, export_mode, "
        "output_mode, font_manifest_digest, exporter_version, render_adapter, identity_contract_version, "
        "reuse_eligible, source_artifact_id, state, claimed_by_operation_id, worker_id, attempt_token_hash, "
        "fencing_token, attempt_count, lease_expires_at) VALUES (:id, :owner, 't', :presentation, 1, "
        ":d, :d, :d, :d, :d, 'pptx', :mode, :d, 'v', 'public-isolated-svg', 'digest-bound/v2', 1, :source, "
        "'running', :operation, 'w', :token, 1, 1, '2026-09-26 00:00:00')"
    ), {"id": uuid.uuid4().hex, "owner": uuid.uuid4().hex, "presentation": uuid.uuid4().hex, "d": "a" * 64,
        "mode": output_mode, "source": uuid.uuid4().hex, "operation": uuid.uuid4().hex,
        "token": "sha256:" + "b" * 64})


def test_svg_native_output_mode_is_the_single_linear_head() -> None:
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert script.get_heads() == [revision_contract.HEAD_REVISION]
    assert revision_contract.HEAD_REVISION == revision_contract.REVISION_SVG_NATIVE_EXPORT_OUTPUT_MODE
    revision = script.get_revision(revision_contract.REVISION_SVG_NATIVE_EXPORT_OUTPUT_MODE)
    assert revision.down_revision == revision_contract.REVISION_REMOVE_TEMPLATE_V2


def test_svg_native_output_mode_round_trips_the_constraint_on_sqlite(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'svg-native.db').as_posix()}"
    config = _alembic_config(database_url)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, revision_contract.REVISION_REMOVE_TEMPLATE_V2)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_identity(connection, "svg-native")

        command.upgrade(config, "head")
        with engine.begin() as connection:
            _insert_identity(connection, "svg-native")
            _insert_identity(connection, "editable-hybrid")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_identity(connection, "raster-lie")

        # A live svg-native identity holds artifact references: downgrade refuses.
        with pytest.raises(RuntimeError, match="svg-native export identities must be retained"):
            command.downgrade(config, "-1")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM public_export_identities WHERE output_mode = 'svg-native'"))
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                revision_contract.REVISION_REMOVE_TEMPLATE_V2)
            assert connection.execute(text("SELECT count(*) FROM public_export_identities")).scalar_one() == 1
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_identity(connection, "svg-native")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
