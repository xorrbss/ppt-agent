"""Non-conflicting regressions adapted from the pinned upstream API tests."""

from fastapi import FastAPI

from api.v1.ppt.endpoints.template_v2_compat import (
    TEMPLATE_V2_COMPAT_ROUTER,
)
from api.v1.ppt.router import API_V1_PPT_ROUTER
from templates.v2.policy import get_structured_template_policy


def _methods_by_path(router) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for route in router.routes:
        result.setdefault(route.path, set()).update(route.methods or set())
    return result


def test_template_v2_is_default_off_without_rollout_configuration(monkeypatch):
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)

    policy = get_structured_template_policy()

    assert policy.creation_enabled is False
    assert policy.allowed_template_ids == frozenset()
    assert policy.canary_readiness().code == "template_v2_feature_disabled"


def test_template_v2_routes_stay_in_api_v1_and_preserve_legacy_async_status():
    methods_by_path = _methods_by_path(API_V1_PPT_ROUTER)

    assert "/api/v1/ppt/structured-templates" in methods_by_path
    assert (
        "/api/v1/ppt/presentation/status/{id}" in methods_by_path
    )
    assert "GET" in methods_by_path["/api/v1/ppt/presentation/status/{id}"]
    assert not any(
        path.startswith("/api/v1/async-tasks")
        for path in methods_by_path
    )
    assert all(
        path.startswith("/api/v1/")
        for path in methods_by_path
        if "structured-templates" in path
    )


def test_compat_router_merges_mutations_into_legacy_template_item_openapi_path():
    compat_methods = _methods_by_path(TEMPLATE_V2_COMPAT_ROUTER)
    root_methods = _methods_by_path(API_V1_PPT_ROUTER)

    assert compat_methods == {
        "/template/{template_id}": {"PATCH", "DELETE"},
    }
    assert root_methods["/api/v1/ppt/template/{template_id}"] == {
        "GET",
        "PATCH",
        "DELETE",
    }
    assert "/api/v1/ppt/template/{id}" not in root_methods


def test_template_item_openapi_exposes_one_resource_path_with_unique_operations():
    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)

    paths = app.openapi()["paths"]
    item_operations = paths["/api/v1/ppt/template/{template_id}"]

    assert set(item_operations) == {"get", "patch", "delete"}
    assert "/api/v1/ppt/template/{id}" not in paths
    operation_ids = {
        operation["operationId"] for operation in item_operations.values()
    }
    assert len(operation_ids) == 3
    for operation in item_operations.values():
        assert any(
            parameter["in"] == "path"
            and parameter["name"] == "template_id"
            and parameter["required"] is True
            for parameter in operation["parameters"]
        )
