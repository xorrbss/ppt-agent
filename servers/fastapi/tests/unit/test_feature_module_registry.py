from fastapi import APIRouter
import pytest

from api.feature_module_registry import (
    FeatureModule,
    FeatureModuleRegistrationError,
    FeatureModuleRegistry,
    compose_static_feature_modules,
)
from api.v1.ppt.router import API_V1_PPT_ROUTER, PPT_FEATURE_MODULES


def _router(path: str, *, name: str) -> APIRouter:
    router = APIRouter()

    @router.get(path, name=name)
    async def endpoint() -> dict[str, bool]:
        return {"ok": True}

    return router


def test_static_registry_composes_declared_modules_in_order() -> None:
    router = compose_static_feature_modules(
        (
            FeatureModule("first", _router("/first", name="first"), frozenset({"ppt"})),
            FeatureModule("second", _router("/second", name="second"), frozenset({"ppt"})),
        ),
        available_capabilities={"ppt"},
        prefix="/api/v1/ppt",
    )

    assert [route.path for route in router.routes] == [
        "/api/v1/ppt/first",
        "/api/v1/ppt/second",
    ]


def test_ppt_router_is_composed_from_an_explicit_static_feature_tuple() -> None:
    assert PPT_FEATURE_MODULES[0].name == "files"
    assert PPT_FEATURE_MODULES[-1].name == "community"
    assert API_V1_PPT_ROUTER.routes


def test_ppt_feature_route_names_are_unique_and_reverse_to_the_public_paths() -> None:
    expected = {
        "get_available_ollama_models": ("GET", "/api/v1/ppt/ollama/models/available"),
        "get_available_openai_models": ("POST", "/api/v1/ppt/openai/models/available"),
        "get_available_anthropic_models": ("POST", "/api/v1/ppt/anthropic/models/available"),
        "get_available_google_models": ("POST", "/api/v1/ppt/google/models/available"),
        "get_community_presentation": (
            "GET",
            "/api/v1/ppt/community/presentations/{community_id}",
        ),
        "get_presentation_layouts": (
            "GET",
            "/api/v1/ppt/template-management/get-templates/{presentation}",
        ),
        "delete_legacy_template": (
            "DELETE",
            "/api/v1/ppt/template-management/delete-templates/{template_id}",
        ),
        "delete_template_v2": ("DELETE", "/api/v1/ppt/template/{template_id}"),
        "get_template_layouts": ("GET", "/api/v1/ppt/template/{template_id}/layouts"),
        "create_template_slide_layout": (
            "POST",
            "/api/v1/ppt/template/slide-layout/create",
        ),
        "create_template_slide_layout_legacy": (
            "POST",
            "/api/v1/ppt/template/create/slide-layout",
        ),
    }
    routes = {route.name: route for route in API_V1_PPT_ROUTER.routes}

    assert all(
        method in routes[name].methods and routes[name].path == route_path
        for name, (method, route_path) in expected.items()
    )
    assert str(API_V1_PPT_ROUTER.url_path_for("get_available_openai_models")) == expected[
        "get_available_openai_models"
    ][1]
    assert str(
        API_V1_PPT_ROUTER.url_path_for("get_community_presentation", community_id=1)
    ) == "/api/v1/ppt/community/presentations/1"


def test_ppt_feature_routes_produce_unique_openapi_operation_ids() -> None:
    from api.main import app

    document = app.openapi()
    operations = [
        operation["operationId"]
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "put", "post", "delete", "patch"}
    ]
    assert len(operations) == len(set(operations))
    assert (
        document["paths"]["/api/v1/ppt/openai/models/available"]["post"]["operationId"]
        == "get_available_openai_models_api_v1_ppt_openai_models_available_post"
    )


@pytest.mark.parametrize(
    ("modules", "message"),
    [
        (
            (
                FeatureModule("same", _router("/first", name="first")),
                FeatureModule("same", _router("/second", name="second")),
            ),
            "duplicate_feature_module:same",
        ),
        (
            (
                FeatureModule("first", _router("/same", name="first")),
                FeatureModule("second", _router("/same", name="second")),
            ),
            "duplicate_feature_route:GET /same",
        ),
        (
            (
                FeatureModule("first", _router("/first", name="same")),
                FeatureModule("second", _router("/second", name="same")),
            ),
            "duplicate_feature_route_name:same",
        ),
    ],
)
def test_registry_rejects_duplicate_startup_registration(
    modules: tuple[FeatureModule, ...], message: str
) -> None:
    with pytest.raises(FeatureModuleRegistrationError, match=message):
        compose_static_feature_modules(
            modules,
            available_capabilities=set(),
            prefix="/api",
        )


def test_registry_fails_closed_for_missing_capability() -> None:
    registry = FeatureModuleRegistry(set())
    with pytest.raises(FeatureModuleRegistrationError, match="missing_feature_capability:workspace"):
        registry.register(
            FeatureModule(
                "workspace",
                _router("/workspace", name="workspace"),
                frozenset({"workspace"}),
            )
        )


def test_registry_rejects_duplicate_route_names_within_one_module() -> None:
    router = APIRouter()

    @router.get("/first", name="same")
    async def first() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/second", name="same")
    async def second() -> dict[str, bool]:
        return {"ok": True}

    with pytest.raises(FeatureModuleRegistrationError, match="duplicate_feature_route_name:same"):
        compose_static_feature_modules(
            (FeatureModule("same-module", router),),
            available_capabilities=set(),
            prefix="/api",
        )
