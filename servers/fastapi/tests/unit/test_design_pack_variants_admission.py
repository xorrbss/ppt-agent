from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_canvas import validate_authored_canvas_admission
from services.authored_vnext.generation_route import (
    design_pack_storage_root,
    load_pinned_design_pack,
)
from services.document_canvas_capabilities import DESIGN_PACK_VARIANT_ROUTE, evaluate_a4_request
from test_design_pack_variants import BASE_PACK, approve, draft, ingest_base
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER = uuid.UUID(int=7)


@pytest.fixture()
def owner_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    token = set_current_owner_id(OWNER)
    root = design_pack_storage_root(OWNER)
    manifest = ingest_base(root)
    try:
        yield root, {"designPackId": BASE_PACK, "version": 1, "digest": manifest.package_digest}
    finally:
        reset_current_owner_id(token)


def _preset_request(pin: dict, aspect_ratio: str = "9:16") -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        content="variant fixture",
        aspect_ratio=aspect_ratio,
        generationMode="registered-template",
        designPackPin=pin,
    )


def _a4_request(pin: dict, orientation: str = "landscape") -> GeneratePresentationRequest:
    request = GeneratePresentationRequest(
        content="variant fixture",
        page_size="A4",
        orientation=orientation,
        generationMode="registered-template",
        designPackPin=pin,
    )
    request._authored_style_snapshot = SimpleNamespace(reference_mode="style_reference")
    return request


def test_preset_pin_is_rejected_without_an_approved_variant(owner_store) -> None:
    root, pin = owner_store
    with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
        validate_authored_canvas_admission(_preset_request(pin), style_id="default")
    draft(root, "tall-9x16")
    request = _preset_request(pin)
    with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
        validate_authored_canvas_admission(request, style_id="default")
    assert request.design_pack_pin.digest == pin["digest"]


def test_preset_pin_admits_through_approved_variant_and_repins(owner_store) -> None:
    root, pin = owner_store
    draft(root, "tall-9x16")
    variant = approve(root, "tall-9x16")
    request = _preset_request(pin)
    validate_authored_canvas_admission(request, style_id="default")
    assert request.design_pack_pin.design_pack_id == BASE_PACK
    assert request.design_pack_pin.version == 1
    assert request.design_pack_pin.digest == variant.manifest.package_digest
    assert (request._authored_canvas.width_px, request._authored_canvas.height_px) == (720, 1280)
    # Replayed admission (worker start) accepts the substituted pin.
    validate_authored_canvas_admission(request, style_id="default")
    resolved = load_pinned_design_pack(owner_id=OWNER, design_pack_pin=request.design_pack_pin)
    assert resolved.root == variant.root
    # The variant serves only its own canvas.
    with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
        validate_authored_canvas_admission(_preset_request(pin, "9:13"), style_id="default")


def test_a4_pin_is_rejected_without_an_approved_variant(owner_store) -> None:
    root, pin = owner_store
    draft(root, "a4-landscape")
    request = _a4_request(pin)
    with pytest.raises(ValueError, match="document_canvas_generation_combination_not_supported"):
        validate_authored_canvas_admission(request, style_id="default")
    assert request.design_pack_pin.digest == pin["digest"]


def test_a4_pin_routes_to_variant_route_and_repins(owner_store) -> None:
    root, pin = owner_store
    draft(root, "a4-landscape")
    variant = approve(root, "a4-landscape")
    request = _a4_request(pin)
    policy, enabled, reason = evaluate_a4_request(request, reference_mode="style_reference")
    assert (policy.route_id, enabled, reason) == (DESIGN_PACK_VARIANT_ROUTE, True, None)
    assert (policy.writer, policy.implemented, policy.verified) == (True, True, False)
    assert request.design_pack_pin.digest == variant.manifest.package_digest
    fresh = _a4_request(pin)
    validate_authored_canvas_admission(fresh, style_id="default")
    assert fresh.design_pack_pin.digest == variant.manifest.package_digest
    assert fresh._document_canvas.orientation == "landscape"
    with pytest.raises(ValueError, match="document_canvas_generation_combination_not_supported"):
        validate_authored_canvas_admission(_a4_request(pin, "portrait"), style_id="default")


def test_verified_stage_does_not_open_the_unverified_variant_route(owner_store, monkeypatch) -> None:
    import services.document_canvas as document_canvas

    root, pin = owner_store
    draft(root, "a4-landscape")
    approve(root, "a4-landscape")
    monkeypatch.setattr(document_canvas, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    request = _a4_request(pin)
    with pytest.raises(ValueError, match="document_canvas_product_readback_pending"):
        validate_authored_canvas_admission(request, style_id="default")
    assert request.design_pack_pin.digest == pin["digest"]
