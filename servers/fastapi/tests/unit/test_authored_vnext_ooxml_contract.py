from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "authored_vnext" / "ooxml"
SCHEMA_PATH = CONTRACT_ROOT / "v1" / "ooxml-package.schema.json"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(character: str = "a") -> str:
    return "sha256:" + character * 64


def _command() -> dict[str, object]:
    return {
        "schemaVersion": "authored-vnext-ooxml-operation-command/v1",
        "operation": "PATCH_TEXT_SLICE",
        "expectedRevision": 1,
        "expectedArtifactDigest": _digest(),
        "idempotencyKey": "edit-1",
        "expectedAccountGeneration": 0,
        "partName": "ppt/slides/slide1.xml",
        "selector": {"kind": "placeholder", "idx": 0, "role": "title"},
        "expectedText": "Before",
        "replacementText": "After",
    }


def test_schema_and_generated_models_are_closed_strict_and_immutable() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        {
            "$ref": "#/$defs/PackageOperationCommand",
            "$defs": schema["$defs"],
        }
    )
    assert list(validator.iter_errors(_command())) == []
    assert list(validator.iter_errors({**_command(), "storagePath": "secret"}))

    generated = _load(
        "authored_vnext_ooxml_generated_test",
        CONTRACT_ROOT / "v1" / "generated" / "ooxml_package.py",
    )
    command = generated.PackageOperationCommand.model_validate(_command())
    assert command.operation == "PATCH_TEXT_SLICE"
    with pytest.raises(ValidationError):
        generated.PackageOperationCommand.model_validate(
            {**_command(), "expectedRevision": 1.0}
        )
    with pytest.raises(ValidationError):
        command.operation = "NOOP_SAVE"


def test_python_and_typescript_contract_generation_fails_on_drift(
    tmp_path: Path,
) -> None:
    generator = _load("authored_vnext_ooxml_generator", CONTRACT_ROOT / "generate.py")
    target = (
        tmp_path
        / "contracts"
        / "authored_vnext"
        / "ooxml"
        / "v1"
        / SCHEMA_PATH.name
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(SCHEMA_PATH.read_bytes())
    assert generator.generate(check=True, repository_root=tmp_path) == 1
    assert generator.generate(check=False, repository_root=tmp_path) == 0
    assert generator.generate(check=True, repository_root=tmp_path) == 0
    generated_ts = target.parent / "generated" / "ooxml-package.ts"
    next_generated_ts = (
        tmp_path
        / "servers"
        / "nextjs"
        / "lib"
        / "ooxml-vnext"
        / "generated"
        / "ooxml-package.ts"
    )
    assert next_generated_ts.read_bytes() == generated_ts.read_bytes()
    generated_ts.write_text("// drift\n", encoding="utf-8")
    assert generator.generate(check=True, repository_root=tmp_path) == 1
    generator.generate(check=False, repository_root=tmp_path)
    next_generated_ts.write_text("// next drift\n", encoding="utf-8")
    assert generator.generate(check=True, repository_root=tmp_path) == 1
