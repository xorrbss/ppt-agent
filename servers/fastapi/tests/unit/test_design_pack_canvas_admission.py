"""Design packs are 16:9-only: any other canvas fails with design_pack_canvas_unsupported."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import services.authored_canvas as authored_canvas
from api.v1.ppt.endpoints import presentation_generate as endpoint
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_canvas import validate_authored_canvas_admission
from services.authored_vnext.design_pack import ingest_design_pack
from services.authored_vnext.generation_route import (
    GenerationRouteError,
    design_pack_storage_root,
    load_pinned_design_pack,
)
from test_design_pack_manifest_and_render import BASE_PACK, BASE_VERSION, authored_source
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER = uuid.UUID(int=11)
CODE = "design_pack_canvas_unsupported"
PIN = {"designPackId": BASE_PACK, "version": 1, "digest": "sha256:" + "a" * 64}


def _request(**extra: object) -> GeneratePresentationRequest:
    request = GeneratePresentationRequest(
        content="pack canvas", generationMode="registered-template", designPackPin=PIN, **extra
    )
    request._authored_style_snapshot = SimpleNamespace(reference_mode="style_reference")
    return request


@pytest.fixture()
def stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def install(value: str) -> None:
        path = tmp_path / "canvas_stages.json"
        path.write_text(
            json.dumps({"1:1": {"default": value}, "21:9": {"default": value}}), encoding="utf-8"
        )
        monkeypatch.setattr(authored_canvas, "CANVAS_STAGES_PATH", path)
        authored_canvas._canvas_stages.cache_clear()

    yield install
    authored_canvas._canvas_stages.cache_clear()


CANVASES = [
    {"aspect_ratio": "9:16"},
    {"aspect_ratio": "9:13"},
    {"aspect_ratio": "1:1"},
    {"aspect_ratio": "21:9"},
    {"page_size": "A4", "orientation": "landscape"},
    {"page_size": "A4", "orientation": "portrait"},
]


@pytest.mark.parametrize("stage_value", ["disabled", "candidate"])
@pytest.mark.parametrize("strategy", [None, "authored"])
@pytest.mark.parametrize("canvas", CANVASES, ids=lambda item: json.dumps(item))
def test_pack_pin_on_any_non_16x9_canvas_is_rejected_first(stage, stage_value, strategy, canvas) -> None:
    stage(stage_value)
    extra = dict(canvas)
    if strategy is not None:
        extra["strategy"] = strategy
    with pytest.raises(ValueError, match=CODE):
        validate_authored_canvas_admission(_request(**extra), style_id="default")


def test_pack_pin_on_16x9_is_admitted() -> None:
    validate_authored_canvas_admission(_request(), style_id="default")


def test_svg_pack_keeps_its_own_code() -> None:
    from services.svg_generation_admission import require_svg_creation_admission

    request = _request(strategy="svg", aspect_ratio="9:16")
    with pytest.raises(HTTPException) as excinfo:
        require_svg_creation_admission(request)
    assert "svg_design_pack_requires_source_native_patch_target" in str(excinfo.value.detail)


def test_sync_endpoint_maps_the_code_to_422() -> None:
    request = _request(aspect_ratio="9:16", template="authored", authored_style="default")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(endpoint.check_if_api_request_is_valid(request, None))
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == CODE


def test_route_loader_rejects_a_pack_compiled_on_another_canvas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    token = set_current_owner_id(OWNER)
    try:
        root = design_pack_storage_root(OWNER)
        wide = ingest_design_pack(
            authored_source(), pack_id=BASE_PACK, package_version=BASE_VERSION, storage_root=root
        )
        narrow = ingest_design_pack(
            authored_source(960, 720), pack_id="narrow-pack", package_version="1.0.0", storage_root=root
        )
        pin = lambda manifest, pack_id: {  # noqa: E731
            "designPackId": pack_id, "version": 1, "digest": manifest.package_digest,
        }
        assert load_pinned_design_pack(owner_id=OWNER, design_pack_pin=pin(wide, BASE_PACK)).canvas_id == "wide-16x9"
        with pytest.raises(GenerationRouteError, match=CODE):
            load_pinned_design_pack(owner_id=OWNER, design_pack_pin=pin(narrow, "narrow-pack"))
    finally:
        reset_current_owner_id(token)
