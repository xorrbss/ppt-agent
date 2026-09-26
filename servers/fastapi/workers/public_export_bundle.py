"""Export bundle schema and input readback."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from services.authored_canvas import (
    AuthoredCanvasMetadata,
    LANDSCAPE_ASPECT_RATIO,
    canvas_metadata_for_aspect_ratio,
)
from services.document_canvas import parse_canvas_metadata
from services.isolated_worker_contracts import (
    ArtifactRef, IsolatedWorkerContractError, IsolatedWorkerJob,
    reject_sensitive_worker_payload,
)
from workers.public_worker_runtime import LocalContentAddressedStore, PublicWorkerExecutionError


EXPORT_BUNDLE_SCHEMA = "public-export-bundle/v1"
EXPORT_CHECKPOINT_BUNDLE_SCHEMA = "public-export-bundle/v2"
FIDELITY_RASTER_MODE = "fidelity-raster"
EDITABLE_HYBRID_MODE = "editable-hybrid"
MAX_SLIDES = 100
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class ExportBundle:
    """One validated export input: raster checkpoints plus optional sources."""

    export_format: str
    export_mode: str
    slides: list[ArtifactRef]
    source_slides: list[ArtifactRef]
    font_embedding: bool
    #: Declared semantic graph, one entry per slide, inline in the bundle. It
    #: decides which containers the assembler may promote to native OOXML.
    semantic_slides: list[Mapping[str, object]]
    export_attempt_id: str | None
    renderer_source_digest: str | None
    local_fit_receipts: list[Mapping[str, object]]
    canvas: AuthoredCanvasMetadata


def _read_bundle(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> ExportBundle:
    if job.input_artifact.media_type != "application/vnd.presenton.export-bundle+json":
        raise PublicWorkerExecutionError("public_export_bundle_media_type_invalid")
    payload = store.read(job.input_artifact)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicWorkerExecutionError("public_export_bundle_invalid_json") from error
    if not isinstance(value, Mapping):
        raise PublicWorkerExecutionError("public_export_bundle_invalid_shape")
    reject_sensitive_worker_payload(value)
    schema_version = value.get("schemaVersion")
    export_mode = FIDELITY_RASTER_MODE
    font_embedding = False
    if schema_version == EXPORT_BUNDLE_SCHEMA:
        expected_keys = {"schemaVersion", "exportFormat", "slides"}
    elif schema_version == EXPORT_CHECKPOINT_BUNDLE_SCHEMA:
        expected_keys = {
            "schemaVersion",
            "canonicalExportIdentity",
            "exportFormat",
            "exportMode",
            "renderAdapter",
            "slides",
        }
        raw_mode = value.get("exportMode")
        if raw_mode == EDITABLE_HYBRID_MODE:
            # A hybrid bundle additionally names the authored sources this
            # worker measures in its own Chrome before assembling native OOXML.
            expected_keys |= {"sourceSlides", "fontEmbedding"}
            if "semanticSlides" in value:
                # The declared graph is optional, but never half-present: it is
                # sealed against the export attempt and renderer runtime it was
                # projected for, so all three keys travel together.
                expected_keys |= {
                    "semanticSlides",
                    "exportAttemptId",
                    "rendererSourceDigest",
                }
            export_mode = EDITABLE_HYBRID_MODE
            raw_font_embedding = value.get("fontEmbedding")
            if not isinstance(raw_font_embedding, bool):
                raise PublicWorkerExecutionError("public_export_bundle_identity_invalid")
            font_embedding = raw_font_embedding
        elif raw_mode != FIDELITY_RASTER_MODE:
            raise PublicWorkerExecutionError("public_export_bundle_identity_invalid")
        if (
            not isinstance(value.get("canonicalExportIdentity"), str)
            or not _SHA256.fullmatch(value["canonicalExportIdentity"])
            or not isinstance(value.get("renderAdapter"), str)
            or not 1 <= len(value["renderAdapter"].strip()) <= 128
        ):
            raise PublicWorkerExecutionError("public_export_bundle_identity_invalid")
    else:
        raise PublicWorkerExecutionError("public_export_bundle_schema_unsupported")
    if "localFitReceipts" in value:
        expected_keys.add("localFitReceipts")
    if "rendererSourceDigest" in value:
        expected_keys.add("rendererSourceDigest")
    if "canvas" in value:
        expected_keys.add("canvas")
    if set(value) != expected_keys:
        raise PublicWorkerExecutionError("public_export_bundle_invalid_shape")
    if "canvas" in value:
        try:
            canvas = parse_canvas_metadata(value["canvas"])
        except (TypeError, ValueError) as error:
            raise PublicWorkerExecutionError("public_export_canvas_contract_invalid") from error
    else:
        canvas = canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO)
    export_format = value.get("exportFormat")
    if export_format not in {"pptx", "pdf"}:
        raise PublicWorkerExecutionError("public_export_format_invalid")
    raw_slides = value.get("slides")
    if not isinstance(raw_slides, list) or not 1 <= len(raw_slides) <= MAX_SLIDES:
        raise PublicWorkerExecutionError("public_export_slide_count_invalid")
    slides: list[ArtifactRef] = []
    for slide_index, raw in enumerate(raw_slides):
        if not isinstance(raw, Mapping):
            raise PublicWorkerExecutionError("public_export_slide_ref_invalid")
        raw_reference: Mapping[str, object] = raw
        if schema_version == EXPORT_CHECKPOINT_BUNDLE_SCHEMA:
            if (
                set(raw) != {"slideIndex", "artifactRef", "validationDigest"}
                or raw.get("slideIndex") != slide_index
                or not isinstance(raw.get("validationDigest"), str)
                or not _SHA256.fullmatch(raw["validationDigest"])
                or not isinstance(raw.get("artifactRef"), Mapping)
            ):
                raise PublicWorkerExecutionError("public_export_slide_checkpoint_invalid")
            raw_reference = raw["artifactRef"]
        try:
            reference = ArtifactRef.from_wire(raw_reference)
        except IsolatedWorkerContractError as error:
            raise PublicWorkerExecutionError("public_export_slide_ref_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.media_type != "image/png"
            or reference.kind != "slide-png"
        ):
            raise PublicWorkerExecutionError("public_export_slide_binding_mismatch")
        path = store.verified_path(reference)
        with path.open("rb") as stream:
            signature = stream.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise PublicWorkerExecutionError("public_export_slide_not_png")
        slides.append(reference)
    if export_mode == EDITABLE_HYBRID_MODE and export_format != "pptx":
        raise PublicWorkerExecutionError("public_export_bundle_identity_invalid")
    semantic_slides, export_attempt_id, renderer_source_digest = _read_declared_graph(value, expected=len(slides))
    local_fit_receipts = _read_local_fit_receipts(value, expected=len(slides))
    return ExportBundle(
        export_format=export_format,
        export_mode=export_mode,
        slides=slides,
        source_slides=_read_source_slides(job, store, value, expected=len(slides)),
        font_embedding=font_embedding,
        semantic_slides=semantic_slides,
        export_attempt_id=export_attempt_id,
        renderer_source_digest=renderer_source_digest,
        local_fit_receipts=local_fit_receipts,
        canvas=canvas,
    )


def _read_declared_graph(
    value: Mapping[str, object],
    *,
    expected: int,
) -> tuple[list[Mapping[str, object]], str | None, str | None]:
    """Validate the inline declared graph and the identity it is sealed against.

    The assembler seals a browser observation receipt naming this export
    attempt and renderer runtime, so a graph without either is refused rather
    than measured. The per-element shape is the assembler's own contract; this
    boundary checks presence, count and per-slide ordering.
    """

    raw_graph = value.get("semanticSlides")
    if raw_graph is None:
        renderer_digest = value.get("rendererSourceDigest")
        if renderer_digest is not None and (
            not isinstance(renderer_digest, str) or not _PREFIXED_SHA256.fullmatch(renderer_digest)
        ):
            raise PublicWorkerExecutionError("public_export_semantic_binding_invalid")
        return [], None, renderer_digest
    attempt_id = value.get("exportAttemptId")
    renderer_digest = value.get("rendererSourceDigest")
    if (
        not isinstance(raw_graph, list)
        or len(raw_graph) != expected
        or not isinstance(attempt_id, str)
        or not 1 <= len(attempt_id.strip()) <= 128
        or not isinstance(renderer_digest, str)
        or not _PREFIXED_SHA256.fullmatch(renderer_digest)
    ):
        raise PublicWorkerExecutionError("public_export_semantic_binding_invalid")
    graph: list[Mapping[str, object]] = []
    for slide_index, declared in enumerate(raw_graph):
        if (
            not isinstance(declared, Mapping)
            or declared.get("slideIndex") != slide_index
            or not isinstance(declared.get("declaredElementIds"), list)
            or not isinstance(declared.get("declaredElements"), list)
        ):
            raise PublicWorkerExecutionError("public_export_semantic_slide_invalid")
        graph.append(declared)
    return graph, attempt_id.strip(), renderer_digest


def _read_local_fit_receipts(
    value: Mapping[str, object],
    *,
    expected: int,
) -> list[Mapping[str, object]]:
    raw = value.get("localFitReceipts")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) != expected:
        raise PublicWorkerExecutionError("public_export_local_fit_receipts_invalid")
    receipts: list[Mapping[str, object]] = []
    for slide_index, receipt in enumerate(raw):
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("slideIndex") != slide_index
            or not isinstance(receipt.get("acceptanceKey"), str)
            or not isinstance(receipt.get("finalHtmlDigest"), str)
        ):
            raise PublicWorkerExecutionError("public_export_local_fit_receipts_invalid")
        receipts.append(receipt)
    return receipts


def _read_source_slides(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
    value: Mapping[str, object],
    *,
    expected: int,
) -> list[ArtifactRef]:
    """Validate the authored HTML the hybrid assembler is allowed to measure."""

    raw_sources = value.get("sourceSlides")
    if raw_sources is None:
        return []
    if not isinstance(raw_sources, list) or len(raw_sources) != expected:
        raise PublicWorkerExecutionError("public_export_source_slide_count_invalid")
    sources: list[ArtifactRef] = []
    for slide_index, raw in enumerate(raw_sources):
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"slideIndex", "artifactRef"}
            or raw.get("slideIndex") != slide_index
            or not isinstance(raw.get("artifactRef"), Mapping)
        ):
            raise PublicWorkerExecutionError("public_export_source_slide_ref_invalid")
        try:
            reference = ArtifactRef.from_wire(raw["artifactRef"])
        except IsolatedWorkerContractError as error:
            raise PublicWorkerExecutionError("public_export_source_slide_ref_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.kind != "slide-html"
            or reference.media_type != "text/html"
        ):
            raise PublicWorkerExecutionError("public_export_source_slide_binding_mismatch")
        store.verified_path(reference)
        sources.append(reference)
    return sources
