"""Admission-time lookup of an approved per-canvas design-pack variant.

A design pack is authored at one canvas. Another canvas is served only by a
separately approved variant (docs/aspect-ratio-expansion-design-20260928.md
decision 9); there is no shrink-to-fit fallback, so no variant means the
request keeps its existing rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
import uuid

from .registry import DesignPackRegistry, DesignPackRegistryError, RegisteredDesignPack


A4_CANVAS_IDS = {"landscape": "a4-landscape", "portrait": "a4-portrait"}


def _major(package: RegisteredDesignPack) -> int:
    return int(package.manifest.package_version.split(".", 1)[0])


def _matches(package: RegisteredDesignPack, pin: Mapping[str, object]) -> bool:
    return (
        package.manifest.pack_id == pin["designPackId"]
        and _major(package) == pin["version"]
        and package.manifest.package_digest == pin["digest"]
    )


def _registry(
    owner_id: uuid.UUID | str | None, storage_root: Path | str | None
) -> DesignPackRegistry | None:
    from services.authored_vnext.generation_route import (
        GenerationRouteError,
        design_pack_storage_root,
    )
    from utils.owner_context import get_current_owner_id

    try:
        if storage_root is None:
            owner = owner_id or get_current_owner_id()
            if owner is None:
                return None
            storage_root = design_pack_storage_root(owner)
        return DesignPackRegistry(storage_root)
    except (DesignPackRegistryError, GenerationRouteError, OSError):
        # Fail closed: an unreadable store admits nothing new.
        return None


def design_pack_variant_for_canvas(
    design_pack_pin: object,
    canvas_id: str | None,
    *,
    owner_id: uuid.UUID | str | None = None,
    storage_root: Path | str | None = None,
) -> RegisteredDesignPack | None:
    """Return the approved variant of the pinned pack for ``canvas_id``, if any.

    A pin that already names that approved variant resolves to it, so an
    admission replayed on a substituted request stays admitted.
    """

    if design_pack_pin is None or not canvas_id:
        return None
    from services.authored_vnext.generation_route import (
        GenerationRouteError,
        normalize_design_pack_pin,
    )

    try:
        pin = normalize_design_pack_pin(design_pack_pin)
    except GenerationRouteError:
        return None
    registry = _registry(owner_id, storage_root)
    if registry is None:
        return None
    for base in registry.list():
        if _matches(base, pin):
            return registry.variants(
                base.manifest.pack_id, base.manifest.package_version
            ).get(canvas_id)
    for package in registry.pinnable():
        if package.is_variant and package.canvas_id == canvas_id and _matches(package, pin):
            return package
    return None


def variant_pin(variant: RegisteredDesignPack) -> dict[str, object]:
    return {
        "designPackId": variant.manifest.pack_id,
        "version": _major(variant),
        "digest": variant.manifest.package_digest,
    }


def apply_design_pack_variant_pin(request: Any, variant: RegisteredDesignPack) -> None:
    """Carry the variant's exact identity downstream in place of the base pin."""

    current = getattr(request, "design_pack_pin", None)
    wire = variant_pin(variant)
    request.design_pack_pin = (
        type(current).model_validate(wire)
        if hasattr(type(current), "model_validate")
        else wire
    )


__all__ = [
    "A4_CANVAS_IDS",
    "apply_design_pack_variant_pin",
    "design_pack_variant_for_canvas",
    "variant_pin",
]
