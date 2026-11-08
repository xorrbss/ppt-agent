"""Transport of per-slide NativeObjects sidecars inside the svg export bundle.

The bundle's optional top-level ``nativeObjects`` is one entry per slide:

* ``null`` -- the slide has no sidecar;
* ``{"status": "valid", "sidecar": <native-objects/v1 wire>}``, optionally with
  ``"chartPaintFallbacks": [{"id": .., "reason": ..}]`` -- svg-native-requirements/v5
  charts without passed, current post-publication paint evidence; each stays
  drawn and is recorded as fell back with its reason (absent for every other
  revision, so earlier bundles are unchanged);
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
    source_chart_policy: dict | None = None
    chart_paint_fallbacks: tuple[tuple[str, str], ...] = ()


def _label(value: Any) -> str | None:
    return value[:_MAX_LABEL] if isinstance(value, str) else None


def bundle_entry(content: Any, *, chart_paint_fallbacks: list[dict] | None = None) -> dict | None:
    """The bundle entry for one slide's stored ``content`` (operation side).

    ``chart_paint_fallbacks`` (v5 only) names sidecar charts that must stay drawn.
    """
    try:
        sidecar = slide_native_objects(content)
    except NativeObjectsError as error:
        if ((content.get("svgReceipt") or {}).get("source_preservation") or {}).get("source_native_charts"):
            raise ValueError("svg_source_chart_export_numeric_mismatch") from error
        raw = content[SLIDE_CONTENT_KEY]
        objects = raw.get("objects") if isinstance(raw, dict) else None
        named = [{"id": _label(item.get("id")), "kind": _label(item.get("kind"))}
                 for item in (objects if isinstance(objects, list) else []) if isinstance(item, dict)]
        return {"status": "invalid", "reason": str(error)[:_MAX_REASON], "objects": named[:_MAX_INVALID_OBJECTS]}
    if sidecar is None:
        return None
    from services.svg_source_charts import source_chart_export_policy
    policy = source_chart_export_policy(content, sidecar)
    fallbacks = _chart_paint_fallbacks(chart_paint_fallbacks, sidecar) if chart_paint_fallbacks else None
    return {"status": "valid", "sidecar": sidecar.to_wire(),
            **({"sourceChartPolicy": policy} if policy is not None else {}),
            **({"chartPaintFallbacks": fallbacks} if fallbacks else {})}


def _chart_paint_fallbacks(value: Any, sidecar: NativeObjects) -> list[dict]:
    """Strict, sorted ``[{"id", "reason"}]`` naming distinct chart objects of ``sidecar``."""
    charts = {item.id for item in sidecar.objects if item.kind == "chart"}
    if not isinstance(value, list) or not 0 < len(value) <= _MAX_INVALID_OBJECTS:
        raise ValueError(_BUNDLE_INVALID)
    result, seen = [], set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"id", "reason"}
                or item["id"] not in charts or item["id"] in seen
                or not isinstance(item["reason"], str) or not 0 < len(item["reason"]) <= _MAX_REASON):
            raise ValueError(_BUNDLE_INVALID)
        seen.add(item["id"])
        result.append({"id": item["id"], "reason": item["reason"]})
    if result != sorted(result, key=lambda entry: entry["id"]):
        raise ValueError(_BUNDLE_INVALID)
    return result


def _parse_entry(entry: Any) -> PageNativeObjects:
    if entry is None:
        return PageNativeObjects()
    if not isinstance(entry, dict):
        raise ValueError(_BUNDLE_INVALID)
    if entry.get("status") == "valid" and set(entry) - {"chartPaintFallbacks"} in (
            {"status", "sidecar"}, {"status", "sidecar", "sourceChartPolicy"}):
        try:
            sidecar = NativeObjects.from_wire(entry["sidecar"])
            policy = entry.get("sourceChartPolicy")
            if "sourceChartPolicy" in entry:
                from services.svg_source_charts import validate_source_chart_export_policy
                if policy is None:
                    raise ValueError("svg_source_chart_export_policy_invalid")
                validate_source_chart_export_policy(policy, sidecar)
            fallbacks = ()
            if "chartPaintFallbacks" in entry:
                fallbacks = tuple((item["id"], item["reason"])
                                  for item in _chart_paint_fallbacks(entry["chartPaintFallbacks"], sidecar))
                required = {record["id"] for record in (policy or {}).get("charts", [])}
                if required & {chart_id for chart_id, _ in fallbacks}:
                    raise ValueError(_BUNDLE_INVALID)
            return PageNativeObjects(sidecar=sidecar, source_chart_policy=policy,
                                     chart_paint_fallbacks=fallbacks)
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
