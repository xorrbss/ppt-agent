"""Bounded export bundle references for trusted worker staging."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError
from utils.hashing import sha256_hex

# The svg export bundle inlines the sealed source manifest, which scales with the source
# document (a 100-page deck measured 13 MB of an 18 MB bundle).
MAX_EXPORT_BUNDLE_BYTES = 32 * 1024 * 1024


def _furniture_metadata_valid(value: object) -> bool:
    """Admit bounded proof metadata; the SVG worker verifies its exact bytes."""
    if not isinstance(value, Mapping) or set(value) != {
        "version", "svg_digest", "canvas", "digest", "element_ids", "images",
    }:
        return False
    canvas, ids, images = value["canvas"], value["element_ids"], value["images"]
    return (
        value["version"] == "svg-system-furniture/v1"
        and all(isinstance(value[key], str) and 1 <= len(value[key]) <= 128 for key in ("svg_digest", "digest"))
        and isinstance(canvas, Mapping) and set(canvas) == {"width", "height"}
        and all(type(canvas[key]) is int and canvas[key] > 0 for key in canvas)
        and isinstance(ids, list) and 1 <= len(ids) <= 10000
        and all(isinstance(node_id, str) and node_id.startswith("svg-system-") for node_id in ids)
        and len(set(ids)) == len(ids)
        and isinstance(images, Mapping) and len(images) <= 4096
        and all(key in ids and isinstance(digest, str) and 1 <= len(digest) <= 128 for key, digest in images.items())
    )


def _input_bundle_references(job: IsolatedWorkerJob, path: Path) -> tuple[ArtifactRef, ...]:
    if job.role != "export":
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
    svg_export = value.get("schemaVersion") in {
        "public-svg-export-bundle/v1",
        "public-svg-export-bundle/v2",
        "public-svg-export-bundle/v3",
        "public-svg-export-bundle/v4",
    }
    if not svg_export:
        # The raster PNG and editable-hybrid export bundles left with the
        # authored strategy; the export worker refuses any other schema with
        # public_export_bundle_schema_unsupported, so nothing is staged for it.
        return ()
    result = _staged_bundle_slides(job, value["slides"], kind="slide-svg", media_type="image/svg+xml")
    if not 1 <= len(result) <= 100:
        raise TrustedWorkerWireError("trusted_worker_bundle_slide_count_invalid")
    if value.get("schemaVersion") in {
        "public-svg-export-bundle/v2",
        "public-svg-export-bundle/v3",
        "public-svg-export-bundle/v4",
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
                or not required_asset_keys <= set(assets)
                or set(assets) - required_asset_keys - {
                    "fullPagePreserve", "systemFurniture", "sourceLineageRef", "sourceLineageDigest",
                }
                or ("sourceLineageRef" in assets) != ("sourceLineageDigest" in assets)
                or ("fullPagePreserve" in assets and type(assets["fullPagePreserve"]) is not bool)
                or ("systemFurniture" in assets and not _furniture_metadata_valid(assets["systemFurniture"]))
                or not isinstance(assets["derivatives"], Mapping)
                or len(assets["derivatives"]) > 4096
            ):
                raise TrustedWorkerWireError("trusted_worker_svg_asset_bundle_invalid")
            for name, kind in (
                ("sourceManifestRef", "source-manifest"),
                ("placementManifestRef", "placement-manifest"),
            ):
                asset_refs.extend(_staged_bundle_slides(job, [assets[name]], kind=kind, media_type="application/json"))
            if "sourceLineageRef" in assets:
                lineage_refs = _staged_bundle_slides(
                    job, [assets["sourceLineageRef"]], kind="svg-export-source-lineage", media_type="application/json"
                )
                if not isinstance(assets["sourceLineageDigest"], str) or assets["sourceLineageDigest"] not in {
                    lineage_refs[0].sha256, "sha256:" + lineage_refs[0].sha256,
                }:
                    raise TrustedWorkerWireError("trusted_worker_svg_asset_bundle_invalid")
                asset_refs.extend(lineage_refs)
            for wire in assets["derivatives"].values():
                if not isinstance(wire, Mapping) or wire.get("mediaType") not in {"image/png", "image/jpeg"}:
                    raise TrustedWorkerWireError("trusted_worker_svg_derivative_type_invalid")
                asset_refs.extend(
                    _staged_bundle_slides(job, [wire], kind="image-derivative", media_type=wire["mediaType"])
                )
        if any(ref.created_by_operation_id != job.operation_id for ref in (*result, *asset_refs)):
            raise TrustedWorkerWireError("trusted_worker_svg_asset_operation_mismatch")
        result = (*result, *asset_refs)
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
        ):
            raise TrustedWorkerWireError("trusted_worker_bundle_slide_binding_mismatch")
        result.append(reference)
    return tuple(result)


def _final_pptx_render_references(job, *, output_reference, stdout):
    """Only quality.review may produce final Office render evidence."""
    if job.role != "export" or output_reference.kind != "presentation-pptx":
        return ()
    result = json.loads(stdout)
    structural = result.get("executionEvidence", {}).get("structuralReadback", {})
    if "finalPptxRender" in structural:
        raise TrustedWorkerWireError("trusted_worker_final_pptx_render_not_allowed")
    return ()


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
        value.get("attemptTokenSha256") == sha256_hex(job.attempt_token.encode("utf-8")),
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
