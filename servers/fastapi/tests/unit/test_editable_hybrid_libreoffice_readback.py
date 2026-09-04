from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import zipfile

import pytest

from services.authored_vnext.semantic.editable_hybrid_libreoffice_readback import (
    HybridReadbackError,
    inspect_hybrid_pptx,
    run_editable_hybrid_readback,
    sha256_file,
    validate_rc_seal,
)


def _digest(seed: str) -> str:
    return "sha256:" + sha256(seed.encode()).hexdigest()


def _pptx(path: Path) -> None:
    slide = """<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id='1' name='q-title'/></p:nvSpPr></p:sp><p:sp><p:nvSpPr><p:cNvPr id='2' name='q-raster'/></p:nvSpPr></p:sp></p:spTree></p:cSld></p:sld>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>")
        archive.writestr("ppt/_rels/presentation.xml.rels", "<Relationships/>")
        archive.writestr("ppt/slides/slide1.xml", slide)


def _seal(pptx: Path) -> dict[str, object]:
    return {
        "schemaVersion": "authored-editable-hybrid-libreoffice-readback-seal/v1",
        "rc": {"id": "rc-clean-001", "sealedAt": "2026-08-31T00:00:00Z", **{f"{name}Digest": _digest(name) for name in ("source", "profile", "policy", "worker", "provider", "font", "exporter")}},
        "artifact": {"sha256": sha256_file(pptx), "slideCount": 1},
        "hybrid": {"profile": "editable_hybrid", "capabilityId": "editable.hybrid.libreoffice-readback.v1", "nativeQids": ["q-title"], "rasterIslandQids": ["q-raster"], "readingOrderQids": ["q-title", "q-raster"], "fallback": {"policyDigest": _digest("policy"), "status": "required"}, "fonts": {"fontDigest": _digest("font"), "status": "verified"}, "repair": {"policyDigest": _digest("policy"), "status": "clean"}},
    }


def test_hybrid_seal_requires_same_rc_inputs_and_qid_reading_order(tmp_path: Path) -> None:
    pptx = tmp_path / "rc.pptx"
    _pptx(pptx)
    seal = _seal(pptx)
    binding = validate_rc_seal(seal, pptx)
    assert binding["nativeQids"] == ["q-title"]
    assert inspect_hybrid_pptx(pptx, expected_qids=binding["readingOrderQids"])["status"] == "passed"
    seal["hybrid"]["fonts"]["fontDigest"] = _digest("other")  # type: ignore[index]
    with pytest.raises(HybridReadbackError, match="hybrid_policy_font_repair_binding"):
        validate_rc_seal(seal, pptx)


def test_hybrid_readback_reports_environment_unavailable_without_fidelity_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pptx = tmp_path / "rc.pptx"
    control = tmp_path / "control.pptx"
    _pptx(pptx)
    _pptx(control)
    # A distinct known-good control is required even when its structure is the
    # same as this tiny test RC.
    with control.open("ab") as handle:
        handle.write(b"\n")
    monkeypatch.setattr("services.authored_vnext.semantic.editable_hybrid_libreoffice_readback._find_soffice", lambda: None)
    receipt = run_editable_hybrid_readback(pptx, control, _seal(pptx), tmp_path / "new-work")
    assert receipt["mode"] == "editable_hybrid"
    assert receipt["status"] == "unavailable"
    assert receipt["classification"] == "environment_unavailable"
    assert receipt["blockers"] == ["libreoffice.environment_unavailable"]
