"""Explicit, compile-time TemplatePackage registry; never scans arbitrary roots."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from .models import PackageValidationReceipt, TemplatePackageManifest
from .validator import TemplatePackageValidationError, validate_template_package


@dataclass(frozen=True, slots=True)
class RegisteredTemplatePackage:
    manifest: TemplatePackageManifest
    receipt: PackageValidationReceipt
    discoverable: bool


class TemplatePackageRegistry:
    def __init__(self, repository_root: Path, registry_path: Path) -> None:
        self.repository_root = repository_root.resolve(strict=True)
        self.registry_path = registry_path
        self._packages = self._load()

    def get(self, template_id: str) -> RegisteredTemplatePackage:
        try:
            package = self._packages[template_id]
        except KeyError as error:
            raise TemplatePackageValidationError("template_package_unknown") from error
        if package.manifest.publication.status == "retired":
            raise TemplatePackageValidationError("template_package_retired")
        return package

    def discoverable(self) -> tuple[RegisteredTemplatePackage, ...]:
        return tuple(
            package
            for package in self._packages.values()
            if package.discoverable and package.manifest.publication.status == "ready"
        )

    def materialize(self, template_id: str) -> dict[str, Any]:
        """Return the verified, network-free runtime payload for one package."""

        package = self.get(template_id)
        entrypoint = self.repository_root / package.manifest.source_root / package.manifest.entrypoint.path
        payload = json.loads(entrypoint.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TemplatePackageValidationError("template_package_entrypoint_invalid")
        materialized = deepcopy(payload)
        fonts = materialized.get("fonts")
        if isinstance(fonts, dict):
            for family, replacement in package.manifest.fonts.aliases.items():
                current = fonts.get(family)
                if isinstance(current, str) and current.lower().startswith(
                    ("http://", "https://", "//")
                ):
                    fonts[family] = replacement
        if any(
            value.lower().startswith(("http://", "https://", "//"))
            for value in _string_values(materialized)
        ):
            raise TemplatePackageValidationError("template_package_runtime_remote_reference")
        _normalize_legacy_authoring(materialized)
        return materialized

    def _load(self) -> dict[str, RegisteredTemplatePackage]:
        registry_file = (self.repository_root / self.registry_path).resolve(strict=True)
        try:
            registry_file.relative_to(self.repository_root)
        except ValueError as error:
            raise TemplatePackageValidationError("template_package_registry_path_escape") from error
        raw: Any = json.loads(registry_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schemaVersion") != "presenton.template-package-registry/v1":
            raise TemplatePackageValidationError("template_package_registry_invalid")
        entries = raw.get("packages")
        if not isinstance(entries, list):
            raise TemplatePackageValidationError("template_package_registry_invalid")
        packages: dict[str, RegisteredTemplatePackage] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("enabled") is not True:
                continue
            manifest_value = entry.get("manifest")
            template_id = entry.get("templateId")
            if not isinstance(manifest_value, str) or not isinstance(template_id, str):
                raise TemplatePackageValidationError("template_package_registry_entry_invalid")
            logical = PurePosixPath(manifest_value)
            if logical.is_absolute() or ".." in logical.parts or "\\" in manifest_value:
                raise TemplatePackageValidationError("template_package_registry_path_invalid")
            manifest, receipt = validate_template_package(
                self.repository_root, Path(*logical.parts)
            )
            if manifest.template_id != template_id or template_id in packages:
                raise TemplatePackageValidationError("template_package_registry_identity_conflict")
            discoverable = entry.get("discoverable") is True
            if discoverable and manifest.publication.status != "ready":
                raise TemplatePackageValidationError("template_package_blocked_discoverability")
            packages[template_id] = RegisteredTemplatePackage(
                manifest=manifest,
                receipt=receipt,
                discoverable=discoverable,
            )
        return packages


def _string_values(value: Any) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_string_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_string_values(child))
    elif isinstance(value, str):
        values.append(value)
    return tuple(values)


def _normalize_legacy_authoring(value: Any) -> None:
    """Normalize bounded legacy extras declared by the package engine contract."""

    if isinstance(value, list):
        for child in value:
            _normalize_legacy_authoring(child)
        return
    if not isinstance(value, dict):
        return
    element_type = value.get("type")
    if element_type == "text":
        minimum = value.get("min_length")
        maximum = value.get("max_length")
        if (
            isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and minimum > maximum
        ):
            value["min_length"], value["max_length"] = maximum, minimum
    elif element_type == "vector" and value.get("shape") == "ellipse":
        points = value.get("points")
        if isinstance(points, list) and len(points) > 2 and all(
            isinstance(point, dict)
            and isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
            for point in points
        ):
            xs = [float(point["x"]) for point in points]
            ys = [float(point["y"]) for point in points]
            value["points"] = [
                {"x": min(xs), "y": min(ys)},
                {"x": max(xs), "y": max(ys)},
            ]
    elif element_type in {"flex", "grid"}:
        children = value.get("children")
        minimum = value.get("min_children")
        maximum = value.get("max_children")
        if isinstance(children, list):
            child_count = len(children)
            if isinstance(minimum, int) and minimum > child_count:
                value["min_children"] = child_count
            if isinstance(maximum, int) and maximum < child_count:
                value["max_children"] = child_count
    if element_type == "grid":
        columns = value.get("columns")
        rows = value.get("rows")
        maximum = value.get("max_children")
        if (
            isinstance(columns, int)
            and columns > 0
            and isinstance(rows, int)
            and rows > 0
            and isinstance(maximum, int)
            and maximum > columns * rows
        ):
            value["rows"] = math.ceil(maximum / columns)
    for child in value.values():
        _normalize_legacy_authoring(child)


def default_template_package_repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def get_template_package_registry() -> TemplatePackageRegistry:
    return TemplatePackageRegistry(
        default_template_package_repository_root(),
        Path("templates/package-registry.v1.json"),
    )


def get_ready_template_package(
    template_id: str,
) -> RegisteredTemplatePackage | None:
    try:
        package = get_template_package_registry().get(template_id)
    except TemplatePackageValidationError:
        return None
    if package.manifest.publication.status != "ready":
        return None
    return package


def list_ready_template_package_ids() -> tuple[str, ...]:
    return tuple(
        package.manifest.template_id
        for package in get_template_package_registry().discoverable()
    )


def template_package_assets_binding(
    package: RegisteredTemplatePackage,
) -> dict[str, Any]:
    receipt = package.receipt
    return {
        "schema_version": receipt.schema_version,
        "template_id": receipt.template_id,
        "package_version": receipt.package_version,
        "manifest_sha256": receipt.manifest_sha256,
        "engine_contract_sha256": receipt.engine_contract_sha256,
        "entrypoint_sha256": receipt.entrypoint_sha256,
        "inventory_sha256": receipt.inventory_sha256,
        "renderer_smoke_sha256": receipt.renderer_smoke_sha256,
        "publication_status": receipt.publication_status,
    }


def is_template_package_release_ready(template_id: str, assets: Any) -> bool:
    package = get_ready_template_package(template_id)
    if package is None or not package.discoverable or not isinstance(assets, dict):
        return False
    return assets.get("template_package") == template_package_assets_binding(package)


__all__ = [
    "RegisteredTemplatePackage",
    "TemplatePackageRegistry",
    "default_template_package_repository_root",
    "get_ready_template_package",
    "get_template_package_registry",
    "is_template_package_release_ready",
    "list_ready_template_package_ids",
    "template_package_assets_binding",
]
