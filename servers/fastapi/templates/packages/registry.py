"""Explicit, compile-time TemplatePackage registry; never scans arbitrary roots."""

from __future__ import annotations

import json
from dataclasses import dataclass
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


__all__ = ["RegisteredTemplatePackage", "TemplatePackageRegistry"]
