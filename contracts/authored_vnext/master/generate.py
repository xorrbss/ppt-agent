"""Generate authored vNext Master JSON contracts from Pydantic and FastAPI."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FASTAPI_ROOT = REPOSITORY_ROOT / "servers" / "fastapi"
CONTRACT_DIRECTORY = Path("contracts/authored_vnext/master/v1")
DECK_MASTER_SCHEMA = CONTRACT_DIRECTORY / "deckmaster-contract.schema.json"
SLIDE_DRAFT_SCHEMA = CONTRACT_DIRECTORY / "slide-draft.schema.json"
API_WIRE_MANIFEST = CONTRACT_DIRECTORY / "api-wire.json"

FEATURE_FLAG = "AUTHORED_VNEXT_MASTER_ENABLED"
DISABLED_DETAIL = "authored_vnext_master_disabled"
MOUNT_PREFIX = "/api/v1/ppt"


def _backend_module(name: str) -> Any:
    backend_path = str(FASTAPI_ROOT)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    return import_module(name)


def _strict_schema(model: type[Any], *, schema_id: str) -> dict[str, Any]:
    config: Mapping[str, Any] = model.model_config
    if config.get("extra") != "forbid" or config.get("strict") is not True:
        raise RuntimeError(f"{model.__name__} is not a strict closed model")
    if config.get("frozen") is not True:
        raise RuntimeError(f"{model.__name__} is not immutable")
    schema = model.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "x-pydantic-strict": True,
        **schema,
    }


def build_deck_master_schema() -> dict[str, Any]:
    model = _backend_module(
        "models.authored_vnext.master.contract"
    ).DeckMasterContract
    return _strict_schema(
        model,
        schema_id="urn:presenton:authored-vnext:deck-master-contract:v1",
    )


def build_slide_draft_schema() -> dict[str, Any]:
    model = _backend_module("models.authored_vnext.master.slide").SlideDraft
    return _strict_schema(
        model,
        schema_id="urn:presenton:authored-vnext:slide-draft:v2",
    )


def _model_name(value: object) -> str | None:
    return getattr(value, "__name__", None)


def _request_model(route: Any) -> str | None:
    models = {
        _model_name(parameter.field_info.annotation)
        for parameter in route.dependant.body_params
        if _model_name(parameter.field_info.annotation) is not None
    }
    if not models:
        return None
    if len(models) != 1:
        raise RuntimeError(f"route {route.path} has an ambiguous request body")
    return models.pop()


def _endpoint_manifest(router: Any) -> list[dict[str, object]]:
    endpoints: list[dict[str, object]] = []
    for route in router.routes:
        for method in sorted(route.methods):
            endpoints.append(
                {
                    "method": method,
                    "path": route.path,
                    "operation": route.name,
                    "requestModel": _request_model(route),
                    "responseModel": _model_name(route.response_model),
                    "successStatus": route.status_code or 200,
                }
            )
    return sorted(endpoints, key=lambda item: (str(item["path"]), str(item["method"])))


def _feature_flag_contract(router_module: Any) -> dict[str, object]:
    with patch.dict(os.environ, {FEATURE_FLAG: "false"}):
        if router_module.authored_vnext_master_enabled():
            raise RuntimeError(f"{FEATURE_FLAG} must default and parse false")
        try:
            router_module._require_enabled()
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            detail = getattr(error, "detail", None)
        else:
            raise RuntimeError("disabled feature gate did not fail closed")
    if status_code != 404 or detail != DISABLED_DETAIL:
        raise RuntimeError("disabled feature gate wire contract changed")
    return {
        "name": FEATURE_FLAG,
        "default": False,
        "disabledResponse": {"status": status_code, "detail": detail},
    }


def build_api_wire_manifest() -> dict[str, Any]:
    router_module = _backend_module(
        "api.v1.ppt.endpoints.authored_vnext.master.router"
    )
    router = router_module.AUTHORED_VNEXT_MASTER_ROUTER
    status_response = router_module.master_status()
    return {
        "schemaVersion": "authored-vnext-master-api-wire/v1",
        "router": {
            "symbol": "AUTHORED_VNEXT_MASTER_ROUTER",
            "mountPrefix": MOUNT_PREFIX,
            "prefix": router.prefix,
            "publicPrefix": f"{MOUNT_PREFIX}{router.prefix}",
        },
        "featureFlag": _feature_flag_contract(router_module),
        "versions": {
            "deckMasterContract": status_response.contract_version,
            "slideSpec": status_response.slide_spec_version,
            "compilerPort": status_response.compiler_port,
        },
        "wireFormat": {
            "fieldNames": "camelCase",
            "additionalProperties": "forbidden",
        },
        "endpoints": _endpoint_manifest(router),
    }


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def render_outputs(repository_root: Path = REPOSITORY_ROOT) -> dict[Path, bytes]:
    output_directory = repository_root / CONTRACT_DIRECTORY
    return {
        output_directory / DECK_MASTER_SCHEMA.name: _json_bytes(
            build_deck_master_schema()
        ),
        output_directory / SLIDE_DRAFT_SCHEMA.name: _json_bytes(
            build_slide_draft_schema()
        ),
        output_directory / API_WIRE_MANIFEST.name: _json_bytes(
            build_api_wire_manifest()
        ),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def generate(*, check: bool, repository_root: Path = REPOSITORY_ROOT) -> int:
    outputs = render_outputs(repository_root)
    stale = [
        path
        for path, expected in outputs.items()
        if not path.is_file() or path.read_bytes() != expected
    ]
    if check:
        if stale:
            print("Authored vNext Master generated contracts are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path.relative_to(repository_root)}", file=sys.stderr)
            print(
                "Run `uv run --project servers/fastapi python "
                "contracts/authored_vnext/master/generate.py`.",
                file=sys.stderr,
            )
            return 1
        print("Authored vNext Master generated contracts are up to date.")
        return 0
    for path, content in outputs.items():
        _atomic_write(path, content)
    print(f"Generated {len(outputs)} authored vNext Master contract files.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when generated files are stale",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args(argv)
    return generate(
        check=arguments.check,
        repository_root=arguments.repository_root.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
