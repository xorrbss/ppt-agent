from __future__ import annotations

from io import BytesIO
import zipfile

from PIL import Image
from pptx import Presentation
from pptx.util import Inches
import pytest

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import (
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
    SourceSnapshotV1,
)
from services.source_locked.digests import sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.pdf import analyze_pdf
from services.source_locked.pptx import analyze_pptx
from services.source_locked.restoration import restore_source_locked_v1


def _contract(filename: str):
    return admit_source_locked_v1(
        [filename],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )


def _snapshot(contract, source: bytes) -> SourceSnapshotV1:
    digest = sha256_digest(source)
    return SourceSnapshotV1(
        source_filename=contract.source_filename,
        source_media_type=contract.source_media_type,
        byte_length=len(source),
        sha256=digest,
        package_sha256=digest,
    )


def _png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), color).save(output, format="PNG")
    return output.getvalue()


def _pptx() -> bytes:
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    image = _png((20, 80, 160))
    for _ in range(2):
        slide = presentation.slides.add_slide(blank)
        slide.shapes.add_picture(BytesIO(image), Inches(1), Inches(1), width=Inches(2))
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _empty_pdf(page_count: int) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{index + 3} 0 R' for index in range(page_count))}] /Count {page_count} >>".encode(),
    ]
    objects.extend(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"
        for _ in range(page_count)
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def test_pptx_restoration_is_byte_exact_and_manifest_verified() -> None:
    source = _pptx()
    contract = _contract("source.pptx")
    manifest = analyze_pptx(source)

    result = restore_source_locked_v1(
        admission_contract=contract,
        source_snapshot=_snapshot(contract, source),
        manifest=manifest,
        source_bytes=source,
    )

    assert result.artifact_bytes == source
    assert result.artifact_bytes.startswith(b"PK")
    assert result.verification.byte_exact_source_package is True
    assert result.verification.slide_count == 2
    assert result.verification.page_count == 2
    assert result.verification.direct_occurrence_count == 2
    assert result.verification.output_package_sha256 == sha256_digest(source)
    assert result.verification.fallback_allowed is False


def test_pptx_restoration_rejects_corruption_and_manifest_omission() -> None:
    source = _pptx()
    contract = _contract("source.pptx")
    snapshot = _snapshot(contract, source)
    manifest = analyze_pptx(source)

    with pytest.raises(SourceLockedError) as corrupted:
        restore_source_locked_v1(
            admission_contract=contract,
            source_snapshot=snapshot,
            manifest=manifest,
            source_bytes=source + b"corrupt",
        )
    assert corrupted.value.detail.code == "RESTORATION_SOURCE_SNAPSHOT_MISMATCH"
    assert corrupted.value.detail.stage == AnalysisStage.SLIDE_IMAGE_RESTORATION

    omitted_manifest = manifest.model_copy(update={"direct_occurrence_count": 1})
    with pytest.raises(SourceLockedError) as missing:
        restore_source_locked_v1(
            admission_contract=contract,
            source_snapshot=snapshot,
            manifest=omitted_manifest,
            source_bytes=source,
        )
    assert missing.value.detail.code == "PPTX_RESTORATION_READBACK_MISMATCH"
    assert missing.value.detail.stage == AnalysisStage.MISSING_CONTENT_VALIDATION
    assert missing.value.detail.fallback_allowed is False


def test_pdf_restoration_renders_lossless_png_per_page_and_builds_pptx() -> None:
    source = _empty_pdf(2)
    contract = _contract("source.pdf")
    manifest = analyze_pdf(source)

    result = restore_source_locked_v1(
        admission_contract=contract,
        source_snapshot=_snapshot(contract, source),
        manifest=manifest,
        source_bytes=source,
    )

    assert result.artifact_bytes.startswith(b"PK")
    assert result.verification.byte_exact_source_package is False
    assert result.verification.page_count == 2
    assert result.verification.slide_count == 2
    assert result.verification.rendered_page_count == 2
    assert result.verification.direct_occurrence_count == 2
    assert len(result.verification.authoritative_image_digests) == 2
    assert result.verification.missing_content_count == 0

    with zipfile.ZipFile(BytesIO(result.artifact_bytes)) as package:
        png_parts = [
            name for name in package.namelist() if name.startswith("ppt/media/") and name.endswith(".png")
        ]
        assert png_parts
        assert all(package.read(name).startswith(b"\x89PNG\r\n\x1a\n") for name in png_parts)
        embedded_digests = {sha256_digest(package.read(name)) for name in png_parts}
    assert embedded_digests == set(result.verification.authoritative_image_digests)

    reopened = Presentation(BytesIO(result.artifact_bytes))
    assert len(reopened.slides) == 2
    for slide in reopened.slides:
        assert len(slide.shapes) == 1
        picture = slide.shapes[0]
        assert picture.height == reopened.slide_height
        assert picture.left > 0  # Portrait page is letterboxed, never cropped.


def test_pdf_restoration_fails_closed_when_render_bundle_is_incomplete(monkeypatch) -> None:
    source = _empty_pdf(2)
    contract = _contract("source.pdf")
    manifest = analyze_pdf(source)
    monkeypatch.setattr(
        "services.source_locked.restoration._render_pdf_pages",
        lambda _source: (_png((255, 255, 255)),),
    )

    with pytest.raises(SourceLockedError) as missing:
        restore_source_locked_v1(
            admission_contract=contract,
            source_snapshot=_snapshot(contract, source),
            manifest=manifest,
            source_bytes=source,
        )

    assert missing.value.detail.code == "PDF_PAGE_RENDER_BUNDLE_INCOMPLETE"
    assert missing.value.detail.stage == AnalysisStage.MISSING_CONTENT_VALIDATION
    assert missing.value.detail.fallback_allowed is False
