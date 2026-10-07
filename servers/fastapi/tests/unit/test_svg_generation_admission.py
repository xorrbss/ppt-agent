import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.generate_presentation_request import AuthoredMasterPinInput
from services.authored_vnext.generation.errors import PublishedMasterRequiredError
from services.authored_vnext.generation.legacy_pin import ResolvedLegacyMasterPin
from services.generation_core.style.design_pack_projection import DesignPackProjectionError
from services.svg_generation_admission import (
    _resolve_design_reference,
    _resolve_master_rules,
    require_svg_creation_admission,
    source_svg_capability,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


def _svg_request(**overrides):
    base = dict(
        strategy="svg", source_mode=None, design_pack_pin=None,
        generation_engine="authored", generation_mode="auto", master_pin=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_design_pack_pin_is_rejected_with_a_specific_structural_reason():
    request = _svg_request(design_pack_pin={"designPackId": "acme", "version": 1})
    with pytest.raises(HTTPException) as excinfo:
        require_svg_creation_admission(request)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "svg_design_pack_requires_source_native_patch_target"


def test_design_pack_projection_error_names_the_real_gap():
    from services.generation_core.style.design_pack_projection import assert_design_pack_projects

    with pytest.raises(DesignPackProjectionError):
        assert_design_pack_projects()


def test_master_pin_alone_no_longer_blocks_admission():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    # No exception: master_pin admissibility is decided later, once resolved
    # and projected (see _resolve_master_rules), not by presence alone.
    require_svg_creation_admission(request)


def test_svg_design_reference_does_not_reinterpret_legacy_pack_execution():
    pin = {"designPackId": "acme", "version": 1, "digest": "sha256:" + "1" * 64}
    require_svg_creation_admission(_svg_request(svg_design_reference_pin=pin))
    with pytest.raises(HTTPException) as error:
        require_svg_creation_admission(_svg_request(svg_design_reference_pin=pin, master_pin=pin))
    assert error.value.detail == "svg_design_reference_conflict"


def test_svg_design_reference_requires_owner_and_pins_target_geometry():
    pin = {"designPackId": "acme", "version": 1, "digest": "sha256:" + "1" * 64}
    request = _svg_request(svg_design_reference_pin=pin)
    canvas = SimpleNamespace(width_px=720, height_px=1280)
    with patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as error:
            _resolve_design_reference(request, canvas)
    assert error.value.status_code == 401
    owner = uuid.uuid4()
    with patch("utils.owner_context.get_current_owner_id", return_value=owner), patch(
        "services.generation_core.style.design_pack_projection.resolve_design_pack_reference",
        return_value={"reference_semantics": "design-identity"},
    ) as resolve:
        assert _resolve_design_reference(request, canvas)["reference_semantics"] == "design-identity"
        resolve.assert_called_once_with(owner_id=owner, pin=pin, width=720, height=1280)


def test_source_svg_reference_capability_allows_company_design_and_selected_canvas():
    capability = source_svg_capability(
        strategy="svg", source_content=True, has_company_standard=True,
        reference_mode="exact_reconstruction", style_id="default",
    )
    assert capability["enabled"] is True
    assert capability["canvasSource"] == "selected-svg"


def _resolved_master(contract):
    digest = contract.digest or ("sha256:" + "0" * 64)
    pin = MasterDigestPin(
        master_id="master:acme-1", master_revision=1, master_digest=digest,
        layout_registry_digest="sha256:" + "0" * 64, font_manifest_digest="sha256:" + "0" * 64,
    )
    return ResolvedLegacyMasterPin(owner_id=uuid.uuid4(), pin=pin, contract=contract)


def test_resolve_master_rules_returns_none_without_a_pin():
    request = _svg_request()
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    assert _resolve_master_rules(request, canvas) == (None, None)


def test_resolve_master_rules_projects_the_published_master():
    contract = DeckMasterContract.model_validate(master_contract_payload())
    resolved = _resolved_master(contract)
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch("services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin", return_value=resolved):
        rules, pin = _resolve_master_rules(request, canvas)
    assert rules["tokens"]["accent"] == "#1463FF"
    assert pin["masterId"] == "master:acme-1"


def test_resolve_master_rules_requires_an_owner():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 401


def test_resolve_master_rules_maps_pipeline_errors_to_409():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch(
             "services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin",
             side_effect=PublishedMasterRequiredError(),
         ):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 409
