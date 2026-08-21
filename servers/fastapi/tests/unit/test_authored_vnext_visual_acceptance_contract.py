from __future__ import annotations

import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "authored-vnext-quality" / "visual_acceptance_census.py"
MANIFEST = ROOT / "scripts" / "authored-vnext-quality" / "representative_deck_manifest.json"


def _module():
    spec = importlib.util.spec_from_file_location("authored_vnext_visual_acceptance_census", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_representative_manifest_covers_required_patterns_languages_and_native_objects():
    module = _module()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    issues = module._manifest_checks(manifest, [{"path": "legacy", "slideCount": len(manifest["slides"]), "textCorpus": " ".join(item for slide in manifest["slides"] for item in slide["requiredText"])}])
    assert issues == []
    assert set(manifest["requiredNativeObjects"]) >= {"text", "shape", "image", "table", "chart"}


def test_manifest_gate_fails_closed_for_missing_required_pattern():
    module = _module()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for slide in manifest["slides"]:
        slide["patterns"] = [pattern for pattern in slide["patterns"] if pattern != "metric"]
    corpus = " ".join(item for slide in manifest["slides"] for item in slide["requiredText"])
    issues = module._manifest_checks(manifest, [{"path": "vnext", "slideCount": len(manifest["slides"]), "textCorpus": corpus}])
    assert "manifest_pattern_missing:metric" in issues


def test_render_inspection_requires_real_render_directory(tmp_path: Path):
    module = _module()
    with pytest.raises(module.CensusFailure, match="render_directory_missing"):
        module.inspect_rendered_slides(tmp_path / "does-not-exist", 1)


def test_relative_quality_rejects_render_evidence_without_digest():
    module = _module()
    render = {"slides": [{"inkRatio": 0.2, "edgeDensity": 0.1}]}

    evidence = module._relative_visual_quality(render, render, {"slides": []})

    assert evidence["verified"] is False
    assert "relative_render_digest_missing:1" in evidence["issues"]


def test_pptx_scene_identity_uses_xml_document_order_for_mixed_native_objects():
    module = _module()
    authored_prefix = "authored-vnext:"
    descriptions = [
        '{"elementId":"element:one","kind":"shape"}',
        '{"elementId":"element:two","kind":"image"}',
        '{"elementId":"element:three","kind":"shape"}',
    ]
    nodes = []
    for index, description in enumerate(descriptions, start=1):
        escaped = description.replace('"', '&quot;')
        if index == 2:
            nodes.append(
                f'<p:pic><p:nvPicPr><p:cNvPr id="{index}" descr="{authored_prefix}{escaped}"/><p:cNvPicPr/><p:nvPrPr/></p:nvPicPr>'
                f'<p:blipFill/><p:spPr><a:xfrm><a:off x="{index}" y="1"/><a:ext cx="10" cy="10"/></a:xfrm></p:spPr></p:pic>'
            )
        else:
            nodes.append(
                f'<p:sp><p:nvSpPr><p:cNvPr id="{index}" descr="{authored_prefix}{escaped}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="{index}" y="1"/><a:ext cx="10" cy="10"/></a:xfrm></p:spPr></p:sp>'
            )
    xml = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree>' + "".join(nodes) + '</p:spTree></p:cSld></p:sld>'
    ).encode("utf-8")
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", xml)
    with ZipFile(BytesIO(buffer.getvalue()), "r") as archive:
        slide = module._parse_slide(archive, "ppt/slides/slide1.xml", 1, (100, 100))

    assert [item["elementId"] for item in slide["sceneIdentityElements"]] == [
        "element:one",
        "element:two",
        "element:three",
    ]
