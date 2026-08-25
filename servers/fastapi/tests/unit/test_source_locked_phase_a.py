from __future__ import annotations

from io import BytesIO
import zipfile

import pytest

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import GenerationMode, PageCountPolicy, SourceMediaType
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.pdf import analyze_pdf
from services.source_locked.pptx import analyze_pptx
from services.source_locked.seal import WriteOnceSourceAnalysisSealStore


def _contract(filename: str):
    return admit_source_locked_v1(
        [filename],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )


def _rels(*items: tuple[str, str, str]) -> str:
    relationships = "".join(
        f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{target}"/>'
        for rel_id, kind, target in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{relationships}</Relationships>"
    )


def _blips(*relationship_ids: str) -> str:
    return (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        + "".join(f'<a:blip r:embed="{rel_id}"/>' for rel_id in relationship_ids)
        + "</p:sld>"
    )


def _pptx_with_duplicate_occurrences() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        zf.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
            '<p:sldId id="257" r:id="rId2"/></p:sldIdLst></p:presentation>',
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels(
                ("rId1", "slide", "slides/slide1.xml"),
                ("rId2", "slide", "slides/slide2.xml"),
            ),
        )
        zf.writestr("ppt/slides/slide1.xml", _blips("rIdImg", "rIdImg"))
        zf.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            _rels(
                ("rIdImg", "image", "../media/image1.png"),
                ("rIdLayout", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ),
        )
        zf.writestr("ppt/slides/slide2.xml", _blips())
        zf.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            _rels(("rIdLayout", "slideLayout", "../slideLayouts/slideLayout1.xml")),
        )
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", _blips("rIdLayoutImg"))
        zf.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _rels(
                ("rIdLayoutImg", "image", "../media/image1.png"),
                ("rIdMaster", "slideMaster", "../slideMasters/slideMaster1.xml"),
            ),
        )
        zf.writestr("ppt/slideMasters/slideMaster1.xml", _blips("rIdMasterImg"))
        zf.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            _rels(("rIdMasterImg", "image", "/ppt/media/image1.png")),
        )
        zf.writestr("ppt/media/image1.png", b"same-image-payload")
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


@pytest.mark.parametrize("filename", ["deck.ppt", "deck.pptx", "deck.pdf", "C:\\fake\\deck.PPTX"])
def test_admission_accepts_exactly_one_supported_source(filename):
    contract = _contract(filename)
    assert contract.source_count == 1
    assert contract.source_filename.lower().startswith("deck.")


@pytest.mark.parametrize(
    ("filenames", "code"),
    [([], "SOURCE_COUNT_INVALID"), (["a.pdf", "b.pdf"], "SOURCE_COUNT_INVALID"), (["deck.pptm"], "SOURCE_PPTM_UNSUPPORTED"), (["deck.docx"], "SOURCE_TYPE_UNSUPPORTED")],
)
def test_admission_rejects_count_and_type_before_analysis(filenames, code):
    with pytest.raises(SourceLockedError) as exc:
        admit_source_locked_v1(
            filenames,
            generation_mode=GenerationMode.SOURCE_LOCKED,
            page_count_policy=PageCountPolicy.SOURCE,
            preserve_all_image_occurrences=True,
        )
    assert exc.value.detail.code == code


def test_pptx_manifest_preserves_duplicate_and_inherited_occurrences():
    manifest = analyze_pptx(_pptx_with_duplicate_occurrences())
    assert manifest.slide_count == manifest.page_count == 2
    assert manifest.media_asset_count == 1
    assert manifest.direct_occurrence_count == 2
    assert manifest.layout_occurrence_count == 2
    assert manifest.master_occurrence_count == 2
    assert manifest.image_occurrence_count == 6
    assert manifest.inherited_occurrence_count == 4
    assert manifest.effective_occurrence_count == 6
    inherited = [item for item in manifest.image_occurrences if item.scope.value != "direct"]
    assert {item.slide_number for item in inherited} == {1, 2}
    assert len({item.occurrence_id for item in inherited}) == 4
    direct = [item for item in manifest.image_occurrences if item.scope.value == "direct"]
    assert len(direct) == 2
    assert direct[0].asset_digest == direct[1].asset_digest
    assert direct[0].occurrence_id != direct[1].occurrence_id


def test_pdf_manifest_maps_one_page_to_one_slide_and_ocr_is_sidecar():
    manifest = analyze_pdf(_empty_pdf(2))
    assert manifest.page_count == manifest.slide_count == 2
    assert [page.page_number for page in manifest.page_render_bundle] == [1, 2]
    assert all(page.lossless_encoding for page in manifest.page_render_bundle)
    assert all(page.source_object_editability == "not_promised" for page in manifest.page_render_bundle)
    assert manifest.ocr_sidecar.authoritative is False


def test_digest_and_write_once_seal_fail_closed():
    data = _pptx_with_duplicate_occurrences()
    contract = _contract("deck.pptx")
    expected = idempotency_payload_digest(contract, sha256_digest(data))
    store = WriteOnceSourceAnalysisSealStore()

    first = analyze_source(
        admission_contract=contract,
        data=data,
        idempotency_key="request-1",
        expected_idempotency_payload_digest=expected,
        seal_store=store,
    )
    replay = analyze_source(
        admission_contract=contract,
        data=data,
        idempotency_key="request-1",
        expected_idempotency_payload_digest=expected,
        seal_store=store,
    )
    assert replay.analysis_seal == first.analysis_seal
    assert first.fallback_allowed is False
    assert [stage.status.value for stage in first.progress_stages] == ["completed", "pending", "pending"]

    other_data = data + b"different"
    other_digest = idempotency_payload_digest(contract, sha256_digest(other_data))
    with pytest.raises(SourceLockedError) as conflict:
        analyze_source(
            admission_contract=contract,
            data=other_data,
            idempotency_key="request-1",
            expected_idempotency_payload_digest=other_digest,
            seal_store=store,
        )
    assert conflict.value.detail.code == "IDEMPOTENCY_KEY_CONFLICT"

    with pytest.raises(SourceLockedError) as mismatch:
        analyze_source(
            admission_contract=contract,
            data=data,
            idempotency_key="request-2",
            expected_idempotency_payload_digest="sha256:" + "0" * 64,
            seal_store=store,
        )
    assert mismatch.value.detail.code == "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH"


def test_legacy_ppt_is_admitted_then_requires_analysis_conversion_without_fallback():
    contract = _contract("legacy.ppt")
    assert contract.source_media_type == SourceMediaType.PPT
    data = b"legacy-ole-placeholder"
    expected = idempotency_payload_digest(contract, sha256_digest(data))
    with pytest.raises(SourceLockedError) as exc:
        analyze_source(
            admission_contract=contract,
            data=data,
            idempotency_key="legacy-request",
            expected_idempotency_payload_digest=expected,
            seal_store=WriteOnceSourceAnalysisSealStore(),
        )
    assert exc.value.detail.code == "SOURCE_CONVERSION_REQUIRED"
    assert exc.value.detail.fallback_allowed is False
