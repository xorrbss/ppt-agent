"""Bounded export bundle references for trusted worker staging."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError

MAX_EXPORT_BUNDLE_BYTES = 8 * 1024 * 1024


def _input_bundle_references(job: IsolatedWorkerJob, path: Path) -> tuple[ArtifactRef, ...]:
    if job.role == "render" and job.input_artifact.kind != "export-bundle":
        return ()
    if job.role not in {"render", "export"}:
        return ()
    if job.input_artifact.size_bytes > MAX_EXPORT_BUNDLE_BYTES:
        raise TrustedWorkerWireError("trusted_worker_bundle_too_large")
    try:
        with path.open("rb") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedWorkerWireError("trusted_worker_bundle_invalid") from error
    if not isinstance(value, Mapping) or not isinstance(value.get("slides"), list):
        raise TrustedWorkerWireError("trusted_worker_bundle_invalid")
    if job.role == "render" and value.get("schemaVersion") not in {
        "public-render-bundle/v1",
        "public-render-bundle/v2",
    }:
        raise TrustedWorkerWireError("trusted_worker_render_bundle_invalid")
    svg_export = job.role == "export" and value.get("schemaVersion") in {
        "public-svg-export-bundle/v1",
        "public-svg-export-bundle/v2",
        "public-svg-export-bundle/v3",
    }
    expected_kind = "slide-svg" if svg_export else ("slide-html" if job.role == "render" else "slide-png")
    expected_media_type = "image/svg+xml" if svg_export else ("text/html" if job.role == "render" else "image/png")
    result = _staged_bundle_slides(job, value["slides"], kind=expected_kind, media_type=expected_media_type)
    if not 1 <= len(result) <= 100:
        raise TrustedWorkerWireError("trusted_worker_bundle_slide_count_invalid")
    if svg_export and value.get("schemaVersion") in {
        "public-svg-export-bundle/v2",
        "public-svg-export-bundle/v3",
    }:
        asset_refs = []
        for entry in value["slides"]:
            assets = entry.get("assets")
            if assets is None:
                continue
            required_asset_keys = {
                "sourceManifestRef",
                "placementManifestRef",
                "sourceManifestDigest",
                "placementManifestDigest",
                "pageIndex",
                "slideRevision",
                "derivatives",
            }
            if (
                not isinstance(assets, Mapping)
                or set(assets) not in (required_asset_keys, required_asset_keys | {"fullPagePreserve"})
                or ("fullPagePreserve" in assets and type(assets["fullPagePreserve"]) is not bool)
                or not isinstance(assets["derivatives"], Mapping)
                or len(assets["derivatives"]) > 4096
            ):
                raise TrustedWorkerWireError("trusted_worker_svg_asset_bundle_invalid")
            for name, kind in (
                ("sourceManifestRef", "source-manifest"),
                ("placementManifestRef", "placement-manifest"),
            ):
                asset_refs.extend(_staged_bundle_slides(job, [assets[name]], kind=kind, media_type="application/json"))
            for wire in assets["derivatives"].values():
                if not isinstance(wire, Mapping) or wire.get("mediaType") not in {"image/png", "image/jpeg"}:
                    raise TrustedWorkerWireError("trusted_worker_svg_derivative_type_invalid")
                asset_refs.extend(
                    _staged_bundle_slides(job, [wire], kind="image-derivative", media_type=wire["mediaType"])
                )
        if any(ref.created_by_operation_id != job.operation_id for ref in (*result, *asset_refs)):
            raise TrustedWorkerWireError("trusted_worker_svg_asset_operation_mismatch")
        result = (*result, *asset_refs)
    # An editable-hybrid export additionally measures the authored HTML inside
    # the isolated worker, so those sources must be staged with the rasters.
    sources = value.get("sourceSlides")
    if job.role == "export" and sources is not None:
        if not isinstance(sources, list) or len(sources) != len(result):
            raise TrustedWorkerWireError("trusted_worker_bundle_source_count_invalid")
        result = (
            *result,
            *_staged_bundle_slides(job, sources, kind="slide-html", media_type="text/html"),
        )
    return tuple(result)


def _staged_bundle_slides(
    job: IsolatedWorkerJob,
    entries: Sequence[object],
    *,
    kind: str,
    media_type: str,
) -> tuple[ArtifactRef, ...]:
    result: list[ArtifactRef] = []
    for entry in entries:
        raw = entry.get("artifactRef") if isinstance(entry, Mapping) else None
        if raw is None:
            raw = entry
        if not isinstance(raw, Mapping):
            raise TrustedWorkerWireError("trusted_worker_bundle_slide_ref_invalid")
        try:
            reference = ArtifactRef.from_wire(raw)
        except ValueError as error:
            raise TrustedWorkerWireError("trusted_worker_bundle_slide_ref_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.kind != kind
            or reference.media_type != media_type
            or (job.role == "render" and reference.created_by_operation_id != job.operation_id)
        ):
            raise TrustedWorkerWireError("trusted_worker_bundle_slide_binding_mismatch")
        result.append(reference)
    return tuple(result)


def _render_output_bundle_references(
    job: IsolatedWorkerJob,
    *,
    output_reference: ArtifactRef,
    path: Path,
) -> tuple[tuple[ArtifactRef, ...], dict[str, object]]:
    if job.role != "render" or output_reference.kind != "export-bundle":
        return (), {}
    if output_reference.media_type != "application/vnd.presenton.export-bundle+json":
        raise TrustedWorkerWireError("trusted_worker_render_output_bundle_invalid")
    try:
        with path.open("rb") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedWorkerWireError("trusted_worker_render_output_bundle_invalid") from error
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schemaVersion", "slides", "runtime"}
        or value.get("schemaVersion") != "public-render-result-bundle/v1"
        or not isinstance(value.get("slides"), list)
        or not 1 <= len(value["slides"]) <= 100
        or not isinstance(value.get("runtime"), Mapping)
    ):
        raise TrustedWorkerWireError("trusted_worker_render_output_bundle_invalid")
    outputs: list[ArtifactRef] = []
    for index, item in enumerate(value["slides"]):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"slideIndex", "artifactRef"}
            or item.get("slideIndex") != index
            or not isinstance(item.get("artifactRef"), Mapping)
        ):
            raise TrustedWorkerWireError("trusted_worker_render_output_slide_invalid")
        try:
            reference = ArtifactRef.from_wire(item["artifactRef"])
        except ValueError as error:
            raise TrustedWorkerWireError("trusted_worker_render_output_slide_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.created_by_operation_id != job.operation_id
            or reference.kind != "slide-png"
            or reference.media_type != "image/png"
        ):
            raise TrustedWorkerWireError("trusted_worker_render_output_slide_binding_mismatch")
        outputs.append(reference)
    runtime = value["runtime"]
    expected_runtime = {
        "chromiumProcessLaunchCount": 1,
        "browserReuseCount": len(outputs) - 1,
        "pageSessionCount": len(outputs),
    }
    if dict(runtime) != expected_runtime:
        raise TrustedWorkerWireError("trusted_worker_render_runtime_invalid")
    return tuple(outputs), {
        "renderBatchSlideCount": len(outputs),
        **expected_runtime,
    }


def _validated_success_output(payload: bytes, job: IsolatedWorkerJob) -> ArtifactRef | None:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedWorkerWireError("trusted_worker_result_invalid") from error
    if not isinstance(value, Mapping):
        raise TrustedWorkerWireError("trusted_worker_result_invalid")
    if value.get("ok") is not True:
        return None
    bindings = (
        value.get("role") == job.role,
        value.get("operationId") == str(job.operation_id),
        value.get("fencingToken") == job.fencing_token,
        value.get("inputDigest") == job.input_digest,
        value.get("attemptTokenSha256") == hashlib.sha256(job.attempt_token.encode("utf-8")).hexdigest(),
    )
    if not all(bindings) or not isinstance(value.get("outputArtifact"), Mapping):
        raise TrustedWorkerWireError("trusted_worker_result_binding_mismatch")
    try:
        reference = ArtifactRef.from_wire(value["outputArtifact"])
    except ValueError as error:
        raise TrustedWorkerWireError("trusted_worker_output_ref_invalid") from error
    if (
        reference.owner_id != job.input_artifact.owner_id
        or reference.tenant_id != job.input_artifact.tenant_id
        or reference.created_by_operation_id != job.operation_id
    ):
        raise TrustedWorkerWireError("trusted_worker_output_binding_mismatch")
    return reference
