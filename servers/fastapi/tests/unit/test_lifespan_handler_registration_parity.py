"""All workspace controllers derive handler registration from one catalog."""

import inspect

from api.lifespan import app_lifespan
from services.public_worker_profiles import get_public_worker_profile
from services.workspace_operation_catalog import OPERATION_SPECS, operation_kinds_for_controller
from services.workspace_operation_worker import (
    external_workspace_operation_kinds,
    register_workspace_operation_handlers,
    run_external_worker,
)


def test_embedded_and_external_workers_register_from_catalog():
    assert "register_workspace_operation_handlers()" in inspect.getsource(app_lifespan)
    assert "register_operation_handlers()" in inspect.getsource(register_workspace_operation_handlers)
    assert "register_operation_handlers(" in inspect.getsource(run_external_worker)
    assert all(spec.embedded for spec in OPERATION_SPECS)


def test_claim_profiles_match_catalog(monkeypatch):
    monkeypatch.delenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", raising=False)
    assert external_workspace_operation_kinds("generation") == operation_kinds_for_controller("generation")
    assert get_public_worker_profile("generation").operation_kinds == operation_kinds_for_controller("generation")
    monkeypatch.setenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", "export")
    assert external_workspace_operation_kinds("ppt-core") == operation_kinds_for_controller("export")
    assert get_public_worker_profile("export").operation_kinds == operation_kinds_for_controller("export")
