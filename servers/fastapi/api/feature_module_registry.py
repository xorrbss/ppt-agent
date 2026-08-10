"""Static, startup-validated composition for API feature modules.

Feature modules are deliberately values, not a plugin protocol.  Importing a
router dynamically makes the route table depend on deployment input and hides
collisions until a request is served.  The application builds this registry
from an explicit tuple during import, then fails closed before FastAPI starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable

from fastapi import APIRouter


class FeatureModuleRegistrationError(RuntimeError):
    """A static feature composition is unsafe or incomplete."""


@dataclass(frozen=True)
class FeatureModule:
    """A router and the explicitly declared capability required to expose it."""

    name: str
    router: APIRouter
    required_capabilities: FrozenSet[str] = frozenset()
    # Existing public routers can have historical endpoint function names in
    # common (for example provider ``get_available_models`` routes).  Their
    # paths/methods remain validated; this narrow flag documents the legacy
    # reverse-lookup ambiguity until those endpoint names can be versioned.
    allow_legacy_route_name_collisions: bool = False


def _route_methods(route: object) -> FrozenSet[str]:
    methods = getattr(route, "methods", None)
    if not methods:
        return frozenset()
    # HEAD is automatically supplied by FastAPI for GET and is not an
    # independently registered API operation.
    return frozenset(method for method in methods if method != "HEAD")


class FeatureModuleRegistry:
    """Validates a fixed list of feature modules before router composition."""

    def __init__(self, available_capabilities: Iterable[str]) -> None:
        self._available_capabilities = frozenset(available_capabilities)
        self._modules: list[FeatureModule] = []
        self._names: set[str] = set()
        self._route_names: set[str] = set()
        self._operations: set[tuple[str, str]] = set()

    @property
    def modules(self) -> tuple[FeatureModule, ...]:
        return tuple(self._modules)

    def register(self, module: FeatureModule) -> None:
        if not module.name or module.name in self._names:
            raise FeatureModuleRegistrationError(
                f"duplicate_feature_module:{module.name or '<empty>'}"
            )
        missing = module.required_capabilities - self._available_capabilities
        if missing:
            raise FeatureModuleRegistrationError(
                "missing_feature_capability:" + ",".join(sorted(missing))
            )

        route_names: set[str] = set()
        operations: set[tuple[str, str]] = set()
        for route in module.router.routes:
            route_name = getattr(route, "name", None)
            if (
                route_name
                and route_name in self._route_names
                and not module.allow_legacy_route_name_collisions
            ):
                raise FeatureModuleRegistrationError(
                    f"duplicate_feature_route_name:{route_name}"
                )
            if route_name:
                route_names.add(route_name)
            path = getattr(route, "path", None)
            if not path:
                continue
            for method in _route_methods(route):
                operation = (method, path)
                if operation in self._operations | operations:
                    raise FeatureModuleRegistrationError(
                        f"duplicate_feature_route:{method} {path}"
                    )
                operations.add(operation)

        self._names.add(module.name)
        self._route_names.update(route_names)
        self._operations.update(operations)
        self._modules.append(module)

    def compose(self, *, prefix: str) -> APIRouter:
        router = APIRouter(prefix=prefix)
        for module in self._modules:
            router.include_router(module.router)
        return router


def compose_static_feature_modules(
    modules: Iterable[FeatureModule],
    *,
    available_capabilities: Iterable[str],
    prefix: str,
) -> APIRouter:
    """Build the route table and validate every module before the app starts."""

    registry = FeatureModuleRegistry(available_capabilities)
    for module in modules:
        registry.register(module)
    return registry.compose(prefix=prefix)
