"""Sessionless PNG bundle to verified PPTX/PDF export worker."""

from __future__ import annotations

import json
import re
import time
import zipfile
import tempfile
import hashlib
import posixpath
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from xml.etree import ElementTree as ET

from PIL import Image
import pypdfium2 as pdfium
from services.document_canvas import parse_canvas_metadata, canvas_physical_size
from pptx import Presentation
from pptx.util import Emu

from services.authored_canvas import (
    AuthoredCanvasMetadata,
    LANDSCAPE_ASPECT_RATIO,
    canvas_metadata_for_aspect_ratio,
)
from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    reject_sensitive_worker_payload,
)
from workers.public_export_hybrid import HybridAssemblyOutcome, assemble_hybrid_pptx
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
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _bounded_local_fit_failure_code(error: BaseException) -> str:
    """Keep a machine-readable cause without retaining presentation content."""

    value = str(error).removeprefix("authored_local_fit_export:").lower()
    value = re.sub(r"[^a-z0-9_-]+", "_", value).strip("_")
    return value[:80] if value and value[0].isalpha() else type(error).__name__.lower()


def _record_page_fallbacks(
    evidence: dict[str, object],
    failed_pages: tuple[Mapping[str, object], ...],
) -> None:
    """Merge readback fallbacks into the existing user-visible quality receipt."""

    editability = evidence.get("editability")
    if isinstance(editability, dict):
        replaced = {int(page["slideIndex"]) + 1 for page in failed_pages}
        slides = editability.get("slides", [])
        for slide in slides:
            if slide.get("slideNumber") not in replaced:
                continue
            required = int(slide.get("requiredTargetCount", 0))
            for counts in slide.get("requiredByRole", {}).values():
                counts.update(
                    {"native": 0, "rasterized": counts.get("total", 0), "missing": 0, "occluded": 0, "unknown": 0}
                )
            slide.update(
                {
                    "nativeCorrespondenceCount": 0,
                    "rasterizedTargetCount": required,
                    "missingTargetCount": 0,
                    "occludedTargetCount": 0,
                    "imageOnly": True,
                    "fullSlideRaster": True,
                    "rasterFragmentCount": 0,
                    "verdict": "not-editable"
                    if required
                    else ("not-applicable" if slide.get("photoOnlyExempt") else "unknown"),
                    "reasons": ["local_fit_readback_failed"],
                }
            )
        for key in (
            "requiredTargetCount",
            "nativeCorrespondenceCount",
            "rasterizedTargetCount",
            "missingTargetCount",
            "occludedTargetCount",
            "unknownTargetCount",
            "rasterFragmentCount",
        ):
            editability[key] = sum(int(slide.get(key, 0)) for slide in slides)
        editability["fullSlideRasterCount"] = sum(bool(slide.get("fullSlideRaster")) for slide in slides)
        editability["imageOnlyPages"] = [slide["slideNumber"] for slide in slides if slide.get("imageOnly")]
        editability["requiredByRole"] = {
            role: {
                key: sum(int(slide.get("requiredByRole", {}).get(role, {}).get(key, 0)) for slide in slides)
                for key in ("total", "native", "rasterized", "missing", "occluded", "unknown")
            }
            for role in ("title", "body", "tableCell", "supportedShape")
        }
        native = editability["nativeCorrespondenceCount"]
        failed = sum(editability[key] for key in ("rasterizedTargetCount", "missingTargetCount", "occludedTargetCount"))
        unknown = editability["unknownTargetCount"] or any(slide.get("verdict") == "unknown" for slide in slides)
        editability["verdict"] = (
            "partially-editable"
            if failed and native
            else "unknown"
            if unknown
            else "not-editable"
            if failed
            else "fully-editable"
            if native
            else "not-applicable"
        )
    quality = evidence.get("quality")
    if not isinstance(quality, dict):
        return
    fallbacks = quality.get("slideFallbacks")
    if not isinstance(fallbacks, list):
        return
    additions = [{"n": int(page["slideIndex"]) + 1, "code": str(page["code"])} for page in failed_pages]
    existing = {item.get("n") for item in fallbacks if isinstance(item, Mapping)}
    additions = [item for item in additions if item["n"] not in existing]
    if not additions:
        return
    quality["slideFallbacks"] = fallbacks + additions
    quality["imageFallbackSlides"] = int(quality.get("imageFallbackSlides", 0)) + len(additions)
    quality["editableSlides"] = max(0, int(quality.get("editableSlides", 0)) - len(additions))
    tally = quality.get("fallbackReasonTally")
    if isinstance(tally, dict):
        tally["local_fit_readback_failed"] = int(tally.get("local_fit_readback_failed", 0)) + len(additions)
    quality["status"] = "partially-editable" if quality["editableSlides"] else "image-only"
    evidence.pop("structural", None)


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
    local_fit_receipts: list[Mapping[str, object]]
    canvas: AuthoredCanvasMetadata


def _slide_parts(archive: zipfile.ZipFile) -> list[str]:
    """Return presentation-ordered slide parts without trusting numeric names."""

    presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    relation_path = "ppt/_rels/presentation.xml.rels"
    relations = {
        item.get("Id"): (
            item.get("Target", "").lstrip("/")
            if item.get("Target", "").startswith("/")
            else posixpath.normpath(posixpath.join("ppt", item.get("Target", "")))
        )
        for item in ET.fromstring(archive.read(relation_path))
        if item.get("TargetMode") != "External"
    }
    return [
        relations[slide.get(_OFFICE_REL + "id")]
        for slide in presentation.findall(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldId")
    ]


def _slide_rel_path(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", name + ".rels")


def _replace_hybrid_pages_with_verified_raster(
    *,
    hybrid: Path,
    fidelity: Path,
    slide_indexes: tuple[int, ...],
    scratch: Path,
) -> Path:
    """Replace only refused slide parts with their identity-verified raster base.

    The source raster is admitted before this function is reached.  Healthy
    hybrid slide and package parts are copied byte-for-byte from ``hybrid``;
    each restored image gets a unique media target so it cannot overwrite a
    resource still referenced by a healthy native slide.
    """

    scratch.mkdir(parents=True, exist_ok=True)
    output = scratch / "hybrid-page-fallback.pptx"
    with zipfile.ZipFile(fidelity) as base, zipfile.ZipFile(hybrid) as current:
        if b'Extension="png"' not in base.read("[Content_Types].xml"):
            raise ValueError("hybrid_page_fallback_png_content_type_missing")
        base_presentation = ET.fromstring(base.read("ppt/presentation.xml"))
        current_presentation = ET.fromstring(current.read("ppt/presentation.xml"))
        if (
            base_presentation.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz").attrib
            != current_presentation.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz").attrib
        ):
            raise ValueError("hybrid_page_fallback_canvas_mismatch")
        base_parts, current_parts = _slide_parts(base), _slide_parts(current)
        if len(base_parts) != len(current_parts) or any(
            index < 0 or index >= len(base_parts) for index in slide_indexes
        ):
            raise ValueError("hybrid_page_fallback_slide_identity_mismatch")
        replacements: dict[str, bytes] = {}
        additions: dict[str, bytes] = {}
        for index in slide_indexes:
            source_part, target_part = base_parts[index], current_parts[index]
            replacements[target_part] = base.read(source_part)
            source_rel, target_rel = _slide_rel_path(source_part), _slide_rel_path(target_part)
            if source_rel not in base.namelist():
                raise ValueError("hybrid_page_fallback_relationship_missing")
            rel_root = ET.fromstring(base.read(source_rel))
            for relation in rel_root.findall(_REL_NS + "Relationship"):
                target = relation.get("Target", "")
                if relation.get("TargetMode") == "External" or not target:
                    continue
                source_resource = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
                )
                if not source_resource.startswith("ppt/media/"):
                    if (
                        source_resource not in base.namelist()
                        or source_resource not in current.namelist()
                        or base.read(source_resource) != current.read(source_resource)
                    ):
                        raise ValueError("hybrid_page_fallback_dependency_mismatch")
                    relation.set("Target", posixpath.relpath(source_resource, posixpath.dirname(target_part)))
                    continue
                if source_resource not in base.namelist():
                    raise ValueError("hybrid_page_fallback_image_missing")
                media_name = posixpath.basename(source_resource)
                media_digest = hashlib.sha256(base.read(source_resource)).hexdigest()
                suffix = posixpath.splitext(media_name)[1] or ".png"
                replacement_resource = f"ppt/media/fallback-slide-{index + 1}-{media_digest}{suffix}"
                additions[replacement_resource] = base.read(source_resource)
                relation.set("Target", posixpath.relpath(replacement_resource, posixpath.dirname(target_part)))
            serialized_rel = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)
            if target_rel in current.namelist():
                replacements[target_rel] = serialized_rel
            else:
                additions[target_rel] = serialized_rel
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as repaired:
            for item in current.infolist():
                if item.filename in replacements:
                    repaired.writestr(item, replacements[item.filename])
                else:
                    repaired.writestr(item, current.read(item.filename))
            for name, payload in additions.items():
                if name not in current.namelist():
                    repaired.writestr(name, payload)
                elif current.read(name) != payload:
                    raise ValueError("hybrid_page_fallback_package_collision")
    sidecar = Path(str(hybrid) + ".local-fit.json")
    if sidecar.is_file():
        evidence = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(evidence, dict) and isinstance(evidence.get("slides"), list):
            refused = {index + 1 for index in slide_indexes}
            evidence["slides"] = [slide for slide in evidence["slides"] if slide.get("slideNumber") not in refused]
            evidence["pptxDigest"] = "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()
            Path(str(output) + ".local-fit.json").write_text(
                json.dumps(evidence, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
            )
    return output


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


def _assert_slide_image_size(
    reference: ArtifactRef,
    store: LocalContentAddressedStore,
    canvas: AuthoredCanvasMetadata,
) -> Path:
    image_path = store.verified_path(reference)
    with Image.open(image_path) as image:
        if image.size != (canvas.width_px, canvas.height_px):
            raise PublicWorkerExecutionError("public_export_slide_canvas_mismatch")
    return image_path


def _build_pptx(
    slides: list[ArtifactRef],
    store: LocalContentAddressedStore,
    target: Path,
    canvas: AuthoredCanvasMetadata,
) -> None:
    deck = Presentation()
    width_emu, height_emu = canvas_physical_size(canvas)
    deck.slide_width = Emu(width_emu)
    deck.slide_height = Emu(height_emu)
    blank = deck.slide_layouts[6]
    while deck.slides:
        relationship_id = deck.slides._sldIdLst[-1].rId
        deck.part.drop_rel(relationship_id)
        del deck.slides._sldIdLst[-1]
    for reference in slides:
        slide = deck.slides.add_slide(blank)
        slide.shapes.add_picture(
            str(_assert_slide_image_size(reference, store, canvas)),
            0,
            0,
            width=deck.slide_width,
            height=deck.slide_height,
        )
    deck.save(str(target))
    try:
        readback = Presentation(str(target))
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            slide_parts = [name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
            if (
                "[Content_Types].xml" not in names
                or "ppt/presentation.xml" not in names
                or len(slide_parts) != len(slides)
                or int(readback.slide_width or 0) != width_emu
                or int(readback.slide_height or 0) != height_emu
            ):
                raise PublicWorkerExecutionError("public_export_pptx_readback_failed")
    except zipfile.BadZipFile as error:
        raise PublicWorkerExecutionError("public_export_pptx_readback_failed") from error


def _build_pdf(
    slides: list[ArtifactRef],
    store: LocalContentAddressedStore,
    target: Path,
    canvas: AuthoredCanvasMetadata,
) -> None:
    width_emu, height_emu = canvas_physical_size(canvas)
    width_pt = width_emu / 12700
    height_pt = height_emu / 12700
    document = pdfium.PdfDocument.new()
    try:
        for reference in slides:
            with Image.open(_assert_slide_image_size(reference, store, canvas)) as image:
                rgb = image.convert("RGB")
                try:
                    bitmap = pdfium.PdfBitmap.from_pil(rgb)
                    page = document.new_page(width_pt, height_pt)
                    pdf_image = pdfium.PdfImage.new(document)
                    pdf_image.set_bitmap(bitmap)
                    pdf_image.set_matrix(pdfium.PdfMatrix(width_pt, 0, 0, height_pt, 0, 0))
                    page.insert_obj(pdf_image)
                    page.gen_content()
                    page.close()
                finally:
                    rgb.close()
        document.save(target)
        with pdfium.PdfDocument(target) as readback:
            page_count = len(readback)
            page_sizes = [readback[index].get_size() for index in range(page_count)]
        if page_count != len(slides) or any(
            abs(width - width_pt) > 0.01 or abs(height - height_pt) > 0.01 for width, height in page_sizes
        ):
            raise PublicWorkerExecutionError("public_export_pdf_readback_failed")
    finally:
        document.close()


async def execute_export(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> WorkerOutput:
    from workers.public_svg_export_worker import is_svg_export_bundle, execute_svg_export

    if is_svg_export_bundle(job, store):
        try:
            return await execute_svg_export(job, store)
        except PublicWorkerExecutionError:
            raise
        except Exception as error:
            raise PublicWorkerExecutionError("svg_export_" + type(error).__name__.lower()) from error
    bundle = _read_bundle(job, store)
    export_format = bundle.export_format
    with tempfile.TemporaryDirectory(prefix="public-export-") as temporary:
        scratch = Path(temporary)
        target = scratch / f"result.{export_format}"
        if export_format == "pptx":
            _build_pptx(bundle.slides, store, target, bundle.canvas)
        else:
            _build_pdf(bundle.slides, store, target, bundle.canvas)
        delivered_mode = FIDELITY_RASTER_MODE
        evidence: dict[str, object] = {}
        fallback_reason: str | None = None
        if bundle.export_mode == EDITABLE_HYBRID_MODE:
            if not bundle.source_slides:
                raise PublicWorkerExecutionError("public_export_source_slides_required")
            try:
                outcome = await assemble_hybrid_pptx(
                    base_pptx=target,
                    html_paths=[store.verified_path(reference) for reference in bundle.source_slides],
                    scratch=scratch,
                    font_embedding=bundle.font_embedding,
                    canvas=bundle.canvas,
                    budget_seconds=job.deadline_epoch_seconds - time.time(),
                    semantic_slides=bundle.semantic_slides,
                    export_attempt_id=bundle.export_attempt_id,
                    renderer_source_digest=bundle.renderer_source_digest,
                )
            except Exception as error:  # noqa: BLE001
                # The raster deck beside this is already good. Anything the
                # assembler path can raise costs the richer output and its
                # reason, never the user's export, so nothing here may escape
                # into the worker's own failure path.
                outcome = HybridAssemblyOutcome(
                    None,
                    {},
                    f"hybrid_assembler_raised:{type(error).__name__}"[:160],
                )
            evidence = {
                "declaredGraphSlides": len(bundle.semantic_slides),
                **outcome.evidence,
            }
            fallback_reason = outcome.fallback_reason
            if outcome.output_path is not None:
                target = outcome.output_path
                delivered_mode = EDITABLE_HYBRID_MODE
        if bundle.local_fit_receipts and export_format == "pptx":
            from services.authored_local_fit_export import (
                LocalFitPageVerificationError,
                verify_local_fit_pptx,
            )

            try:
                # Prove the identity-bound original images before allowing a
                # page-level hybrid downgrade. Receipt, canvas and source
                # identity failures remain document failures.
                if delivered_mode == EDITABLE_HYBRID_MODE:
                    verify_local_fit_pptx(
                        scratch / "result.pptx",
                        bundle.local_fit_receipts,
                        mode="fidelity",
                        pngs=[store.read(reference) for reference in bundle.slides],
                        strict_quality=False,
                    )
                local_fit_evidence = verify_local_fit_pptx(
                    target,
                    bundle.local_fit_receipts,
                    mode=("hybrid" if delivered_mode == EDITABLE_HYBRID_MODE else "fidelity"),
                    pngs=(
                        [store.read(reference) for reference in bundle.slides]
                        if delivered_mode == FIDELITY_RASTER_MODE
                        else None
                    ),
                    collect_hybrid_page_failures=(delivered_mode == EDITABLE_HYBRID_MODE),
                    strict_quality=(bundle.export_mode != EDITABLE_HYBRID_MODE),
                )
            except LocalFitPageVerificationError as error:
                failed_pages = tuple(error.failed_pages)
                indexes = tuple(int(page["slideIndex"]) for page in failed_pages)
                try:
                    target = _replace_hybrid_pages_with_verified_raster(
                        hybrid=target,
                        fidelity=scratch / "result.pptx",
                        slide_indexes=indexes,
                        scratch=scratch / "local-fit-page-fallback",
                    )
                except (OSError, ValueError, zipfile.BadZipFile) as replacement_error:
                    raise PublicWorkerExecutionError(
                        "public_export_local_fit_page_fallback_failed_"
                        + _bounded_local_fit_failure_code(replacement_error)
                    ) from replacement_error
                existing_fallbacks = {
                    int(page["n"])
                    for page in (evidence.get("quality", {}) or {}).get("slideFallbacks", [])
                    if isinstance(page, Mapping) and type(page.get("n")) is int
                }
                final_fallbacks = existing_fallbacks | {index + 1 for index in indexes}
                if len(final_fallbacks) == len(bundle.slides):
                    delivered_mode = FIDELITY_RASTER_MODE
                try:
                    local_fit_evidence = verify_local_fit_pptx(
                        target,
                        bundle.local_fit_receipts,
                        mode=("fidelity" if delivered_mode == FIDELITY_RASTER_MODE else "hybrid"),
                        pngs=(
                            [store.read(reference) for reference in bundle.slides]
                            if delivered_mode == FIDELITY_RASTER_MODE
                            else None
                        ),
                        strict_quality=(bundle.export_mode != EDITABLE_HYBRID_MODE),
                    )
                except (OSError, ValueError, zipfile.BadZipFile) as retry_error:
                    raise PublicWorkerExecutionError(
                        "public_export_local_fit_page_fallback_readback_failed_"
                        + _bounded_local_fit_failure_code(retry_error)
                    ) from retry_error
                evidence["localFitFallbackPages"] = [
                    {
                        "n": index + 1,
                        "code": str(page["code"]),
                        **({"diagnostics": page["diagnostics"]} if "diagnostics" in page else {}),
                    }
                    for index, page in zip(indexes, failed_pages, strict=True)
                ]
                _record_page_fallbacks(evidence, failed_pages)
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                raise PublicWorkerExecutionError(
                    "public_export_local_fit_readback_failed_" + _bounded_local_fit_failure_code(error)
                ) from error
            evidence["localFit"] = local_fit_evidence
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
                "canvas": bundle.canvas.model_dump(mode="json", by_alias=True),
                "fallbackReason": fallback_reason,
                **evidence,
            },
        )


if __name__ == "__main__":
    raise SystemExit(main("export", execute_export))
