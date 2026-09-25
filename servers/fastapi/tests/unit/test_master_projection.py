import pytest

from models.authored_vnext.master.contract import DeckMasterContract
from services.generation_core.style.master_projection import (
    MasterProjectionError,
    project_master_contract,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


def _contract() -> DeckMasterContract:
    return DeckMasterContract.model_validate(master_contract_payload())


def test_projects_tokens_fonts_and_standard_layout_geometry():
    contract = _contract()
    result = project_master_contract(contract, 1280, 720)
    assert result["style_id"] == contract.master_id
    assert result["tokens"]["accent"] == contract.tokens.colors.accent
    assert result["fonts"]["heading"]["family"] == contract.font_manifest.primary
    assert len(result["layouts"]) == 1
    layout = result["layouts"][0]
    assert layout["title_box"]["width"] > 0
    assert layout["body_box"]["width"] > 0
    assert result["validation"]["unsupported_roles"] == ["cover", "section", "custom_layouts"]
    assert "digest" in result


def test_scales_geometry_to_a_different_canvas():
    contract = _contract()
    base = project_master_contract(contract, 1280, 720)
    doubled = project_master_contract(contract, 2560, 1440)
    assert doubled["layouts"][0]["title_box"]["width"] == pytest.approx(
        base["layouts"][0]["title_box"]["width"] * 2
    )


def test_out_of_range_canvas_is_rejected():
    contract = _contract()
    with pytest.raises(MasterProjectionError, match="svg_master_canvas_unsupported"):
        project_master_contract(contract, 0, 720)


def test_a_furniture_only_layout_cannot_project():
    payload = master_contract_payload()
    for slot in payload["layouts"]["standard"]["slots"]:
        slot["role"] = "content"
    payload["layouts"]["standard"]["slots"] = [
        slot for slot in payload["layouts"]["standard"]["slots"] if slot["role"] != "title"
    ]
    contract = DeckMasterContract.model_validate(payload)
    with pytest.raises(MasterProjectionError, match="svg_master_layout_geometry_missing"):
        project_master_contract(contract, 1280, 720)
