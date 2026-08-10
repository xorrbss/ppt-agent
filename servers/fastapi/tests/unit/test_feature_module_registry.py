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
