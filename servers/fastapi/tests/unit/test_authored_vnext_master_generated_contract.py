"""Generated schema/API manifest drift gates for authored vNext Master."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tests.support.authored_vnext_master_fixture import (
    long_title_slide_payload,
    master_contract_payload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
GENERATOR_PATH = (
    REPOSITORY_ROOT / "contracts" / "authored_vnext" / "master" / "generate.py"
)


def _generator():
    spec = spec_from_file_location("authored_vnext_master_contract_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_contracts_match_authoritative_models_and_router() -> None:
    generator = _generator()
    assert generator.generate(check=True, repository_root=REPOSITORY_ROOT) == 0

    schema_directory = (
        REPOSITORY_ROOT / "contracts" / "authored_vnext" / "master" / "v1"
    )
    master_schema = json.loads(
        (schema_directory / "deckmaster-contract.schema.json").read_text("utf-8")
    )
    slide_schema = json.loads(
        (schema_directory / "slide-draft.schema.json").read_text("utf-8")
    )
    assert master_schema["x-pydantic-strict"] is True
    assert slide_schema["x-pydantic-strict"] is True
    assert set(master_schema["$defs"]["LayoutRegistry"]["required"]) == {
        "cover", "section", "standard", "longTitle",
    }
    assert "slots" not in master_schema["$defs"]["MasterLayout"]["required"]
    assert "aiEditZones" not in master_schema["required"]
    assert set(master_schema["$defs"]["AIEditZone"]["required"]) == {
        "id", "scope", "bounds",
    }
    assert list(Draft202012Validator(master_schema).iter_errors(master_contract_payload())) == []
    assert list(Draft202012Validator(slide_schema).iter_errors(long_title_slide_payload())) == []

    extra_master = {**master_contract_payload(), "visualDirection": {}}
    assert list(Draft202012Validator(master_schema).iter_errors(extra_master))
    extra_slide = {**long_title_slide_payload(), "furniture": []}
    assert list(Draft202012Validator(slide_schema).iter_errors(extra_slide))

    furniture_only = master_contract_payload()
    for layout in furniture_only["layouts"].values():
        layout["slots"] = []
    furniture_only["titleStressCorpus"] = []
    furniture_only["aiEditZones"] = [
        {
            "id": "ai-zone:main-content",
            "scope": {"kind": "common"},
            "bounds": {"x": 100.0, "y": 120.0, "width": 1080.0, "height": 500.0},
        }
    ]
    assert list(Draft202012Validator(master_schema).iter_errors(furniture_only)) == []
    furniture_only["aiEditZones"][0]["style"] = {"fontSize": 48}
    assert list(Draft202012Validator(master_schema).iter_errors(furniture_only))


def test_api_wire_manifest_is_camel_case_feature_gated_and_complete() -> None:
    generator = _generator()
    manifest = generator.build_api_wire_manifest()
    assert manifest["router"] == {
        "symbol": "AUTHORED_VNEXT_MASTER_ROUTER",
        "mountPrefix": "/api/v1/ppt",
        "prefix": "/authored-vnext/master",
        "publicPrefix": "/api/v1/ppt/authored-vnext/master",
    }
    assert manifest["featureFlag"] == {
        "name": "AUTHORED_VNEXT_MASTER_ENABLED",
        "default": False,
        "disabledResponse": {
            "status": 404,
            "detail": "authored_vnext_master_disabled",
        },
    }
    assert manifest["versions"] == {
        "deckMasterContract": "deckmaster-contract/v1",
        "slideSpec": "slidespec/v2",
        "compilerPort": "OoxmlMasterCompilerPort",
    }
    endpoints = {
        (endpoint["method"], endpoint["path"]): endpoint
        for endpoint in manifest["endpoints"]
    }
    assert set(endpoints) == {
        ("GET", "/authored-vnext/master/status"),
        ("GET", "/authored-vnext/master/assets/{asset_id}"),
        ("GET", "/authored-vnext/master/masters"),
        ("POST", "/authored-vnext/master/masters/register"),
        ("GET", "/authored-vnext/master/masters/{master_id}/head"),
        ("GET", "/authored-vnext/master/masters/{master_id}/published"),
        (
            "GET",
            "/authored-vnext/master/masters/{master_id}/revisions/{revision}",
        ),
        (
            "POST",
            "/authored-vnext/master/presentations/{presentation_id}/apply",
        ),
        ("POST", "/authored-vnext/master/drafts"),
        ("POST", "/authored-vnext/master/drafts/{master_id}/validate"),
        ("POST", "/authored-vnext/master/drafts/{master_id}/publish"),
        ("POST", "/authored-vnext/master/assets/logo"),
        ("POST", "/authored-vnext/master/preview"),
        ("POST", "/authored-vnext/master/decks"),
        (
            "POST",
            "/authored-vnext/master/decks/{deck_id}/slides/{slide_id}/regenerate-body",
        ),
    }
    assert endpoints[("POST", "/authored-vnext/master/drafts")][
        "requestModel"
    ] == "CreateDraftRequest"
    assert endpoints[("POST", "/authored-vnext/master/preview")][
        "responseModel"
    ] == "PreviewResponse"
    assert endpoints[("POST", "/authored-vnext/master/assets/logo")] == {
        "method": "POST",
        "operation": "upload_master_logo",
        "path": "/authored-vnext/master/assets/logo",
        "requestModel": "UploadFile",
        "responseModel": "MasterAssetReceipt",
        "successStatus": 201,
    }
    assert endpoints[("GET", "/authored-vnext/master/assets/{asset_id}")] == {
        "method": "GET",
        "operation": "get_master_asset",
        "path": "/authored-vnext/master/assets/{asset_id}",
        "requestModel": None,
        "responseModel": None,
        "successStatus": 200,
    }


def test_generator_check_detects_and_repairs_drift(tmp_path) -> None:
    generator = _generator()
    assert generator.generate(check=True, repository_root=tmp_path) == 1
    assert generator.generate(check=False, repository_root=tmp_path) == 0
    assert generator.generate(check=True, repository_root=tmp_path) == 0

    api_manifest = (
        tmp_path / "contracts" / "authored_vnext" / "master" / "v1" / "api-wire.json"
    )
    api_manifest.write_text("{}\n", encoding="utf-8")
    assert generator.generate(check=True, repository_root=tmp_path) == 1
    assert generator.generate(check=False, repository_root=tmp_path) == 0
    assert generator.generate(check=True, repository_root=tmp_path) == 0
