"""Startup import for verified bundled TemplatePackage definitions."""

from __future__ import annotations

from copy import deepcopy
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid
from typing import Any

from sqlalchemy import select

from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_revision import TemplateV2Revision
from services.database import async_session_maker
from services.template_v2_revision_service import append_revision
from templates.v2.wire_codec import decode_wire_layouts
from utils.get_env import get_app_data_directory_env
from utils.icon_weights import normalize_icon_weight

from .asset_store import TemplatePackageAssetStore
from .models import PackageAssetInventory
from .registry import (
    RegisteredTemplatePackage,
    TemplatePackageRegistry,
    get_template_package_registry,
    template_package_assets_binding,
)
from .validator import TemplatePackageValidationError


LOGGER = logging.getLogger(__name__)


def _resolved_app_data_directory() -> Path:
    return Path(get_app_data_directory_env()).resolve()


async def import_template_packages_on_startup(
    registry: TemplatePackageRegistry | None = None,
) -> None:
    """Synchronize only registry-admitted ready packages into Template V2 storage."""

    selected = registry or get_template_package_registry()
    packages = selected.discoverable()
    imported_ids = {package.manifest.template_id for package in packages}
    app_data = _resolved_app_data_directory()
    asset_store = TemplatePackageAssetStore(app_data / "template-package-cas")

    async with async_session_maker() as session:
        for package in packages:
            payload = selected.materialize(package.manifest.template_id)
            template = _template_from_package(package, payload)
            _materialize_package_assets(
                selected.repository_root,
                package,
                app_data=app_data,
                asset_store=asset_store,
            )
            existing = await session.get(TemplateV2, template.id)
            if existing is None:
                session.add(template)
                await session.flush()
                await append_revision(
                    session,
                    template=template,
                    revision=1,
                    reason="package_import",
                )
                LOGGER.info("Imported verified template package: %s", template.id)
            else:
                if existing.owner_id is not None:
                    raise TemplatePackageValidationError(
                        "template_package_persisted_identity_conflict"
                    )
                changed = _update_from_package(existing, template)
                revision = int(getattr(existing, "revision", 1) or 1)
                revision_entry = await session.get(
                    TemplateV2Revision,
                    (existing.id, revision),
                )
                if changed:
                    revision += 1
                    existing.revision = revision
                    await append_revision(
                        session,
                        template=existing,
                        revision=revision,
                        reason="package_import",
                    )
                    LOGGER.info("Updated verified template package: %s", existing.id)
                elif revision_entry is None:
                    await append_revision(
                        session,
                        template=existing,
                        revision=revision,
                        reason="package_import",
                    )
        await _remove_stale_package_rows(session, imported_ids, app_data)
        await session.commit()


def _template_from_package(
    package: RegisteredTemplatePackage,
    payload: dict[str, Any],
) -> TemplateV2:
    template_id = package.manifest.template_id
    rewritten = _rewrite_static_asset_urls(payload, template_id)
    layouts = _coerce_slide_layouts(rewritten.get("layouts"))
    merged_components = _coerce_merged_components(rewritten.get("merged_components"))
    components = rewritten.get("components")
    icon_type = normalize_icon_weight(
        rewritten.get("icon_type", rewritten.get("icon_weight"))
    )
    assets = {
        "template_package": template_package_assets_binding(package),
        "template_id": template_id,
        "icon_type": icon_type,
        "icon_weight": icon_type,
        "fonts": deepcopy(rewritten.get("fonts"))
        if isinstance(rewritten.get("fonts"), dict)
        else {},
        "images": _collect_image_urls(layouts, merged_components),
        "thumbnail": rewritten.get("thumbnail"),
    }
    return TemplateV2(
        id=template_id,
        owner_id=None,
        presentation_id=None,
        name=_required_string(rewritten.get("name"), template_id),
        description=_optional_string(rewritten.get("description")),
        raw_layouts=None,
        components=components if isinstance(components, dict) else None,
        merged_components=merged_components,
        layouts=layouts,
        assets=assets,
        is_default=True,
        revision=1,
    )


def _update_from_package(existing: TemplateV2, incoming: TemplateV2) -> bool:
    changed = False
    for field in (
        "name",
        "description",
        "raw_layouts",
        "components",
        "merged_components",
        "layouts",
        "assets",
        "is_default",
    ):
        value = deepcopy(getattr(incoming, field))
        if getattr(existing, field) != value:
            setattr(existing, field, value)
            changed = True
    return changed


def _materialize_package_assets(
    repository_root: Path,
    package: RegisteredTemplatePackage,
    *,
    app_data: Path,
    asset_store: TemplatePackageAssetStore,
) -> None:
    manifest = package.manifest
    inventory_path = repository_root / manifest.assets.inventory_path
    inventory = PackageAssetInventory.model_validate_json(inventory_path.read_bytes())
    source_root = repository_root / manifest.source_root
    mappings = asset_store.import_inventory(source_root, inventory)
    templates_root = app_data / "templates"
    templates_root.mkdir(parents=True, exist_ok=True)
    destination = templates_root / manifest.template_id
    staging = templates_root / f".{manifest.template_id}.{uuid.uuid4().hex}.tmp"
    staging.mkdir()
    try:
        for item in inventory.files:
            logical = PurePosixPath(item.path)
            if not logical.parts or logical.parts[0] != "static":
                continue
            digest = mappings[item.path].removeprefix("sha256:")
            blob = asset_store.root / "blobs" / "sha256" / digest[:2] / digest
            target = staging / Path(*logical.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(blob, target)
            except OSError:
                shutil.copy2(blob, target)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staging, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def _remove_stale_package_rows(
    session: Any,
    imported_ids: set[str],
    app_data: Path,
) -> None:
    rows = list(await session.scalars(select(TemplateV2)))
    for row in rows:
        assets = row.assets if isinstance(row.assets, dict) else {}
        binding = assets.get("template_package")
        if not isinstance(binding, dict) or row.id in imported_ids:
            continue
        await session.delete(row)
        destination = app_data / "templates" / row.id
        if destination.is_dir():
            shutil.rmtree(destination)
        LOGGER.info("Removed retired template package: %s", row.id)


def _rewrite_static_asset_urls(value: Any, template_id: str) -> Any:
    if isinstance(value, str):
        if not value.startswith("static/"):
            return value
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise TemplatePackageValidationError("template_package_static_path_invalid")
        return f"/app_data/templates/{template_id}/{relative.as_posix()}"
    if isinstance(value, list):
        return [_rewrite_static_asset_urls(item, template_id) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_static_asset_urls(child, template_id)
            for key, child in value.items()
        }
    return value


def _coerce_slide_layouts(value: Any) -> dict[str, Any]:
    payload = {"layouts": value} if isinstance(value, list) else value
    wire = decode_wire_layouts(payload)
    wire.validate_strict()
    return wire.to_storage_value()


def _coerce_merged_components(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = {"components": value} if isinstance(value, list) else value
    return deepcopy(payload) if isinstance(payload, dict) else None


def _collect_image_urls(*values: Any) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "image" and isinstance(value.get("data"), str):
                image = value["data"]
                if image and image not in seen:
                    seen.add(image)
                    images.append(image)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return images


def _required_string(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["import_template_packages_on_startup"]
