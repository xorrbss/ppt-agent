"""Owner-scoped admission and receipt sealing for source-native generation."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from services.authored_vnext.design_pack import (
    DesignPackRegistry,
    DesignPackRegistryError,
    RegisteredDesignPack,
)
from utils.owner_context import get_current_owner_id


class GenerationRouteError(ValueError):
    code = "generation_route_invalid"

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def design_pack_storage_root(owner_id: uuid.UUID | str) -> Path:
    try:
        owner = uuid.UUID(str(owner_id))
    except (TypeError, ValueError) as error:
        raise GenerationRouteError("design_pack_owner_invalid") from error
    configured = os.getenv("APP_DATA_DIRECTORY", "").strip()
    app_data = Path(configured or "app_data").expanduser().resolve(strict=False)
    return app_data / "design-packs" / "users" / str(owner)


def _pin_value(pin: object, *names: str) -> object:
    if isinstance(pin, Mapping):
        for name in names:
            if name in pin:
                return pin[name]
    for name in names:
        if hasattr(pin, name):
            return getattr(pin, name)
    return None


def normalize_design_pack_pin(pin: object) -> dict[str, object]:
    pack_id = _pin_value(pin, "designPackId", "design_pack_id", "packId", "pack_id", "id")
    version = _pin_value(pin, "version", "packageVersion", "package_version")
    digest = _pin_value(pin, "digest", "packageDigest", "package_digest")
    if (
        not isinstance(pack_id, str)
        or not pack_id.strip()
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(digest, str)
        or not digest.strip()
    ):
        raise GenerationRouteError("design_pack_pin_invalid")
    normalized = {
        "designPackId": str(pack_id).strip(),
        "version": version,
        "digest": str(digest).strip(),
    }
    if not normalized["digest"].startswith("sha256:") or len(normalized["digest"]) != 71:
        raise GenerationRouteError("design_pack_pin_invalid")
    return normalized


def load_pinned_design_pack(
    *, owner_id: uuid.UUID | str, design_pack_pin: object
) -> RegisteredDesignPack:
    pin = normalize_design_pack_pin(design_pack_pin)
    try:
        # Admission may have re-pinned the request to an approved canvas variant.
        packages = DesignPackRegistry(design_pack_storage_root(owner_id)).pinnable()
    except DesignPackRegistryError as error:
        raise GenerationRouteError(str(error)) from error
    matches = [
        package
        for package in packages
        if package.manifest.pack_id == pin["designPackId"]
        and int(package.manifest.package_version.split(".", 1)[0]) == pin["version"]
        and package.manifest.package_digest == pin["digest"]
    ]
    if len(matches) != 1:
        raise GenerationRouteError("design_pack_pin_digest_mismatch")
    return matches[0]


def resolve_internal_design_pack_pin(
    *, owner_id: uuid.UUID | str, authored_style: str
) -> dict[str, object]:
    """Resolve a public style choice to one exact private package authority."""

    style_id = str(authored_style or "").strip()
    if not style_id:
        raise GenerationRouteError("source_content_template_required")
    try:
        packages = DesignPackRegistry(design_pack_storage_root(owner_id)).list()
    except DesignPackRegistryError as error:
        raise GenerationRouteError(str(error)) from error
    matches = [package for package in packages if package.manifest.pack_id == style_id]
    if len(matches) != 1:
        raise GenerationRouteError("source_content_design_pack_unavailable")
    package = matches[0]
    return {
        "designPackId": package.manifest.pack_id,
        "version": int(package.manifest.package_version.split(".", 1)[0]),
        "digest": package.manifest.package_digest,
    }


def resolve_generation_route(
    *,
    requested_mode: str | None = None,
    generation_mode: str | None = None,
    design_pack_pin: object | None,
    source_mode: str | None = None,
    authored_style: str | None = None,
    owner_id: uuid.UUID | str | None = None,
    operation_id: uuid.UUID | str | None = None,
    **_: Any,
) -> dict[str, object]:
    """Resolve once at admission; clients never choose an execution engine."""

    mode = (generation_mode or requested_mode or "auto").strip()
    allowed = {
        "auto",
        "registered-template",
        "brand-style",
        "free-design",
        "fidelity-first",
        "editable-first",
    }
    if mode not in allowed:
        raise GenerationRouteError("generation_mode_invalid")
    if mode == "editable-first":
        raise GenerationRouteError("capability_not_supported")
    if (
        mode == "registered-template"
        and design_pack_pin is None
        and source_mode != "source_content_restyle"
    ):
        raise GenerationRouteError("design_pack_pin_required")

    resolved_owner = owner_id or get_current_owner_id()
    if resolved_owner is None:
        raise GenerationRouteError("design_pack_owner_invalid")
    pin: dict[str, object] | None = None
    if source_mode == "source_content_restyle":
        if not str(authored_style or "").strip():
            raise GenerationRouteError("source_content_template_required")
        if design_pack_pin is not None:
            raise GenerationRouteError("source_content_design_pack_not_supported")
        return {
            "selectedRoute": "authored-fallback",
            "designPackPin": None,
            "narrativeIrDigest": None,
            "fallbackReason": "source-content-authored-reflow",
        }
    if design_pack_pin is not None:
        package = load_pinned_design_pack(
            owner_id=resolved_owner, design_pack_pin=design_pack_pin
        )
        pin = {
            "designPackId": package.manifest.pack_id,
            "version": int(package.manifest.package_version.split(".", 1)[0]),
            "digest": package.manifest.package_digest,
        }
        selected = "source-native"
        fallback_reason = None
    else:
        selected = "authored-fallback"
        fallback_reason = "no-compatible-design-pack"

    del operation_id
    unsigned: dict[str, object] = {
        "selectedRoute": selected,
        "designPackPin": pin,
        "narrativeIrDigest": None,
        "fallbackReason": fallback_reason,
    }
    return unsigned


__all__ = [
    "GenerationRouteError",
    "design_pack_storage_root",
    "load_pinned_design_pack",
    "normalize_design_pack_pin",
    "resolve_internal_design_pack_pin",
    "resolve_generation_route",
]
