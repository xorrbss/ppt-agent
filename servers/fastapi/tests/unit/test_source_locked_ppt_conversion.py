from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

from pptx import Presentation
import pytest

from services.source_locked.conversion import (
    DEFAULT_SOFFICE_PATH,
    _run_libreoffice_conversion,
    convert_legacy_ppt_v1,
)
from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    GenerationMode,
    PageCountPolicy,
    PptSourceManifestV1,
    SourceMediaType,
)
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.pptx import analyze_pptx
from services.source_locked.restoration import restore_source_locked_v1
from services.source_locked.seal import WriteOnceSourceAnalysisSealStore


def _synthetic_pptx() -> bytes:
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _pptx_with_unverifiable_relationship(*, external: bool) -> bytes:
    relationship_attributes = (
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        + ('Target="https://example.invalid/image.png" TargetMode="External"' if external else 'Target="../media/missing.wav"')
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        package.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            'Target="slides/slide1.xml"/></Relationships>',
        )
        package.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        package.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rIdMedia" {relationship_attributes}/></Relationships>',
        )
    return output.getvalue()


@pytest.mark.skipif(not DEFAULT_SOFFICE_PATH.is_file(), reason="LibreOffice is not installed")
def test_synthetic_ppt_analysis_and_restoration_preserve_original_conversion_authority(
    tmp_path: Path,
) -> None:
    source_pptx = tmp_path / "synthetic.pptx"
    source_pptx.write_bytes(_synthetic_pptx())
    legacy_path = _run_libreoffice_conversion(
        soffice_path=DEFAULT_SOFFICE_PATH,
        input_path=source_pptx,
        output_directory=tmp_path / "legacy-output",
        output_filter="ppt:MS PowerPoint 97",
        profile_directory=tmp_path / "legacy-profile",
        timeout_seconds=180,
    )

    source = legacy_path.read_bytes()
    contract = admit_source_locked_v1(
        ["synthetic.ppt"],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )
    expected_digest = idempotency_payload_digest(contract, sha256_digest(source))
    analysis = analyze_source(
        admission_contract=contract,
        data=source,
        idempotency_key="synthetic-ppt-conversion",
        expected_idempotency_payload_digest=expected_digest,
        seal_store=WriteOnceSourceAnalysisSealStore(),
    )
    assert analysis.source_snapshot.source_media_type == SourceMediaType.PPT
    assert analysis.source_snapshot.sha256 == sha256_digest(source)
    assert isinstance(analysis.manifest, PptSourceManifestV1)
    assert analysis.manifest.original_package_sha256 == analysis.source_snapshot.sha256

    result = restore_source_locked_v1(
        admission_contract=contract,
        source_snapshot=analysis.source_snapshot,
        manifest=analysis.manifest,
        source_bytes=source,
    )

    assert result.artifact_bytes.startswith(b"PK")
    assert result.verification.source_type == "ppt"
    assert result.verification.page_count == result.verification.slide_count == 1
    assert result.verification.source_snapshot_sha256 == sha256_digest(source)
    assert result.verification.output_package_sha256 == sha256_digest(result.artifact_bytes)
    assert result.verification.missing_content_count == 0
    assert result.verification.byte_exact_source_package is False
    assert analyze_pptx(result.artifact_bytes).slide_count == 1

    with pytest.raises(SourceLockedError) as invalid:
        convert_legacy_ppt_v1(b"not-a-compound-ppt")
    assert invalid.value.detail.code == "SOURCE_CONVERSION_REQUIRED"


@pytest.mark.parametrize(
    ("external", "code"),
    [
        (False, "PPTX_RELATIONSHIP_TARGET_MISSING"),
        (True, "PPTX_EXTERNAL_CONTENT_UNVERIFIABLE"),
    ],
)
def test_pptx_analysis_rejects_missing_or_external_content_relationships(
    external: bool,
    code: str,
) -> None:
    with pytest.raises(SourceLockedError) as rejected:
        analyze_pptx(_pptx_with_unverifiable_relationship(external=external))
    assert rejected.value.detail.code == code
