"""Fail-closed TemplatePackage validation and renderer-smoke receipt checks."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from fontTools.ttLib import TTFont
from PIL import Image

from .models import (
    PackageAssetInventory,
    PackageFile,
    PackageValidationReceipt,
    TemplatePackageManifest,
)


_SVG_FORBIDDEN = re.compile(
    r"<(?:script|foreignObject)\b|\bon[a-z]+\s*=|\b(?:href|src)\s*=\s*[\"'](?:https?:|data:|//)",
    re.IGNORECASE,
)
_MAX_ASSET_BYTES = 64 * 1024 * 1024
_MAX_IMAGE_PIXELS = 80_000_000


class TemplatePackageValidationError(ValueError):
    pass


def validate_template_package(
    repository_root: Path,
    manifest_path: Path,
) -> tuple[TemplatePackageManifest, PackageValidationReceipt]:
    root = repository_root.resolve(strict=True)
    manifest_file = _confined_file(root, manifest_path)
    manifest_bytes = manifest_file.read_bytes()
    manifest = TemplatePackageManifest.model_validate_json(manifest_bytes)

    source_root = _confined_directory(root, Path(manifest.source_root))
    if source_root.name != manifest.template_id:
        raise TemplatePackageValidationError("template_package_identity_path_mismatch")
    entrypoint = _confined_below(source_root, manifest.entrypoint.path)
    _verify_bytes(entrypoint, manifest.entrypoint.sha256, manifest.entrypoint.size_bytes)
    try:
        template = json.loads(entrypoint.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TemplatePackageValidationError("template_package_entrypoint_invalid_json") from error
    if not isinstance(template, dict) or template.get("id") != manifest.template_id:
        raise TemplatePackageValidationError("template_package_payload_identity_mismatch")

    engine_file = _confined_file(root, Path(manifest.engine.contract_path))
    engine_bytes = engine_file.read_bytes()
    _verify_digest(engine_bytes, manifest.engine.contract_sha256, "engine_contract")
    engine = _json_object(engine_bytes, "engine_contract")
    supported = engine.get("capabilities")
    if (
        engine.get("engineId") != manifest.engine.id
        or engine.get("contractVersion") != manifest.engine.contract_version
        or not isinstance(supported, list)
        or not set(manifest.engine.required_capabilities).issubset(set(supported))
    ):
        raise TemplatePackageValidationError("template_package_engine_mismatch")

    inventory_file = _confined_file(root, Path(manifest.assets.inventory_path))
    inventory = PackageAssetInventory.model_validate_json(inventory_file.read_bytes())
    if inventory.template_id != manifest.template_id:
        raise TemplatePackageValidationError("template_package_inventory_identity_mismatch")
    inventory_digest = _inventory_digest(inventory)
    if inventory_digest != manifest.assets.inventory_sha256:
        raise TemplatePackageValidationError("template_package_inventory_digest_mismatch")
    if (
        len(inventory.files) != manifest.assets.count
        or sum(item.size_bytes for item in inventory.files)
        != manifest.assets.total_size_bytes
    ):
        raise TemplatePackageValidationError("template_package_inventory_totals_mismatch")

    declared = {item.path for item in inventory.files}
    if len(declared) != len(inventory.files):
        raise TemplatePackageValidationError("template_package_duplicate_asset_path")
    actual = {
        item.relative_to(source_root).as_posix()
        for item in source_root.rglob("*")
        if item.is_file() and item != entrypoint
    }
    if actual != declared:
        raise TemplatePackageValidationError("template_package_inventory_not_exact")
    for item in inventory.files:
        _validate_asset(source_root, item)

    for alias_path in manifest.fonts.aliases.values():
        if alias_path not in declared:
            raise TemplatePackageValidationError("template_package_font_alias_missing")
    if manifest.thumbnail.asset_path not in declared:
        raise TemplatePackageValidationError("template_package_thumbnail_missing")
    thumbnail_file = _confined_below(source_root, manifest.thumbnail.asset_path)
    _verify_digest(thumbnail_file.read_bytes(), manifest.thumbnail.sha256, "thumbnail")
    with Image.open(thumbnail_file) as thumbnail:
        if thumbnail.size != (manifest.thumbnail.width, manifest.thumbnail.height):
            raise TemplatePackageValidationError("template_package_thumbnail_geometry_mismatch")

    smoke_file = _confined_file(root, Path(manifest.renderer_smoke.receipt_path))
    smoke_bytes = smoke_file.read_bytes()
    _verify_digest(smoke_bytes, manifest.renderer_smoke.receipt_sha256, "renderer_smoke")
    smoke = _json_object(smoke_bytes, "renderer_smoke")
    layouts = template.get("layouts")
    if (
        smoke.get("templateId") != manifest.template_id
        or smoke.get("entrypointSha256") != manifest.entrypoint.sha256
        or smoke.get("engineContractSha256") != manifest.engine.contract_sha256
        or smoke.get("networkPolicy") != "deny"
        or smoke.get("scriptPolicy") != "deny"
        or not isinstance(layouts, list)
        or smoke.get("layoutCount") != len(layouts)
        or not str(smoke.get("status", "")).startswith("passed-")
    ):
        raise TemplatePackageValidationError("template_package_renderer_smoke_invalid")

    remote_references = _remote_references(template)
    expected_blockers = set(manifest.publication.blockers)
    if remote_references and "remote_font_reference" not in expected_blockers:
        raise TemplatePackageValidationError("template_package_remote_reference_unblocked")
    if manifest.fonts.unresolved_aliases and "unresolved_font_aliases" not in expected_blockers:
        raise TemplatePackageValidationError("template_package_unresolved_fonts_unblocked")
    if manifest.publication.status == "ready" and (
        manifest.license.status != "approved"
        or manifest.license.rights_evidence_id is None
        or remote_references
        or manifest.fonts.unresolved_aliases
    ):
        raise TemplatePackageValidationError("template_package_publication_not_proven")

    receipt = PackageValidationReceipt(
        schemaVersion="presenton.template-package-receipt/v1",
        templateId=manifest.template_id,
        packageVersion=manifest.package_version,
        manifestSha256=hashlib.sha256(manifest_bytes).hexdigest(),
        engineContractSha256=manifest.engine.contract_sha256,
        entrypointSha256=manifest.entrypoint.sha256,
        inventorySha256=inventory_digest,
        rendererSmokeSha256=manifest.renderer_smoke.receipt_sha256,
        assetCount=len(inventory.files),
        totalSizeBytes=manifest.assets.total_size_bytes,
        publicationStatus=manifest.publication.status,
        blockers=manifest.publication.blockers,
    )
    return manifest, receipt


def _validate_asset(source_root: Path, item: PackageFile) -> None:
    asset = _confined_below(source_root, item.path)
    if asset.is_symlink() or item.size_bytes > _MAX_ASSET_BYTES:
        raise TemplatePackageValidationError("template_package_asset_policy_rejected")
    _verify_bytes(asset, item.sha256, item.size_bytes)
    data = asset.read_bytes()
    suffix = asset.suffix.lower()
    if item.media_type == "image/png":
        if suffix != ".png" or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise TemplatePackageValidationError("template_package_asset_mime_mismatch")
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            if image.width * image.height > _MAX_IMAGE_PIXELS:
                raise TemplatePackageValidationError("template_package_image_too_large")
    elif item.media_type == "image/svg+xml":
        if suffix != ".svg" or b"<svg" not in data[:4096].lower():
            raise TemplatePackageValidationError("template_package_asset_mime_mismatch")
        try:
            svg = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TemplatePackageValidationError("template_package_svg_invalid") from error
        if _SVG_FORBIDDEN.search(svg):
            raise TemplatePackageValidationError("template_package_svg_unsafe")
    elif item.media_type == "font/ttf":
        if suffix != ".ttf" or data[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true"}:
            raise TemplatePackageValidationError("template_package_asset_mime_mismatch")
        try:
            TTFont(io.BytesIO(data), lazy=False)
        except Exception as error:
            raise TemplatePackageValidationError("template_package_font_invalid") from error
    else:
        raise TemplatePackageValidationError("template_package_asset_media_type_unsupported")


def _inventory_digest(inventory: PackageAssetInventory) -> str:
    files = [item.model_dump(mode="json", by_alias=True) for item in inventory.files]
    canonical = json.dumps({"files": files}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _remote_references(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_remote_references(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_remote_references(item))
    elif isinstance(value, str) and value.lower().startswith(("http://", "https://", "//")):
        found.append(value)
    return tuple(found)


def _confined_file(root: Path, path_value: Path) -> Path:
    value = _confined_below(root, path_value.as_posix())
    if not value.is_file() or value.is_symlink():
        raise TemplatePackageValidationError("template_package_file_missing")
    return value


def _confined_directory(root: Path, path_value: Path) -> Path:
    value = _confined_below(root, path_value.as_posix())
    if not value.is_dir() or value.is_symlink():
        raise TemplatePackageValidationError("template_package_directory_missing")
    return value


def _confined_below(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise TemplatePackageValidationError("template_package_path_invalid")
    logical = PurePosixPath(relative)
    if logical.is_absolute() or not logical.parts or ".." in logical.parts:
        raise TemplatePackageValidationError("template_package_path_invalid")
    candidate = (root / Path(*logical.parts)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise TemplatePackageValidationError("template_package_path_escape") from error
    return candidate


def _verify_bytes(path: Path, digest: str, size: int) -> None:
    data = path.read_bytes()
    if len(data) != size:
        raise TemplatePackageValidationError("template_package_size_mismatch")
    _verify_digest(data, digest, "file")


def _verify_digest(data: bytes, expected: str, label: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise TemplatePackageValidationError(f"template_package_{label}_hash_mismatch")


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TemplatePackageValidationError(f"template_package_{label}_invalid_json") from error
    if not isinstance(value, dict):
        raise TemplatePackageValidationError(f"template_package_{label}_invalid")
    return value


__all__ = ["TemplatePackageValidationError", "validate_template_package"]
