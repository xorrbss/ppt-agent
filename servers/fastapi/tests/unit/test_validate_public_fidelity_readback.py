from __future__ import annotations

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


def _minimal_pptx(path: Path) -> None:
    parts = {
        "[Content_Types].xml": b"<Types/>",
        "_rels/.rels": b"<Relationships/>",
        "ppt/presentation.xml": b"<p:presentation xmlns:p='p'/>",
        "ppt/_rels/presentation.xml.rels": b"<Relationships/>",
        "ppt/slides/slide1.xml": b"<p:sld xmlns:p='p'/>",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)


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
    assert receipt["officeReadbackStatus"] == "blocked"
    assert "product_path_artifact_binding_missing" in receipt["blockers"]
    assert office_calls == []
    assert receipt["libreOffice"]["status"] == "not_run"
    assert receipt["powerPoint"]["status"] == "not_run"
    receipt_path = tmp_path / "receipts.jsonl"
    validator.append_receipt(receipt_path, receipt)
    validator.append_receipt(receipt_path, receipt)
    assert len(receipt_path.read_text(encoding="utf-8").splitlines()) == 2
    assert validator.main([str(pptx), "--receipt", str(tmp_path / "cli-receipts.jsonl")]) == 2
    assert office_calls == []


def test_office_readback_only_starts_after_matching_product_digest(tmp_path, monkeypatch) -> None:
    validator = _validator_module()
    pptx = tmp_path / "product-worker-output.pptx"
    _minimal_pptx(pptx)
    digest = validator._sha256_file(pptx)
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
    )
    assert office_calls == ["libreoffice", "powerpoint"]
    assert receipt["productBinding"]["status"] == "bound"
    assert receipt["officeReadbackStatus"] == "passed"
    assert receipt["promotionReady"] is False
