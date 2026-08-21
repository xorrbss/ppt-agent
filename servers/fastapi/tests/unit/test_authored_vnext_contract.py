from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.compatibility_migration.authored_vnext_contract import (
    TEMPLATE_V2_NEW_GENERATION_DISABLED,
    authored_vnext_promotion_readiness,
    require_authored_vnext_creation_admission,
)


def test_authored_vnext_contract_admits_creation_without_claiming_promotion():
    readiness = authored_vnext_promotion_readiness()

    assert readiness.creation_ready is True
    assert readiness.ready is False
    assert readiness.product_name == "authored-vNext"
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
