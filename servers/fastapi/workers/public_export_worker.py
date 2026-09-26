"""Sessionless PNG bundle to verified PPTX/PDF export worker."""

from __future__ import annotations

import re
import time
import zipfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

from PIL import Image
import pypdfium2 as pdfium
from services.document_canvas import canvas_physical_size
from pptx import Presentation
from pptx.util import Emu

from services.authored_canvas import (
    AuthoredCanvasMetadata,
)
from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerJob,
)
from workers.public_export_hybrid import HybridAssemblyOutcome, assemble_hybrid_pptx
from workers.public_export_bundle import (
    EDITABLE_HYBRID_MODE as EDITABLE_HYBRID_MODE,
    EXPORT_BUNDLE_SCHEMA as EXPORT_BUNDLE_SCHEMA,
    EXPORT_CHECKPOINT_BUNDLE_SCHEMA as EXPORT_CHECKPOINT_BUNDLE_SCHEMA,
    FIDELITY_RASTER_MODE as FIDELITY_RASTER_MODE,
    MAX_SLIDES as MAX_SLIDES,
    ExportBundle as ExportBundle,
    _OFFICE_REL as _OFFICE_REL,
    _PREFIXED_SHA256 as _PREFIXED_SHA256,
    _REL_NS as _REL_NS,
    _SHA256 as _SHA256,
    _read_bundle as _read_bundle,
    _read_declared_graph as _read_declared_graph,
    _read_local_fit_receipts as _read_local_fit_receipts,
    _read_source_slides as _read_source_slides,
)
from workers.public_export_hybrid_raster import (
    _replace_hybrid_pages_with_verified_raster as _replace_hybrid_pages_with_verified_raster,
    _slide_parts as _slide_parts,
    _slide_rel_path as _slide_rel_path,
)
from workers.public_worker_runtime import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
    WorkerOutput,
    main,
)

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
