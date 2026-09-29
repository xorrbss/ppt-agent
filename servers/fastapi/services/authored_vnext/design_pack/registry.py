"""Dynamic, caller-rooted registry for immutable source-native design packs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from .canvases import canvas_id_for_size
from .models import DesignPackManifest, DesignPackStructure


class DesignPackRegistryError(ValueError):
    """A discovered package is malformed, mutable, or digest-inconsistent."""


@dataclass(frozen=True, slots=True)
class RegisteredDesignPack:
    root: Path
    manifest: DesignPackManifest
    structure: DesignPackStructure

    @property
    def canvas_id(self) -> str | None:
        """The registered canvas of the pack's own compiled source, if any."""

        canvas = self.structure.canvas
        return canvas_id_for_size(canvas.width, canvas.height, canvas.unit)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DesignPackRegistryError(f"design_pack_{label}_invalid") from error


def _confined(package_root: Path, relative: str) -> Path:
    logical = PurePosixPath(relative)
    if logical.is_absolute() or ".." in logical.parts or "\\" in relative:
        raise DesignPackRegistryError("design_pack_inventory_path_invalid")
    target = package_root.joinpath(*logical.parts).resolve(strict=True)
    try:
        target.relative_to(package_root)
    except ValueError as error:
        raise DesignPackRegistryError("design_pack_inventory_path_escape") from error
    if not target.is_file() or target.is_symlink():
        raise DesignPackRegistryError("design_pack_inventory_file_invalid")
    return target


def _load_package(manifest_path: Path, storage_root: Path) -> RegisteredDesignPack:
    if manifest_path.is_symlink():
        raise DesignPackRegistryError("design_pack_manifest_invalid")
    package_root = manifest_path.parent.resolve(strict=True)
    try:
        package_root.relative_to(storage_root)
    except ValueError as error:
        raise DesignPackRegistryError("design_pack_package_path_escape") from error
    try:
        manifest = DesignPackManifest.model_validate(
            _load_json(manifest_path, "manifest")
        )
    except ValidationError as error:
        raise DesignPackRegistryError("design_pack_manifest_invalid") from error
    if (
        package_root.name != manifest.package_version
        or package_root.parent.name != manifest.pack_id
    ):
        raise DesignPackRegistryError("design_pack_package_identity_mismatch")

    inventory_by_path = {item.path: item for item in manifest.inventory}
    for item in manifest.inventory:
        target = _confined(package_root, item.path)
        value = target.read_bytes()
        if len(value) != item.size_bytes or _sha256(value) != item.sha256:
            raise DesignPackRegistryError("design_pack_inventory_digest_mismatch")
    structure_item = inventory_by_path.get(manifest.structure_path)
    if structure_item is None or structure_item.role != "structure":
        raise DesignPackRegistryError("design_pack_structure_inventory_missing")
    structure_path = _confined(package_root, manifest.structure_path)
    try:
        structure = DesignPackStructure.model_validate(
            _load_json(structure_path, "structure")
        )
    except ValidationError as error:
        raise DesignPackRegistryError("design_pack_structure_invalid") from error
    if (
        structure.structure_digest != manifest.structure_digest
        or structure.source_digest != manifest.source_digest
        or structure.source_kind != manifest.source_kind
    ):
        raise DesignPackRegistryError("design_pack_structure_binding_mismatch")
    source_files = [item for item in manifest.inventory if item.role == "source"]
    if len(source_files) != 1 or f"sha256:{source_files[0].sha256}" != manifest.source_digest:
        raise DesignPackRegistryError("design_pack_source_binding_mismatch")
    render_inventory = {
        item.path: item
        for item in manifest.inventory
        if item.role == "representative-render"
    }
    for render in structure.representative_renders:
        item = render_inventory.get(render.asset_path)
        if item is None or item.sha256 != render.sha256:
            raise DesignPackRegistryError("design_pack_render_binding_mismatch")
    return RegisteredDesignPack(
        root=package_root,
        manifest=manifest,
        structure=structure,
    )


def _version_key(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


class DesignPackRegistry:
    """Discover packages from ``<root>/<pack-id>/<semver>/manifest.json``."""

    def __init__(self, storage_root: Path | str) -> None:
        self.storage_root = Path(storage_root).expanduser().resolve(strict=False)
        self._packages: dict[tuple[str, str], RegisteredDesignPack] = {}
        self.reload()

    def reload(self) -> tuple[RegisteredDesignPack, ...]:
        if not self.storage_root.exists():
            self._packages = {}
            return ()
        if not self.storage_root.is_dir() or self.storage_root.is_symlink():
            raise DesignPackRegistryError("design_pack_storage_root_invalid")
        packages: dict[tuple[str, str], RegisteredDesignPack] = {}
        manifests = sorted(
            self.storage_root.glob("*/*/manifest.json"),
            key=lambda path: path.as_posix().casefold(),
        )
        for manifest_path in manifests:
            package = _load_package(manifest_path, self.storage_root)
            key = (
                package.manifest.pack_id,
                package.manifest.package_version,
            )
            if key in packages:
                raise DesignPackRegistryError("design_pack_package_duplicate")
            packages[key] = package
        self._packages = packages
        return self.list()

    def list(self) -> tuple[RegisteredDesignPack, ...]:
        return tuple(
            self._packages[key]
            for key in sorted(
                self._packages,
                key=lambda item: (item[0], _version_key(item[1])),
            )
        )

    def get(self, pack_id: str, package_version: str) -> RegisteredDesignPack:
        try:
            return self._packages[(pack_id, package_version)]
        except KeyError as error:
            raise DesignPackRegistryError("design_pack_unknown") from error

    def latest(self, pack_id: str) -> RegisteredDesignPack:
        versions = [
            package
            for (candidate_id, _), package in self._packages.items()
            if candidate_id == pack_id
        ]
        if not versions:
            raise DesignPackRegistryError("design_pack_unknown")
        return max(
            versions,
            key=lambda package: _version_key(package.manifest.package_version),
        )


__all__ = [
    "DesignPackRegistry",
    "DesignPackRegistryError",
    "RegisteredDesignPack",
]
