"""Source-preserving SVG export receipt and native PPTX readback."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
import zipfile

from PIL import Image
from pptx import Presentation
import pytest

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import source_digest
from services.svg_export_operation import _source_preservation_receipts
from workers.public_svg_export_worker import _source_preservation_readback, convert_pptx


def test_source_receipt_gates_export_and_reads_back_native_values(tmp_path):
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    image_path = tmp_path / "source-image.png"
    Image.new("RGB", (8, 8), "#2962ff").save(image_path)
    image_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width_px}" '
        f'height="{canvas.height_px}" viewBox="0 0 {canvas.width_px} {canvas.height_px}">'
        '<text id="title" x="80" y="100" font-family="Arial" font-size="24">원문 제목</text>'
        '<rect x="80" y="140" width="240" height="80" fill="#ffffff" stroke="#222222"/>'
        '<text id="cell" x="95" y="190" font-family="Arial" font-size="20">42</text>'
        f'<image id="photo" x="380" y="80" width="160" height="120" href="{image_path.name}"/>'
        '</svg>'
    )
    receipt = {
        "contract_version": "svg-source-preservation/v1",
        "manifest_digest": "sha256:" + "1" * 64,
        "source_digest": "sha256:" + "2" * 64,
        "source_page_id": "source-page-1",
        "source_page_index": 0,
        "page_count": 1,
        "page_digest": "sha256:" + "3" * 64,
        "plan_digest": "sha256:" + "4" * 64,
        "svg_digest": source_digest(svg),
        "style_digest": "sha256:" + "5" * 64,
        "canvas": {"width": canvas.width_px, "height": canvas.height_px},
        "bindings": [
            {"source_id": "title-source", "kind": "text", "element_id": "title",
             "text": "원문 제목", "start": 0, "end": 5},
            {"source_id": "table-cell-0-0", "kind": "table_cell", "element_id": "cell",
             "text": "42", "start": 0, "end": 2, "block_id": "table-1", "row": 0,
             "column": 0, "row_span": 1, "column_span": 1},
            {"source_id": "image-source", "kind": "image", "element_id": "photo",
             "slot_id": image_path.name, "asset_digest": "sha256:" + image_sha256},
        ],
        "checks": {"source_ids_exact": True, "text_exact": True,
                   "table_structure_exact": True, "image_occurrences_exact": True},
        "rendering_verified": True,
    }
    slide = SimpleNamespace(svg_content=svg, content={
        "svgReceipt": {"source_preservation": receipt}
    })
    presentation = SimpleNamespace(theme={"svg": {"sourcePreservation": {
        "schemaVersion": "svg-source-preservation/v1",
        "manifestDigest": receipt["manifest_digest"],
        "pageCount": 1,
    }}})

    assert _source_preservation_receipts(presentation, [slide], canvas) == [receipt]
    missing_receipt = SimpleNamespace(svg_content=svg, content={"svgReceipt": {}})
    with pytest.raises(PublicExportOperationError, match="svg_source_export_receipt_required"):
        _source_preservation_receipts(presentation, [missing_receipt], canvas)
    wrong_digest = {**receipt, "svg_digest": "sha256:" + "0" * 64}
    wrong_digest_slide = SimpleNamespace(svg_content=svg, content={
        "svgReceipt": {"source_preservation": wrong_digest}
    })
    with pytest.raises(PublicExportOperationError, match="svg_source_export_receipt_input_mismatch"):
        _source_preservation_receipts(presentation, [wrong_digest_slide], canvas)
    unverified = {**receipt, "rendering_verified": False}
    unverified_slide = SimpleNamespace(svg_content=svg, content={
        "svgReceipt": {"source_preservation": unverified}
    })
    with pytest.raises(PublicExportOperationError, match="svg_source_export_rendering_unverified"):
        _source_preservation_receipts(presentation, [unverified_slide], canvas)

    svg_path = tmp_path / "slide-001.svg"
    svg_path.write_text(svg, encoding="utf-8")
    target = tmp_path / "source-preserving.pptx"
    readback = convert_pptx(
        [svg_path], target, [""], canvas,
        source_preservation=[receipt],
        expected_image_digests=[{image_path.name: image_sha256}],
    )["sourcePreservation"]

    assert readback["passed"] is True
    assert readback["pages"][0]["text"]["matchedBindingCount"] == 2
    assert readback["pages"][0]["tables"]["matchedCellValueCount"] == 1
    assert readback["pages"][0]["tables"]["nativeTableEditabilityVerified"] is False
    assert readback["pages"][0]["images"]["matchedOccurrenceCount"] == 1

    changed_text = Presentation(str(target))
    next(shape for shape in changed_text.slides[0].shapes
         if shape.has_text_frame and "원문 제목" in shape.text_frame.text).text = "변조된 제목"
    with pytest.raises(ValueError, match="svg_source_pptx_text_readback_mismatch"):
        _source_preservation_readback(
            changed_text, [receipt], [{image_path.name: image_sha256}]
        )

    replacement_image = tmp_path / "replacement.png"
    Image.new("RGB", (8, 8), "#ff1744").save(replacement_image)
    changed_image_target = tmp_path / "changed-image.pptx"
    with zipfile.ZipFile(target) as source_archive, zipfile.ZipFile(changed_image_target, "w") as target_archive:
        for member in source_archive.infolist():
            payload = source_archive.read(member.filename)
            if member.filename.startswith("ppt/media/"):
                payload = replacement_image.read_bytes()
            target_archive.writestr(member, payload)
    changed_image = Presentation(str(changed_image_target))
    with pytest.raises(ValueError, match="svg_source_pptx_image_readback_mismatch"):
        _source_preservation_readback(
            changed_image, [receipt], [{image_path.name: image_sha256}]
        )
