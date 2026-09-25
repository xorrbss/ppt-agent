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
    _resolve_master_rules,
    require_svg_creation_admission,
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
    with patch("services.svg_generation_admission.os.getenv", return_value="true"), \
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
    with patch("services.svg_generation_admission.os.getenv", return_value="true"), \
         patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 401


def test_resolve_master_rules_maps_pipeline_errors_to_409():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.os.getenv", return_value="true"), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch(
             "services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin",
             side_effect=PublishedMasterRequiredError(),
         ):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 409
