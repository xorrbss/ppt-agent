from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

import pypdfium2
from pptx import Presentation
from pptx.util import Inches

from services.source_locked.contracts import (
    AdmissionContractV1,
    AnalysisStage,
    GenerationMode,
    ImageOccurrenceScope,
    PdfSourceManifestV1,
    PptSourceManifestV1,
    PptxSourceManifestV1,
    SourceManifestV1,
    SourceMediaType,
    SourceSnapshotV1,
)
from services.source_locked.conversion import (
    convert_legacy_ppt_v1,
    ppt_conversion_readback_projection,
)
from services.source_locked.digests import sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.pdf import analyze_pdf
from services.source_locked.pptx import analyze_pptx

_PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_PDF_RENDER_SCALE = 2


@dataclass(frozen=True, slots=True)
class SourceLockedRestorationVerificationV1:
    source_type: Literal["ppt", "pptx", "pdf"]
    source_snapshot_sha256: str
    output_package_sha256: str
    page_count: int
    slide_count: int
    direct_occurrence_count: int
    rendered_page_count: int
    authoritative_image_digests: tuple[str, ...]
    missing_content_count: Literal[0]
    byte_exact_source_package: bool
    fallback_allowed: Literal[False] = False


@dataclass(frozen=True, slots=True)
class SourceLockedRestorationResultV1:
    artifact_bytes: bytes
    verification: SourceLockedRestorationVerificationV1
    contract_version: Literal["source-locked-restoration-result/v1"] = (
        "source-locked-restoration-result/v1"
    )
    artifact_media_type: Literal[
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ] = _PPTX_MEDIA_TYPE


def _raise(code: str, stage: AnalysisStage, message: str) -> None:
    raise SourceLockedError(code, stage, message)


def _validate_inputs(
    *,
    admission_contract: AdmissionContractV1,
    source_snapshot: SourceSnapshotV1,
    manifest: SourceManifestV1,
    source_bytes: bytes,
) -> str:
    if admission_contract.generation_mode != GenerationMode.SOURCE_LOCKED:
        _raise(
            "RESTORATION_GENERATION_MODE_INVALID",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "Source restoration requires generation_mode=source_locked",
        )
    if not source_bytes:
        _raise(
            "RESTORATION_SOURCE_EMPTY",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "The admitted source snapshot is empty",
        )

    source_digest = sha256_digest(source_bytes)
    if (
        source_snapshot.sha256 != source_digest
        or source_snapshot.package_sha256 != source_digest
        or source_snapshot.byte_length != len(source_bytes)
    ):
        _raise(
            "RESTORATION_SOURCE_SNAPSHOT_MISMATCH",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "The restoration source does not match the admitted source snapshot",
        )
    if (
        source_snapshot.source_filename != admission_contract.source_filename
        or source_snapshot.source_media_type != admission_contract.source_media_type
    ):
        _raise(
            "RESTORATION_ADMISSION_MISMATCH",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "The source snapshot does not match its admission contract",
        )
    if manifest.package_sha256 != source_digest:
        _raise(
            "RESTORATION_MANIFEST_SOURCE_MISMATCH",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "The source manifest is not bound to the admitted source package",
        )

    expected_types = {
        SourceMediaType.PPT: "ppt",
        SourceMediaType.PPTX: "pptx",
        SourceMediaType.PDF: "pdf",
    }
    expected_type = expected_types.get(admission_contract.source_media_type)
    if expected_type is None:
        _raise(
            "RESTORATION_SOURCE_TYPE_UNSUPPORTED",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "Source restoration v1 supports admitted .ppt, .pptx, and .pdf sources only",
        )
    if manifest.source_type != expected_type:
        _raise(
            "RESTORATION_MANIFEST_TYPE_MISMATCH",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "The source manifest type does not match its admission contract",
        )
    return source_digest


def _manifest_payload(manifest: SourceManifestV1) -> dict[str, object]:
    return manifest.model_dump(mode="json")


def _sealed_ppt_readback_projection(manifest: PptSourceManifestV1) -> dict[str, object]:
    converted_manifest = manifest.converted_manifest.model_dump(mode="json")
    converted_manifest.pop("package_sha256", None)
    return {
        "contract_version": "source-locked-ppt-conversion-readback/v1",
        "original_ppt_sha256": manifest.original_package_sha256,
        "converted_manifest": converted_manifest,
        "source_page_count": manifest.source_page_count,
        "converted_page_count": manifest.converted_page_count,
        "source_visual_page_digests": manifest.source_visual_page_digests,
        "converted_visual_page_digests": manifest.converted_visual_page_digests,
        "missing_content_count": manifest.missing_content_count,
        "visual_readback_exact": manifest.visual_readback_exact,
        "isolated_libreoffice_profile": manifest.isolated_libreoffice_profile,
        "power_point_com_used": manifest.power_point_com_used,
        "byte_exact_source_package": False,
        "fallback_allowed": False,
    }


def _restore_ppt(
    *,
    source_digest: str,
    manifest: PptSourceManifestV1,
    source_bytes: bytes,
) -> SourceLockedRestorationResultV1:
    conversion = convert_legacy_ppt_v1(source_bytes)
    if conversion.original_ppt_sha256 != source_digest:
        _raise(
            "PPT_CONVERSION_SOURCE_MISMATCH",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The legacy PPT conversion is not bound to the admitted source snapshot",
        )
    if ppt_conversion_readback_projection(conversion) != _sealed_ppt_readback_projection(manifest):
        _raise(
            "PPT_CONVERSION_READBACK_MISMATCH",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The legacy PPT conversion does not match its sealed structural and visual readback",
        )

    converted = conversion.converted_manifest
    occurrence_digests = tuple(item.asset_digest for item in converted.image_occurrences)
    return SourceLockedRestorationResultV1(
        artifact_bytes=conversion.converted_pptx_bytes,
        verification=SourceLockedRestorationVerificationV1(
            source_type="ppt",
            source_snapshot_sha256=source_digest,
            output_package_sha256=conversion.converted_pptx_sha256,
            page_count=conversion.source_page_count,
            slide_count=conversion.converted_page_count,
            direct_occurrence_count=converted.direct_occurrence_count,
            rendered_page_count=0,
            authoritative_image_digests=occurrence_digests,
            missing_content_count=0,
            byte_exact_source_package=False,
        ),
    )


def _restore_pptx(
    *,
    source_digest: str,
    manifest: PptxSourceManifestV1,
    source_bytes: bytes,
) -> SourceLockedRestorationResultV1:
    # A source-locked PPTX is already the highest-fidelity editable package. Copying
    # the admitted bytes avoids a lossy deserialize/save cycle.
    artifact_bytes = bytes(source_bytes)
    if artifact_bytes != source_bytes:
        _raise(
            "PPTX_BYTE_EXACT_RESTORATION_FAILED",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The restored PPTX package is not byte-exact",
        )

    readback = analyze_pptx(artifact_bytes)
    if _manifest_payload(readback) != _manifest_payload(manifest):
        _raise(
            "PPTX_RESTORATION_READBACK_MISMATCH",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The restored PPTX does not match the sealed slide and image manifest",
        )
    output_digest = sha256_digest(artifact_bytes)
    if output_digest != source_digest or readback.package_sha256 != output_digest:
        _raise(
            "PPTX_RESTORATION_PACKAGE_DIGEST_MISMATCH",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The restored PPTX package digest changed during restoration",
        )

    occurrence_digests = tuple(item.asset_digest for item in readback.image_occurrences)
    return SourceLockedRestorationResultV1(
        artifact_bytes=artifact_bytes,
        verification=SourceLockedRestorationVerificationV1(
            source_type="pptx",
            source_snapshot_sha256=source_digest,
            output_package_sha256=output_digest,
            page_count=readback.page_count,
            slide_count=readback.slide_count,
            direct_occurrence_count=readback.direct_occurrence_count,
            rendered_page_count=0,
            authoritative_image_digests=occurrence_digests,
            missing_content_count=0,
            byte_exact_source_package=True,
        ),
    )


def _render_pdf_pages(source_bytes: bytes) -> tuple[bytes, ...]:
    rendered: list[bytes] = []
    try:
        document = pypdfium2.PdfDocument(source_bytes)
        try:
            for page_index in range(len(document)):
                page = document[page_index]
                try:
                    bitmap = page.render(scale=_PDF_RENDER_SCALE)
                    try:
                        image = bitmap.to_pil()
                        output = BytesIO()
                        image.save(output, format="PNG", optimize=False, compress_level=9)
                        payload = output.getvalue()
                        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise ValueError("PDF renderer did not produce a PNG payload")
                        rendered.append(payload)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            document.close()
    except SourceLockedError:
        raise
    except Exception as exc:
        raise SourceLockedError(
            "PDF_PAGE_RENDER_FAILED",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "The PDF could not be rendered into its authoritative lossless PNG bundle",
        ) from exc
    return tuple(rendered)


def _add_contained_picture(slide, image_bytes: bytes, slide_width: int, slide_height: int) -> None:
    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as image:
        image_width, image_height = image.size
    if image_width <= 0 or image_height <= 0:
        _raise(
            "PDF_PAGE_RENDER_DIMENSIONS_INVALID",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "A rendered PDF page has invalid image dimensions",
        )

    image_ratio = image_width / image_height
    slide_ratio = slide_width / slide_height
    if image_ratio >= slide_ratio:
        width = slide_width
        height = round(slide_width / image_ratio)
        left = 0
        top = (slide_height - height) // 2
    else:
        height = slide_height
        width = round(slide_height * image_ratio)
        left = (slide_width - width) // 2
        top = 0
    slide.shapes.add_picture(BytesIO(image_bytes), left, top, width=width, height=height)


def _build_pdf_pptx(rendered_pages: tuple[bytes, ...]) -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333333)
    presentation.slide_height = Inches(7.5)
    blank_layout = presentation.slide_layouts[6]
    for page_png in rendered_pages:
        slide = presentation.slides.add_slide(blank_layout)
        _add_contained_picture(
            slide,
            page_png,
            presentation.slide_width,
            presentation.slide_height,
        )
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _restore_pdf(
    *,
    source_digest: str,
    manifest: PdfSourceManifestV1,
    source_bytes: bytes,
) -> SourceLockedRestorationResultV1:
    source_readback = analyze_pdf(source_bytes)
    if _manifest_payload(source_readback) != _manifest_payload(manifest):
        _raise(
            "PDF_SOURCE_MANIFEST_READBACK_MISMATCH",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The PDF source no longer matches its sealed page manifest",
        )

    rendered_pages = _render_pdf_pages(source_bytes)
    if len(rendered_pages) != manifest.page_count:
        _raise(
            "PDF_PAGE_RENDER_BUNDLE_INCOMPLETE",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The authoritative PDF page render bundle is incomplete",
        )
    render_digests = tuple(sha256_digest(page) for page in rendered_pages)
    artifact_bytes = _build_pdf_pptx(rendered_pages)
    readback = analyze_pptx(artifact_bytes)

    direct = tuple(
        occurrence
        for occurrence in readback.image_occurrences
        if occurrence.scope == ImageOccurrenceScope.DIRECT
    )
    direct_digests = tuple(occurrence.asset_digest for occurrence in direct)
    direct_slides = tuple(occurrence.slide_number for occurrence in direct)
    expected_slides = tuple(range(1, manifest.page_count + 1))
    media_parts_are_png = all(occurrence.asset_part.lower().endswith(".png") for occurrence in direct)
    if (
        readback.slide_count != manifest.slide_count
        or readback.page_count != manifest.page_count
        or readback.direct_occurrence_count != manifest.page_count
        or readback.inherited_occurrence_count != 0
        or readback.effective_occurrence_count != manifest.page_count
        or direct_slides != expected_slides
        or direct_digests != render_digests
        or not media_parts_are_png
        or readback.missing_content_count != 0
    ):
        _raise(
            "PDF_RESTORATION_READBACK_MISMATCH",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The generated PPTX does not contain exactly one verified PNG occurrence per PDF page",
        )

    return SourceLockedRestorationResultV1(
        artifact_bytes=artifact_bytes,
        verification=SourceLockedRestorationVerificationV1(
            source_type="pdf",
            source_snapshot_sha256=source_digest,
            output_package_sha256=sha256_digest(artifact_bytes),
            page_count=readback.page_count,
            slide_count=readback.slide_count,
            direct_occurrence_count=readback.direct_occurrence_count,
            rendered_page_count=len(rendered_pages),
            authoritative_image_digests=render_digests,
            missing_content_count=0,
            byte_exact_source_package=False,
        ),
    )


def restore_source_locked_v1(
    *,
    admission_contract: AdmissionContractV1,
    source_snapshot: SourceSnapshotV1,
    manifest: SourceManifestV1,
    source_bytes: bytes,
) -> SourceLockedRestorationResultV1:
    """Restore one admitted source without falling back to adaptive generation."""

    source_digest = _validate_inputs(
        admission_contract=admission_contract,
        source_snapshot=source_snapshot,
        manifest=manifest,
        source_bytes=source_bytes,
    )
    if isinstance(manifest, PptSourceManifestV1):
        return _restore_ppt(
            source_digest=source_digest,
            manifest=manifest,
            source_bytes=source_bytes,
        )
    if isinstance(manifest, PptxSourceManifestV1):
        return _restore_pptx(
            source_digest=source_digest,
            manifest=manifest,
            source_bytes=source_bytes,
        )
    if isinstance(manifest, PdfSourceManifestV1):
        return _restore_pdf(
            source_digest=source_digest,
            manifest=manifest,
            source_bytes=source_bytes,
        )
    _raise(
        "RESTORATION_MANIFEST_UNSUPPORTED",
        AnalysisStage.SLIDE_IMAGE_RESTORATION,
        "Unsupported source-locked restoration manifest",
    )
