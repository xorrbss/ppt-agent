"""Post-publication native chart paint evidence for svg-native-requirements/v5.

The v5 chart paint proof (``utils.slide_capture_chart_paint``) needs 55-105 s
of capture planes per real chart, more than the render worker budget of a
generation, remake or other render pass. Those passes therefore render without
a chart witness manifest and record the chart's paint as the existing
``pending`` state; publication still requires the content geometry and value
proof. This module holds the pure parts of the separate verification that runs
the unchanged proof after publication:

* ``witness_source`` -- the page's bound chart declarations, kept in
  ``slide.content`` at publication so the manifest can be rebuilt from the
  published page (the svg itself no longer carries its declarations);
* ``seal_paint_evidence`` / ``current_paint_evidence`` -- the proof's result,
  stored in ``slide.properties`` and bound to the page's canonical svg digest,
  the rebuilt manifest digest and the page's NativeObjects sidecar, so evidence
  of another page state never applies;
* ``chart_paint_fallbacks`` -- the export's per-chart decision: a v5 chart is
  native only with passed, current evidence; otherwise it stays drawn with a
  recorded reason. Nothing here can fail a deck.

Earlier requirement revisions never reach any of this: every entry point
returns its empty value unless the revision is literally v5.
"""
from __future__ import annotations

from copy import deepcopy

WITNESS_SOURCE_KEY = "nativeChartWitnessSource"
EVIDENCE_KEY = "svgNativeChartPaint"
WITNESS_SOURCE_SCHEMA = "svg-native-chart-witness-source/v1"
EVIDENCE_SCHEMA = "svg-native-chart-paint-evidence/v1"
# The isolated renderer worker admits at most 120 s per capture
# (``utils.authored_renderer_worker``); the verification uses all of it,
# twice the 60 s default a render pass gets.
VERIFICATION_TIMEOUT_SECONDS = 120
UNVERIFIED_REASON = "native_chart_paint_unverified"
_MAX_REASON = 200


def _v5(revision) -> bool:
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    return revision == REQUIREMENTS_VERSION_V5


def witness_source(payload: dict, svg: str | None, *, revision) -> dict | None:
    """The bound chart declaration record a published v5 page keeps, else ``None``."""
    if not _v5(revision) or not isinstance(payload, dict) or not svg:
        return None
    from services.generation_core.drivers.svg.native_declarations import bound_declarations
    record, current = bound_declarations(payload, svg)
    if not current or not any(isinstance(item, dict) and item.get("kind") == "chart"
                              for item in record.get("objects") or []):
        return None
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    return {"schema": WITNESS_SOURCE_SCHEMA, "requirementsRevision": REQUIREMENTS_VERSION_V5,
            "declarations": deepcopy(record)}


def verification_manifest(content, svg: str | None) -> dict | None:
    """Rebuild the server-derived witness manifest of a published page, or ``None``."""
    from services.generation_core.drivers.svg.native_declarations import PAYLOAD_KEY
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    source = (content or {}).get(WITNESS_SOURCE_KEY) if isinstance(content, dict) else None
    if (not isinstance(source, dict) or set(source) != {"schema", "requirementsRevision", "declarations"}
            or source["schema"] != WITNESS_SOURCE_SCHEMA
            or source["requirementsRevision"] != REQUIREMENTS_VERSION_V5
            or not isinstance(source["declarations"], dict) or not svg):
        return None
    from services.generation_core.drivers.svg.native_chart_evidence import chart_witness_render_kwargs
    return chart_witness_render_kwargs({PAYLOAD_KEY: source["declarations"]}, svg,
                                       revision=REQUIREMENTS_VERSION_V5).get("chart_witness_manifest")


def _native_objects_digest(content) -> str:
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY
    from utils.hashing import canonical_json_digest
    return canonical_json_digest({"nativeObjects": (content or {}).get(SLIDE_CONTENT_KEY)})


def _body(svg: str, manifest: dict, content, paint: dict) -> dict:
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    from services.svg_contract import source_digest
    return {"schema": EVIDENCE_SCHEMA, "requirementsRevision": REQUIREMENTS_VERSION_V5,
            "svgDigest": source_digest(svg), "manifestDigest": manifest["manifestDigest"],
            "nativeObjectsDigest": _native_objects_digest(content),
            "chartIds": sorted(chart["id"] for chart in manifest["charts"]), "paint": deepcopy(paint)}


def seal_paint_evidence(svg: str, manifest: dict, content, paint: dict) -> dict:
    """The stored record of one completed proof validation (passed or failed)."""
    from utils.hashing import canonical_json_digest
    body = _body(svg, manifest, content, paint)
    return {**body, "digest": canonical_json_digest(body)}


def current_paint_evidence(properties, content, svg: str | None, *, manifest: dict | None = None) -> dict | None:
    """The stored record when it binds exactly this page state; stale or malformed is ``None``."""
    record = (properties or {}).get(EVIDENCE_KEY) if isinstance(properties, dict) else None
    if not isinstance(record, dict) or not isinstance(record.get("paint"), dict) or not svg:
        return None
    if manifest is None:
        manifest = verification_manifest(content, svg)
    if manifest is None:
        return None
    try:
        expected = seal_paint_evidence(svg, manifest, content, record["paint"])
    except (KeyError, TypeError, ValueError):
        return None
    return record if record == expected else None


def _required_source_chart_ids(content) -> set:
    receipt = ((content or {}).get("svgReceipt") or {}).get("source_preservation") or {}
    return {item.get("element_id") for key in ("whole_page_native_groups", "source_native_charts")
            for item in receipt.get(key) or [] if isinstance(item, dict)}


def gated_chart_ids(content, sidecar, *, revision) -> list[str]:
    """Sidecar charts whose native export needs post-publication paint evidence.

    Source-preserved charts keep their own sealed source authority (their
    export already fails closed on any numeric mismatch); they are not gated.
    """
    if not _v5(revision) or sidecar is None:
        return []
    required = _required_source_chart_ids(content)
    return sorted(item.id for item in sidecar.objects if item.kind == "chart" and item.id not in required)


def chart_paint_fallbacks(content, properties, svg: str | None, sidecar, *, revision) -> list[dict]:
    """``[{"id", "reason"}]`` for every gated chart that must stay drawn."""
    gated = gated_chart_ids(content, sidecar, revision=revision)
    if not gated:
        return []
    record = current_paint_evidence(properties, content, svg)
    paint = (record or {}).get("paint") or {}
    proven = set(record["chartIds"]) if record is not None and paint.get("passed") is True else set()
    if record is None:
        reason = UNVERIFIED_REASON
    elif paint.get("passed") is True:
        reason = UNVERIFIED_REASON
    else:
        reason = f"native_chart_paint_failed:{paint.get('reason') or 'native_chart_capture_unproven'}"
    return [{"id": chart_id, "reason": reason[:_MAX_REASON]} for chart_id in gated if chart_id not in proven]


__all__ = [
    "EVIDENCE_KEY", "EVIDENCE_SCHEMA", "UNVERIFIED_REASON", "VERIFICATION_TIMEOUT_SECONDS",
    "WITNESS_SOURCE_KEY", "WITNESS_SOURCE_SCHEMA", "chart_paint_fallbacks", "current_paint_evidence",
    "gated_chart_ids", "seal_paint_evidence", "verification_manifest", "witness_source",
]
