from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import zipfile


def _validator_module():
    script = Path(__file__).parents[2] / "scripts" / "validate_public_fidelity_readback.py"
    spec = importlib.util.spec_from_file_location("public_fidelity_readback", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_pptx(path: Path, *, full_slide: bool = True) -> str:
    image = b"\x89PNG\r\n\x1a\npublic-fidelity-raster"
    extent_x = "12192000" if full_slide else "6000000"
    parts = {
        "[Content_Types].xml": b"<Types/>",
        "_rels/.rels": b"<Relationships/>",
        "ppt/presentation.xml": (
            b"<p:presentation xmlns:p='http://schemas.openxmlformats.org/"
            b"presentationml/2006/main'><p:sldSz cx='12192000' cy='6858000'/>"
            b"</p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": b"<Relationships/>",
        "ppt/slides/slide1.xml": (
            "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
            "xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main' "
            "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
            "<p:cSld><p:spTree><p:pic><p:blipFill><a:blip r:embed='rId2'/>"
            "</p:blipFill><p:spPr><a:xfrm><a:off x='0' y='0'/>"
            f"<a:ext cx='{extent_x}' cy='6858000'/></a:xfrm></p:spPr>"
            "</p:pic></p:spTree></p:cSld></p:sld>"
        ).encode(),
        "ppt/slides/_rels/slide1.xml.rels": (
            b"<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            b"<Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/image' "
            b"Target='../media/image1.png'/></Relationships>"
        ),
        "ppt/media/image1.png": image,
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return "sha256:" + sha256(image).hexdigest()


def test_ooxml_receipt_is_append_only_and_product_binding_is_fail_closed(
    tmp_path, monkeypatch
) -> None:
    validator = _validator_module()
    pptx = tmp_path / "published-fidelity.pptx"
    _minimal_pptx(pptx)
    ooxml = validator.inspect_fidelity_pptx(pptx)
    assert ooxml["status"] == "passed"
    assert ooxml["slideCount"] == 1
    office_calls: list[str] = []
    monkeypatch.setattr(validator, "libreoffice_readback", lambda *_args: office_calls.append("libreoffice"))
    monkeypatch.setattr(validator, "powerpoint_readback", lambda *_args: office_calls.append("powerpoint"))
    receipt = validator.build_receipt(
        pptx,
        work_dir=tmp_path / "work",
        product_operation_id=None,
        product_artifact_digest=None,
    )
    assert receipt["releaseReadiness"] == "blocked"
    assert receipt["promotionReady"] is False
    assert receipt["officeReadbackStatus"] == "not_verified"
    assert "product_path_artifact_binding_missing" in receipt["blockers"]
    assert office_calls == []
    assert receipt["libreOffice"]["status"] == "not_verified"
    assert receipt["powerPoint"]["status"] == "not_verified"
    receipt_path = tmp_path / "receipts.jsonl"
    validator.append_receipt(receipt_path, receipt)
    validator.append_receipt(receipt_path, receipt)
    assert len(receipt_path.read_text(encoding="utf-8").splitlines()) == 2
    assert validator.main([str(pptx), "--receipt", str(tmp_path / "cli-receipts.jsonl")]) == 2
    assert office_calls == []


def test_office_readback_only_starts_after_matching_product_digest(tmp_path, monkeypatch) -> None:
    validator = _validator_module()
    pptx = tmp_path / "product-worker-output.pptx"
    image_digest = _minimal_pptx(pptx)
    digest = validator._sha256_file(pptx)
    inspected = validator.inspect_fidelity_pptx(pptx)
    office_calls: list[str] = []
    monkeypatch.setattr(
        validator,
        "libreoffice_readback",
        lambda *_args: office_calls.append("libreoffice")
        or {"capability": "available", "status": "passed", "blockers": []},
    )
    monkeypatch.setattr(
        validator,
        "powerpoint_readback",
        lambda *_args: office_calls.append("powerpoint")
        or {"capability": "available", "status": "passed", "blockers": []},
    )
    receipt = validator.build_receipt(
        pptx,
        work_dir=tmp_path / "work",
        product_operation_id="operation-from-local-worker",
        product_artifact_digest=digest,
        product_package_digest=inspected["packageDigest"],
        product_slide_count=inspected["slideCount"],
        product_slide_image_digests=[image_digest],
    )
    assert office_calls == ["libreoffice", "powerpoint"]
    assert receipt["productBinding"]["status"] == "bound"
    assert receipt["officeReadbackStatus"] == "passed"
    assert receipt["promotionReady"] is False


def test_readback_binds_available_package_and_slide_evidence_and_rejects_mismatch(
    tmp_path, monkeypatch
) -> None:
    validator = _validator_module()
    pptx = tmp_path / "published-fidelity.pptx"
    image_digest = _minimal_pptx(pptx)
    inspected = validator.inspect_fidelity_pptx(pptx)
    monkeypatch.setattr(
        validator,
        "libreoffice_readback",
        lambda *_args: {"capability": "missing", "status": "not_verified", "blockers": ["libreoffice.unavailable"]},
    )
    monkeypatch.setattr(
        validator,
        "powerpoint_readback",
        lambda *_args: {"capability": "missing", "status": "not_verified", "blockers": ["powerpoint.com_unavailable"]},
    )
    receipt = validator.build_receipt(
        pptx,
        work_dir=tmp_path / "work",
        product_operation_id="published-operation",
        product_artifact_digest=inspected["digest"],
        product_package_digest=inspected["packageDigest"],
        product_slide_count=inspected["slideCount"],
        product_slide_image_digests=[image_digest],
    )
    assert receipt["productBinding"]["status"] == "bound"
    assert receipt["productBinding"]["packageDigestStatus"] == "matched"
    assert receipt["productBinding"]["slideCountStatus"] == "matched"
    assert receipt["officeReadbackStatus"] == "not_verified"
    assert receipt["releaseReadiness"] == "blocked"
    assert receipt["fidelity"]["mode"] == "raster_fidelity"
    mismatch = validator.build_receipt(
        pptx,
        work_dir=tmp_path / "work-mismatch",
        product_operation_id="published-operation",
        product_artifact_digest=inspected["digest"],
        product_package_digest="sha256:not-the-package",
        product_slide_count=2,
        product_slide_image_digests=[image_digest],
    )
    assert mismatch["productBinding"]["status"] == "unverified"
    assert mismatch["productBinding"]["packageDigestStatus"] == "mismatched"
    assert mismatch["productBinding"]["slideCountStatus"] == "mismatched"
    assert "product_path_package_digest_mismatch" in mismatch["blockers"]
    assert "product_path_slide_count_mismatch" in mismatch["blockers"]
    image_mismatch = validator.build_receipt(
        pptx,
        work_dir=tmp_path / "work-image-mismatch",
        product_operation_id="published-operation",
        product_artifact_digest=inspected["digest"],
        product_package_digest=inspected["packageDigest"],
        product_slide_count=1,
        product_slide_image_digests=["sha256:" + ("0" * 64)],
    )
    assert image_mismatch["productBinding"]["status"] == "unverified"
    assert image_mismatch["productBinding"]["slideImageDigestStatus"] == "mismatched"
    assert "ooxml.slide_image_digest_mismatch" in image_mismatch["blockers"]


def test_fidelity_package_rejects_non_full_slide_raster(tmp_path) -> None:
    validator = _validator_module()
    pptx = tmp_path / "cropped-fidelity.pptx"
    image_digest = _minimal_pptx(pptx, full_slide=False)
    inspected = validator.inspect_fidelity_pptx(
        pptx,
        expected_slide_image_digests=[image_digest],
    )
    assert inspected["status"] == "failed"
    assert "ooxml.slide_image_geometry_invalid" in inspected["blockers"]
    assert inspected["rasterEvidence"]["slidesWithFullSlideImage"] == 0
