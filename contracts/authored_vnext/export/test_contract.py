"""Focused schema, generated model, and drift tests for the export contract."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest


EXPORT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXPORT_DIRECTORY.parents[2]
SCHEMA_PATH = EXPORT_DIRECTORY / "v1" / "export-contract.schema.json"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(character: str = "a") -> str:
    return "sha256:" + character * 64


def _raw_digest(character: str = "a") -> str:
    return character * 64


def _validate(definition: str, payload: object) -> list[object]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    )
    return list(validator.iter_errors(payload))


def _create_request() -> dict[str, object]:
    return {
        "schemaVersion": "authored-vnext-export/v1",
        "ownerScope": {"tenantId": "tenant-1", "ownerId": "owner-1"},
        "deckId": "deck:quarterly",
        "expectedDeckRevision": 3,
        "expectedDeckDigest": _digest("1"),
        "masterId": "master:brand",
        "expectedMasterRevision": 2,
        "expectedMasterDigest": _digest("2"),
        "expectedActiveArtifactRevision": None,
        "expectedActiveArtifactDigest": None,
        "slideSpecDigests": [{"slideId": "slide:intro", "revision": 4, "digest": _digest("3")}],
        "assetDigests": [{"assetId": "asset:logo", "digest": _digest("4")}],
        "fontDigest": _digest("5"),
        "packageDigest": None,
        "idempotencyKey": "export:quarterly:0001",
        "operationFence": 7,
        "timeoutMs": 60000,
        "maxAttempts": 3,
    }


def test_schema_is_closed_and_fails_digest_or_fence_mismatch_inputs() -> None:
    Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    payload = _create_request()
    assert _validate("CreateExportJobRequest", payload) == []
    assert _validate("CreateExportJobRequest", {**payload, "storagePath": "secret/file.pptx"})
    assert _validate("CreateExportJobRequest", {**payload, "expectedMasterDigest": "bad"})
    assert _validate("CreateExportJobRequest", {**payload, "operationFence": 0})


def test_worker_buffer_is_internal_and_public_receipt_is_opaque() -> None:
    receipt_properties = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["ArtifactReceipt"]["properties"]
    assert "contentBase64" not in receipt_properties
    assert "storageKey" not in receipt_properties
    assert "path" not in receipt_properties
    assert {"artifactId", "downloadUrl", "packageReadbackDigest"} <= receipt_properties.keys()

    worker = {
        "schemaVersion": "authored-vnext-ooxml-worker/v1",
        "operation": "EXPORT_PPTX",
        "operationId": "operation-0001",
        "jobId": "export-job:job-1",
        "fencingToken": 7,
        "attempt": 1,
        "timeoutMs": 60000,
        "deadline": "2026-08-13T12:00:00Z",
        "master": {"contractVersion": "deckmaster-contract/v1"},
        "slides": [{"slideId": "slide:intro", "layoutId": "layout:standard", "values": {"title": "Hello"}}],
        "assets": [],
        "digests": {
            "masterSha256": _raw_digest("1"), "specSha256": _raw_digest("2"),
            "assetManifestSha256": _raw_digest("3"), "fontManifestSha256": _raw_digest("4"),
            "packageSha256": None,
        },
        "resumeFromReceipt": None,
    }
    assert _validate("WorkerRequestEnvelope", worker) == []


def test_generated_python_models_are_strict_closed_and_immutable() -> None:
    generated = _load("authored_vnext_export_generated", EXPORT_DIRECTORY / "v1" / "generated" / "export_contract.py")
    request = generated.CreateExportJobRequest.model_validate(_create_request())
    assert request.operation_fence == 7
    assert request.owner_scope.tenant_id == "tenant-1"
    with pytest.raises(Exception):
        generated.CreateExportJobRequest.model_validate({**_create_request(), "operationFence": 7.0})
    with pytest.raises(Exception):
        request.operation_fence = 8


def test_generator_check_detects_and_repairs_drift(tmp_path: Path) -> None:
    generator = _load("authored_vnext_export_generator", EXPORT_DIRECTORY / "generate.py")
    target_schema = tmp_path / "contracts" / "authored_vnext" / "export" / "v1" / SCHEMA_PATH.name
    target_schema.parent.mkdir(parents=True)
    target_schema.write_bytes(SCHEMA_PATH.read_bytes())
    assert generator.generate(check=True, repository_root=tmp_path) == 1
    assert generator.generate(check=False, repository_root=tmp_path) == 0
    assert generator.generate(check=True, repository_root=tmp_path) == 0
    generated_ts = target_schema.parent / "generated" / "export-contract.ts"
    generated_ts.write_text("// drift\n", encoding="utf-8")
    assert generator.generate(check=True, repository_root=tmp_path) == 1
