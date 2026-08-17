from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from models.authored_vnext.master.contract import DeckMasterContract
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
    resolve_authored_composition,
)
from services.authored_vnext.integration.export_service import (
    AuthoredVNextExportService,
    ExportServiceError,
)
from services.authored_vnext.master.digests import seal_master_contract
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.authored_styles import resolve_authored_style


OFFICIAL_POLICY = "company-furniture-consistency-policy-authored-template/v1"


def _master() -> DeckMasterContract:
    return seal_master_contract(
        DeckMasterContract.model_validate(deepcopy(master_contract_payload()))
    )


def test_official_composition_seals_independent_style_and_company_pins():
    master = _master()
    composition, effective = resolve_authored_composition(
        master,
        style=resolve_authored_style("osb-smart-banking"),
    )

    assert composition.merge_policy_version == OFFICIAL_POLICY
    assert composition.master_digest == master.digest
    assert composition.style_id == "osb-smart-banking"
    assert composition.consistency_policy is not None
    assert composition.authored_template is not None
    assert composition.authored_style_pin.style_id == composition.style_id
    assert composition.company_standard_pin.master_digest == master.digest
    assert composition.company_standard_pin.effective_contract_digest == effective.digest
    composition.require_digest(composition.digest, consumer="focused-test")
    assert materialize_authored_composition(master, composition) == effective
    assert effective.furniture == master.furniture
    assert effective.brand_assets == master.brand_assets


def test_native_export_replays_only_the_persisted_composition_pin():
    master = _master()
    composition, effective = resolve_authored_composition(
        master,
        style=resolve_authored_style("osb-smart-banking"),
    )
    service = AuthoredVNextExportService(None, None, None)

    replayed = service._materialize_composition(
        master,
        SimpleNamespace(composition=composition),
        {"compositionPin": composition.model_dump(mode="json", by_alias=True)},
        expected_master_digest=master.digest,
    )

    assert replayed == effective


def test_native_export_rejects_a_different_composition_payload():
    master = _master()
    composition, _ = resolve_authored_composition(
        master,
        style=resolve_authored_style("osb-smart-banking"),
    )
    other, _ = resolve_authored_composition(
        master,
        style=resolve_authored_style("minimal-vellum"),
    )
    service = AuthoredVNextExportService(None, None, None)

    with pytest.raises(ExportServiceError, match="authored_vnext_composition_mismatch"):
        service._materialize_composition(
            master,
            SimpleNamespace(composition=composition),
            {"compositionPin": other.model_dump(mode="json", by_alias=True)},
            expected_master_digest=master.digest,
        )
