"""Transport of per-slide NativeObjects sidecars inside the svg export bundle.

The bundle's optional top-level ``nativeObjects`` is one entry per slide:

* ``null`` -- the slide has no sidecar;
* ``{"status": "valid", "sidecar": <native-objects/v1 wire>}``;
* ``{"status": "invalid", "reason": <code>, "objects": [{"id": .., "kind": ..}]}``
  -- the stored sidecar failed validation. The export does not fail: every
  object it names (or the sidecar as one unit when none can be named) is
  recorded as fell back with that reason by the export scene.

The operation side builds entries from ``slide.content``; the worker parses
them strictly (any malformed entry rejects the bundle).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.generation_core.native_objects import (
    SLIDE_CONTENT_KEY,
    NativeObjects,
    NativeObjectsError,
    slide_native_objects,
)

BUNDLE_KEY = "nativeObjects"
_MAX_REASON, _MAX_LABEL, _MAX_INVALID_OBJECTS = 200, 128, 1000
_BUNDLE_INVALID = "svg_export_native_objects_invalid"


@dataclass(frozen=True)
class PageNativeObjects:
    """One slide's sidecar as the export scene consumes it."""

    sidecar: NativeObjects | None = None
    invalid_reason: str | None = None
    invalid_objects: tuple[tuple[str | None, str | None], ...] = ()


def _label(value: Any) -> str | None:
    return value[:_MAX_LABEL] if isinstance(value, str) else None


def bundle_entry(content: Any) -> dict | None:
    """The bundle entry for one slide's stored ``content`` (operation side)."""
    try:
        sidecar = slide_native_objects(content)
    except NativeObjectsError as error:
        raw = content[SLIDE_CONTENT_KEY]
        objects = raw.get("objects") if isinstance(raw, dict) else None
        named = [{"id": _label(item.get("id")), "kind": _label(item.get("kind"))}
                 for item in (objects if isinstance(objects, list) else []) if isinstance(item, dict)]
        return {"status": "invalid", "reason": str(error)[:_MAX_REASON], "objects": named[:_MAX_INVALID_OBJECTS]}
    return None if sidecar is None else {"status": "valid", "sidecar": sidecar.to_wire()}


def _parse_entry(entry: Any) -> PageNativeObjects:
    if entry is None:
        return PageNativeObjects()
    if not isinstance(entry, dict):
        raise ValueError(_BUNDLE_INVALID)
    if entry.get("status") == "valid" and set(entry) == {"status", "sidecar"}:
        try:
            return PageNativeObjects(sidecar=NativeObjects.from_wire(entry["sidecar"]))
        except NativeObjectsError as error:
            raise ValueError(_BUNDLE_INVALID) from error
    if entry.get("status") != "invalid" or set(entry) != {"status", "reason", "objects"}:
        raise ValueError(_BUNDLE_INVALID)
    reason, objects = entry["reason"], entry["objects"]
    if (not isinstance(reason, str) or not 0 < len(reason) <= _MAX_REASON
            or not isinstance(objects, list) or len(objects) > _MAX_INVALID_OBJECTS):
        raise ValueError(_BUNDLE_INVALID)
    named = []
    for item in objects:
        if (not isinstance(item, dict) or set(item) != {"id", "kind"}
                or any(value is not None and (not isinstance(value, str) or len(value) > _MAX_LABEL)
                       for value in item.values())):
            raise ValueError(_BUNDLE_INVALID)
        named.append((item["id"], item["kind"]))
    return PageNativeObjects(invalid_reason=reason, invalid_objects=tuple(named))


def parse_bundle_native_objects(value: Any, slide_count: int) -> list[PageNativeObjects]:
    """Strictly parse the bundle field (worker side); raises ``ValueError``."""
    if not isinstance(value, list) or len(value) != slide_count:
        raise ValueError(_BUNDLE_INVALID)
    return [_parse_entry(entry) for entry in value]


__all__ = ["BUNDLE_KEY", "PageNativeObjects", "bundle_entry", "parse_bundle_native_objects"]
