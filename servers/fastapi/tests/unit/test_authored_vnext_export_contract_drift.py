"""Drift gates for the shared authored-vNext Python/TypeScript export contract."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, inspect
import pytest

from services.authored_vnext.integration.sql_repository import (
    validate_exportable_deck_payload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "authored_vnext" / "export"


def _module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_python_and_typescript_exports_match_json_schema() -> None:
    generator = _module("authored_vnext_export_generator", CONTRACT_ROOT / "generate.py")

    assert generator.generate(check=True, repository_root=REPOSITORY_ROOT) == 0
    outputs = generator.render_outputs(REPOSITORY_ROOT)
    assert set(path.suffix for path in outputs) == {".py", ".ts"}
    assert all(path.read_bytes() == content for path, content in outputs.items())


def test_worker_envelopes_are_strict_versioned_and_share_fencing_contract() -> None:
    schema = json.loads(
        (CONTRACT_ROOT / "v1" / "export-contract.schema.json").read_text("utf-8")
    )
    validator = Draft202012Validator(schema)
    generated = _module(
        "authored_vnext_export_generated",
        CONTRACT_ROOT / "v1" / "generated" / "export_contract.py",
    )
    digest = "a" * 64
    request = {
        "schemaVersion": "authored-vnext-ooxml-worker/v1",
        "operation": "EXPORT_PPTX",
        "operationId": "operation:contract-drift",
        "jobId": "export-job:contract-drift",
        "fencingToken": 3,
        "attempt": 1,
        "timeoutMs": 30_000,
        "deadline": "2026-08-13T12:00:00Z",
        "master": {"contractVersion": "deckmaster-contract/v1"},
        "slides": [
            {"slideId": "slide:one", "layoutId": "layout:one", "values": {}}
        ],
        "assets": [],
        "digests": {
            "masterSha256": digest,
            "specSha256": digest,
            "assetManifestSha256": digest,
            "fontManifestSha256": digest,
            "packageSha256": None,
        },
        "resumeFromReceipt": None,
    }

    assert list(validator.evolve(schema=schema["$defs"]["WorkerRequestEnvelope"]).iter_errors(request)) == []
    parsed = generated.WorkerRequestEnvelope.model_validate(request)
    assert parsed.fencing_token == 3
    assert parsed.model_dump(mode="json", by_alias=True) == request

    stale = {**request, "fencingToken": "3"}
    assert list(validator.evolve(schema=schema["$defs"]["WorkerRequestEnvelope"]).iter_errors(stale))
    assert generated.WorkerRequestEnvelope.model_validate_json(
        json.dumps(request, separators=(",", ":"))
    ) == parsed


def test_generator_check_reports_and_repairs_contract_drift(tmp_path: Path) -> None:
    generator = _module("authored_vnext_export_generator_copy", CONTRACT_ROOT / "generate.py")
    destination = tmp_path / "contracts" / "authored_vnext" / "export" / "v1"
    destination.mkdir(parents=True)
    destination.joinpath("export-contract.schema.json").write_bytes(
        (CONTRACT_ROOT / "v1" / "export-contract.schema.json").read_bytes()
    )

    assert generator.generate(check=True, repository_root=tmp_path) == 1
    assert generator.generate(check=False, repository_root=tmp_path) == 0
    assert generator.generate(check=True, repository_root=tmp_path) == 0
    generated_python = destination / "generated" / "export_contract.py"
    generated_python.write_text("# stale\n", encoding="utf-8")
    assert generator.generate(check=True, repository_root=tmp_path) == 1


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
