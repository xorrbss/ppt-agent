from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.compatibility_migration.authored_vnext_contract import (
    TEMPLATE_V2_NEW_GENERATION_DISABLED,
    authored_vnext_promotion_readiness,
    require_authored_vnext_creation_admission,
)
from api.v1.ppt.endpoints.authored_vnext.generation.router import (
    _require_internal_new_generation,
)


def test_authored_first_contract_admits_authored_without_claiming_promotion():
    readiness = authored_vnext_promotion_readiness()

    assert readiness.creation_ready is True
    assert readiness.ready is False
    assert readiness.product_name == "Authored"
    assert readiness.authored_only_user_engine is True
    assert readiness.canonical_authored_document_required is True
    assert readiness.vnext_independent_generation is False
    assert readiness.fidelity_default_export is True
    assert readiness.hybrid_partial_edit_only is True
    assert readiness.scene_ir_required is False
    assert readiness.legacy_open_preview_export is True
    assert readiness.template_v2_new_generation is False
    assert readiness.census_complete is False
    assert readiness.relative_visual_quality_verified is False
    assert readiness.physical_deletion is False

    with pytest.raises(HTTPException) as raised:
        require_authored_vnext_creation_admission(
            SimpleNamespace(strategy="template_v2", template="authored")
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == TEMPLATE_V2_NEW_GENERATION_DISABLED
    assert raised.value.detail["compatibility"]["creation_ready"] is True
    assert raised.value.detail["compatibility"]["promotion_ready"] is False
    assert raised.value.detail["compatibility"]["physical_deletion"] is False


def test_independent_vnext_generation_is_default_frozen_and_internal_only(monkeypatch):
    monkeypatch.delenv("AUTHORED_VNEXT_INTERNAL_COMPATIBILITY_ENABLED", raising=False)
    monkeypatch.delenv("AUTHORED_VNEXT_NEW_GENERATION_ENABLED", raising=False)

    with pytest.raises(HTTPException) as raised:
        _require_internal_new_generation()

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "authored_vnext_independent_generation_frozen",
        "requiredUserEngine": "authored",
        "compatibilityReadOpen": True,
        "promotionReady": False,
    }

    monkeypatch.setenv("AUTHORED_VNEXT_INTERNAL_COMPATIBILITY_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_VNEXT_NEW_GENERATION_ENABLED", "true")
    _require_internal_new_generation()
