"""Sessionless PNG bundle to verified PPTX/PDF export worker."""

from __future__ import annotations

import json
import re
import time
import zipfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

from PIL import Image
import pypdfium2 as pdfium
from pptx import Presentation
from pptx.util import Emu

from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    reject_sensitive_worker_payload,
)
from workers.public_export_hybrid import assemble_hybrid_pptx
from workers.public_worker_runtime import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
    WorkerOutput,
    main,
)


EXPORT_BUNDLE_SCHEMA = "public-export-bundle/v1"
EXPORT_CHECKPOINT_BUNDLE_SCHEMA = "public-export-bundle/v2"
FIDELITY_RASTER_MODE = "fidelity-raster"
EDITABLE_HYBRID_MODE = "editable-hybrid"
MAX_SLIDES = 100
# The authored slide canvas the hybrid assembler requires, in EMU. python-pptx's
# Inches(13.333333) lands one EMU short of it.
SLIDE_WIDTH_EMU = 12_192_000
SLIDE_HEIGHT_EMU = 6_858_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
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
                raise PublicWorkerExecutionError(
                    "public_export_bundle_identity_invalid"
                )
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
    if set(value) != expected_keys:
        raise PublicWorkerExecutionError("public_export_bundle_invalid_shape")
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
                raise PublicWorkerExecutionError(
                    "public_export_slide_checkpoint_invalid"
                )
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
    semantic_slides, export_attempt_id, renderer_source_digest = _read_declared_graph(
        value, expected=len(slides)
    )
    return ExportBundle(
        export_format=export_format,
        export_mode=export_mode,
        slides=slides,
        source_slides=_read_source_slides(job, store, value, expected=len(slides)),
        font_embedding=font_embedding,
        semantic_slides=semantic_slides,
        export_attempt_id=export_attempt_id,
        renderer_source_digest=renderer_source_digest,
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
        return [], None, None
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
            raise PublicWorkerExecutionError(
                "public_export_source_slide_ref_invalid"
            ) from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.kind != "slide-html"
            or reference.media_type != "text/html"
        ):
            raise PublicWorkerExecutionError(
                "public_export_source_slide_binding_mismatch"
            )
        store.verified_path(reference)
        sources.append(reference)
    return sources


def _build_pptx(slides: list[ArtifactRef], store: LocalContentAddressedStore, target: Path) -> None:
    deck = Presentation()
    deck.slide_width = Emu(SLIDE_WIDTH_EMU)
    deck.slide_height = Emu(SLIDE_HEIGHT_EMU)
    blank = deck.slide_layouts[6]
    while deck.slides:
        relationship_id = deck.slides._sldIdLst[-1].rId
        deck.part.drop_rel(relationship_id)
        del deck.slides._sldIdLst[-1]
    for reference in slides:
        slide = deck.slides.add_slide(blank)
        slide.shapes.add_picture(
            str(store.verified_path(reference)),
            0,
            0,
            width=deck.slide_width,
            height=deck.slide_height,
        )
    deck.save(str(target))
    try:
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            slide_parts = [
                name
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ]
            if (
                "[Content_Types].xml" not in names
                or "ppt/presentation.xml" not in names
                or len(slide_parts) != len(slides)
            ):
                raise PublicWorkerExecutionError("public_export_pptx_readback_failed")
    except zipfile.BadZipFile as error:
        raise PublicWorkerExecutionError("public_export_pptx_readback_failed") from error


def _build_pdf(slides: list[ArtifactRef], store: LocalContentAddressedStore, target: Path) -> None:
    document = pdfium.PdfDocument.new()
    try:
        for reference in slides:
            with Image.open(store.verified_path(reference)) as image:
                rgb = image.convert("RGB")
                try:
                    bitmap = pdfium.PdfBitmap.from_pil(rgb)
                    page = document.new_page(960, 540)
                    pdf_image = pdfium.PdfImage.new(document)
                    pdf_image.set_bitmap(bitmap)
                    pdf_image.set_matrix(pdfium.PdfMatrix(960, 0, 0, 540, 0, 0))
                    page.insert_obj(pdf_image)
                    page.gen_content()
                    page.close()
                finally:
                    rgb.close()
        document.save(target)
        with pdfium.PdfDocument(target) as readback:
            page_count = len(readback)
        if page_count != len(slides):
            raise PublicWorkerExecutionError("public_export_pdf_readback_failed")
    finally:
        document.close()


async def execute_export(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> WorkerOutput:
    bundle = _read_bundle(job, store)
    export_format = bundle.export_format
    with tempfile.TemporaryDirectory(prefix="public-export-") as temporary:
        scratch = Path(temporary)
        target = scratch / f"result.{export_format}"
        if export_format == "pptx":
            _build_pptx(bundle.slides, store, target)
        else:
            _build_pdf(bundle.slides, store, target)
        delivered_mode = FIDELITY_RASTER_MODE
        evidence: dict[str, object] = {}
        fallback_reason: str | None = None
        if bundle.export_mode == EDITABLE_HYBRID_MODE:
            if not bundle.source_slides:
                raise PublicWorkerExecutionError(
                    "public_export_source_slides_required"
                )
            outcome = await assemble_hybrid_pptx(
                base_pptx=target,
                html_paths=[
                    store.verified_path(reference)
                    for reference in bundle.source_slides
                ],
                scratch=scratch,
                font_embedding=bundle.font_embedding,
                budget_seconds=job.deadline_epoch_seconds - time.time(),
                semantic_slides=bundle.semantic_slides,
                export_attempt_id=bundle.export_attempt_id,
                renderer_source_digest=bundle.renderer_source_digest,
            )
            evidence = {
                "declaredGraphSlides": len(bundle.semantic_slides),
                **outcome.evidence,
            }
            fallback_reason = outcome.fallback_reason
            if outcome.output_path is not None:
                target = outcome.output_path
                delivered_mode = EDITABLE_HYBRID_MODE
        artifact = store.put_path(
            target,
            source=job.input_artifact,
            operation_id=job.operation_id,
            kind=f"presentation-{export_format}",
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                if export_format == "pptx"
                else "application/pdf"
            ),
        )
        return WorkerOutput(
            artifact=artifact,
            evidence={
                "requestedExportMode": bundle.export_mode,
                "deliveredExportMode": delivered_mode,
                "fallbackReason": fallback_reason,
                **evidence,
            },
        )


if __name__ == "__main__":
    raise SystemExit(main("export", execute_export))
