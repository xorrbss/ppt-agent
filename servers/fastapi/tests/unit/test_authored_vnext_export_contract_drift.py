"""Deck payload validation and migration reversibility of the authored-vNext SQL repository."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import create_engine, inspect
import pytest

from services.authored_vnext.integration.sql_repository import (
    validate_exportable_deck_payload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schemaVersion": "deck-document/v2"},
        {"master": {}, "slides": []},
        [],
    ],
)
def test_export_repository_requires_explicit_supported_deck_schema(payload) -> None:
    with pytest.raises(ValueError, match="authored_vnext_deck_(?:schema_unsupported|payload_invalid)"):
        validate_exportable_deck_payload(payload)


def test_authored_vnext_migration_upgrade_and_downgrade_are_reversible(
    monkeypatch, tmp_path: Path
) -> None:
    migration = _module(
        "authored_vnext_repository_migration",
        REPOSITORY_ROOT
        / "servers"
        / "fastapi"
        / "alembic"
        / "versions"
        / "e4f8a2c6d0b3_add_authored_vnext_sql_repository.py",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite3'}")
    expected = {table.name for table in migration.AUTHORED_VNEXT_SQL_TABLES}
    try:
        with engine.begin() as connection:
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            migration.upgrade()
            assert expected <= set(inspect(connection).get_table_names())
            migration.upgrade()
            assert expected <= set(inspect(connection).get_table_names())
            migration.downgrade()
            assert expected.isdisjoint(inspect(connection).get_table_names())
            migration.downgrade()
            assert expected.isdisjoint(inspect(connection).get_table_names())
    finally:
        engine.dispose()
