"""Physical output readback through the integrity-checked SVG worker boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image
import pytest

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import document_canvas_for_orientation
from workers.public_svg_export_worker import VENDOR_ROOT, convert_pptx, converter_digest

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


def _slide(path, canvas, *, a4):
    width, height = canvas.width_px, canvas.height_px
    Image.new("RGB", (2, 2), "blue").save(path.parent / "asset.png")
    font = "13.405555555555556" if a4 else "16"
    stroke = "1.411111111111111" if a4 else "1"
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect x="60" y="60" width="100" height="100" fill="#ff0000" stroke="#000000" stroke-width="{stroke}"/>'
        f'<g transform="translate(20 30)"><g transform="translate(10 10)">'
        f'<rect x="{width - 190}" y="{height - 200}" width="100" height="100" fill="#00ff00"/></g></g>'
        f'<text x="60" y="240" font-family="Arial" font-size="{font}" fill="#000000">Native text</text>'
        '<image x="180" y="60" width="100" height="100" href="asset.png"/>'
        "</svg>",
        encoding="utf-8",
    )


def test_worker_native_page_elements_and_legacy_return_sequence(tmp_path):
    # Expectations are constants, deliberately independent of production helpers.
    fixtures = [
        (canvas_metadata_for_aspect_ratio("16:9"), False, 12192000, 6858000, 571500, 952500, 10668000, 5334000),
        (document_canvas_for_orientation("landscape"), True, 10692000, 7560000, 540000, 900000, 9252000, 6120000),
        (document_canvas_for_orientation("portrait"), True, 7560000, 10692000, 540000, 900000, 6120000, 9252000),
        (canvas_metadata_for_aspect_ratio("9:16"), False, 6858000, 12192000, 571500, 952500, 5334000, 10668000),
        (canvas_metadata_for_aspect_ratio("9:13"), False, 6858000, 9906000, 571500, 952500, 5334000, 8382000),
        (canvas_metadata_for_aspect_ratio("16:9"), False, 12192000, 6858000, 571500, 952500, 10668000, 5334000),
    ]
    for index, (canvas, a4, width, height, origin, length, far_x, far_y) in enumerate(fixtures):
        source, target = tmp_path / f"slide-{index}.svg", tmp_path / f"deck-{index}.pptx"
        _slide(source, canvas, a4=a4)
        evidence = convert_pptx([source], target, ["Physical fixture"], canvas)
        assert evidence["nativeTextRuns"] > 0
        with zipfile.ZipFile(target) as archive:
            presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
            size = presentation.find("p:sldSz", NS)
            assert (int(size.attrib["cx"]), int(size.attrib["cy"])) == (width, height)
            slide = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
        boxes = []
        for transform in slide.findall(".//a:xfrm", NS):
            off, ext = transform.find("a:off", NS), transform.find("a:ext", NS)
            if off is not None and ext is not None:
                boxes.append(
                    tuple(int(v) for v in (off.attrib["x"], off.attrib["y"], ext.attrib["cx"], ext.attrib["cy"]))
                )
        assert (origin, origin, length, length) in boxes
        assert (far_x, far_y, length, length) in boxes
        picture = slide.find(".//p:pic/p:spPr/a:xfrm", NS)
        assert picture is not None
        assert int(picture.find("a:ext", NS).attrib["cx"]) == length
        assert int(picture.find("a:off", NS).attrib["x"]) == (1620000 if a4 else 1714500)
        text_runs = slide.findall(".//a:r", NS)
        assert any(
            run.find("a:t", NS).text == "Native text"
            and int(run.find("a:rPr", NS).attrib["sz"]) == (950 if a4 else 1200)
            for run in text_runs
        )
        widths = [int(line.attrib["w"]) for line in slide.findall(".//a:ln", NS) if "w" in line.attrib]
        assert (12700 if a4 else 9525) in widths

    # Exercise the production converter and its sealed OOXML readback, with
    # one-line and wrapped titles sharing a frozen header and native text.
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.svg_common_header import prepare_common_header
    from services.svg_contract import finalize_svg
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="운영 현황", title="운영 현황"),
        SlideOutlineModel(content="승인 조건", title="승인 전 수정본은 공개하지 않고 게시본을 유지한다"),
    ])
    contract, bindings = prepare_common_header(
        {"tokens": {}, "common_header": {"title_width_px": 470}}, outline, canvas)
    sources, paths = [], []
    for index, binding in enumerate(bindings):
        source = finalize_svg('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect x="64" y="400" width="200" height="60" fill="#2563EB"/></svg>',
            index=index, n=2, style_snapshot={"common_header": contract}, header_page=binding)
        path = tmp_path / f"uniform-{index}.svg"
        path.write_text(source, encoding="utf-8")
        sources.append(source)
        paths.append(path)
    target = tmp_path / "uniform-editable.pptx"
    result = convert_pptx(paths, target, ["", ""], canvas, native_coverage_sources=sources)
    readback = result["genericNativeCoverageOoxml"]["commonHeaderReadback"]
    assert readback["passed"] is True
    assert [len(page["lines"]) for page in readback["pages"]] == [1, 2]
    with zipfile.ZipFile(target) as archive:
        for index, binding in enumerate(bindings):
            slide = ET.fromstring(archive.read(f"ppt/slides/slide{index + 1}.xml"))
            title_shapes = [shape for shape in slide.findall(".//p:sp", NS)
                if "".join(node.text or "" for node in shape.findall(".//a:t", NS)) in binding["lines"]]
            assert len(title_shapes) == len(binding["lines"])
            for shape in title_shapes:
                body_properties = shape.find("p:txBody/a:bodyPr", NS)
                assert body_properties.find("a:noAutofit", NS) is not None
                assert body_properties.find("a:spAutoFit", NS) is None


def test_context_restored_after_failure_and_parallel_rejected():
    converter_digest()
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        from svg_to_pptx.conversion_context import ConversionContext, current_conversion_context
        from svg_to_pptx.drawingml.utils import px_to_emu

        context = ConversionContext(1188, 840, 10691813, 7559675, legacy=False)
        assert context.width_emu == 10691813 and context.height_emu == 7559675
        with pytest.raises(ValueError, match="nonuniform"):
            ConversionContext(1188, 840, 10691813, 7550000, legacy=False)
        with pytest.raises(ValueError, match="SVG-image PPTX"):
            create_pptx_with_native_svg(
                [], Path("unused.pptx"), use_native_shapes=False, pptx_structure="flat", conversion_context=context
            )
        assert current_conversion_context().legacy and px_to_emu(100) == 952500
        with pytest.raises(ValueError, match="parallel_unsupported"):
            create_pptx_with_native_svg(
                [], Path("unused.pptx"), workers=2, pptx_structure="flat", conversion_context=context
            )
        assert px_to_emu(100) == 952500
    finally:
        sys.path.remove(str(VENDOR_ROOT))


def test_converter_patch_rebuild_matches_shipped_bytes(tmp_path):
    root = Path(__file__).resolve().parents[4]
    patch_root = root / "compatibility/slide-master"
    baseline = tmp_path / "baseline"
    shutil.copytree(VENDOR_ROOT, baseline, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    spec = importlib.util.spec_from_file_location("rebuild_slide_master", patch_root / "rebuild.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for patch, _suffix, _note in reversed(module.PATCHES):
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
             "apply", "--no-index", "--reverse", "-p5", str(patch)],
            cwd=baseline,
            check=True,
        )
    output = tmp_path / "rebuilt"
    module.rebuild(output, source=baseline)
    assert (output / "MANIFEST.json").read_bytes() == (VENDOR_ROOT / "MANIFEST.json").read_bytes()
    manifest = json.loads((output / "MANIFEST.json").read_text())
    for name in manifest["files"]:
        assert (output / name).read_bytes() == (VENDOR_ROOT / name).read_bytes()
