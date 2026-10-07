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


def test_source_native_table_readback_binds_grid_merges_blanks_and_values():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    shape = slide.shapes.add_table(2, 2, 0, 0, deck.slide_width, deck.slide_height)
    shape.name = "source-native-table-fixture"
    shape.table.cell(0, 0).merge(shape.table.cell(0, 1))
    shape.table.cell(0, 0).text = "Two lines"
    shape.table.cell(1, 0).text = "42"
    bindings = []
    for source_id, row, column, span, fragments in (
        ("heading", 0, 0, 2, ["Two ", "lines"]),
        ("value", 1, 0, 1, ["42"]),
        ("blank", 1, 1, 1, [""]),
    ):
        cursor = 0
        for fragment in fragments:
            bindings.append({"source_id": source_id, "kind": "table_cell", "block_id": "table-1",
                "text": fragment, "start": cursor, "end": cursor + len(fragment),
                "row": row, "column": column, "row_span": 1, "column_span": span})
            cursor += len(fragment)
    receipt = {"bindings": bindings, "source_page_id": "page-1", "svg_digest": "fixture",
        "source_digest": "source", "manifest_digest": "manifest", "canvas": {"width": 1280, "height": 720},
        "native_tables": [{"block_id": "table-1", "element_id": shape.name,
            "frame": {"x": 0, "y": 0, "width": 1280, "height": 720}}]}
    result = _source_preservation_readback(deck, [receipt], [{}])
    table_evidence = result["pages"][0]["tables"]
    assert table_evidence["nativeTableEditabilityVerified"] is True
    assert len(table_evidence["verifiedNativeTables"][0]["cells"]) == 3
    shape.table.cell(1, 1).text = "unexpected"
    with pytest.raises(ValueError, match="svg_source_pptx_table_cell_mismatch"):
        _source_preservation_readback(deck, [receipt], [{}])
    shape.table.cell(1, 1).text = ""
    shape.name = "unbound-table"
    with pytest.raises(ValueError, match="svg_source_pptx_table_binding_mismatch"):
        _source_preservation_readback(deck, [receipt], [{}])


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



def test_attested_master_furniture_is_exactly_read_back_without_weakening_source(tmp_path):
    import base64
    import copy
    import io
    from workers.public_svg_export_worker import _validate_source_preservation_entry
    from services.svg_master_furniture import attested_source_furniture

    canvas = canvas_metadata_for_aspect_ratio("16:9")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "#123456").save(buffer, format="PNG")
    logo = buffer.getvalue()
    data = "data:image/png;base64," + base64.b64encode(logo).decode()
    image_digest = hashlib.sha256(logo).hexdigest()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
           '<text id="source-title" x="80" y="100" font-family="Arial" font-size="24" fill="#111111" font-weight="400">원문 제목</text>'
           '<g id="svg-system-master-furniture">'
           '<text id="svg-system-master-brand" x="80" y="650" font-family="Arial" font-size="18" fill="#111111" font-weight="400">Brand</text>'
           f'<image id="svg-system-master-logo" x="1000" y="620" width="40" height="40" href="{data}" preserveAspectRatio="xMidYMid meet"/>'
           '</g></svg>')
    receipt = {
        "contract_version": "svg-source-preservation/v1", "source_page_index": 0, "page_count": 1,
        "svg_digest": source_digest(svg), "canvas": {"width": 1280, "height": 720},
        "rendering_verified": True, "manifest_digest": "sha256:" + "1" * 64,
        "source_digest": "sha256:" + "2" * 64, "source_page_id": "page-1",
        "page_digest": "sha256:" + "3" * 64, "plan_digest": "sha256:" + "4" * 64,
        "style_digest": "sha256:" + "5" * 64,
        "checks": dict(source_ids_exact=True, text_exact=True, table_structure_exact=True, image_occurrences_exact=True),
        "bindings": [{"kind":"text","element_id":"source-title","source_id":"title-1","text":"원문 제목"}],
        "system_furniture": {"digest":"sha256:" + "6" * 64,
            "element_ids":["svg-system-master-furniture","svg-system-master-brand","svg-system-master-logo"],
            "images":{"svg-system-master-logo":"sha256:" + image_digest}},
    }
    attested = _validate_source_preservation_entry(svg, receipt, index=0, page_count=1, canvas=canvas)
    path = tmp_path / "brand.svg"
    path.write_text(svg, encoding="utf-8")
    target = tmp_path / "brand.pptx"
    evidence = convert_pptx([path], target, [""], canvas,
                            source_preservation=[attested], expected_image_digests=[{}])["sourcePreservation"]
    assert evidence["pages"][0]["text"]["matchedBindingCount"] == 1
    assert evidence["pages"][0]["systemFurniture"] == {
        "digest":receipt["system_furniture"]["digest"], "textShapeCount":1,"imageShapeCount":1,"valuesVerified":True,
    }
    deck = Presentation(target)
    next(shape for shape in deck.slides[0].shapes if shape.has_text_frame and shape.text == "원문 제목").text = "Brand"
    with pytest.raises(ValueError, match="svg_source_pptx_text_readback_mismatch"):
        _source_preservation_readback(deck, [attested], [{}])
    corrupt = copy.deepcopy(receipt)
    corrupt["system_furniture"]["element_ids"].append("source-title")
    with pytest.raises(ValueError, match="svg_source_furniture_receipt_mismatch"):
        attested_source_furniture(svg, corrupt)
    changed = svg.replace("Brand", "Forged")
    with pytest.raises(ValueError, match="svg_source_furniture_receipt_mismatch"):
        attested_source_furniture(changed, receipt)


def test_free_svg_source_export_keeps_template_and_application_identity():
    from services.svg_export_quality import TEMPLATE_CHECKS
    from services.svg_export_source_preservation import source_preservation_receipts

    canvas = canvas_metadata_for_aspect_ratio("16:9")
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text id="source-title">source</text></svg>'
    digest = source_digest(svg)
    style_digest, template_digest = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    receipt = dict(contract_version="svg-source-preservation/v1", manifest_digest="manifest",
        source_digest="source", source_page_id="page", source_page_index=0, page_count=1,
        page_digest="page", plan_digest="plan", svg_digest=digest, style_digest=style_digest,
        canvas={"width":1280,"height":720}, rendering_verified=True,
        checks=dict(source_ids_exact=True,text_exact=True,table_structure_exact=True,image_occurrences_exact=True),
        bindings=[dict(kind="text",source_id="title",element_id="source-title",text="source")],
        layout_schema_version="svg-source-layout/v2", layout_id="free-svg", template_digest=template_digest,
        template_application=dict(template_digest=template_digest,layout_id="free-svg",composition_mode="free-svg",
                                  passed=True,checks={key:True for key in TEMPLATE_CHECKS}))
    slide = SimpleNamespace(svg_content=svg, content={"svgReceipt":{"source_preservation":receipt,
        "quality":dict(source_digest=digest,passed=True,measurement_complete=True)}})
    presentation = SimpleNamespace(theme={"svg":dict(sourcePreservation=dict(
        schemaVersion="svg-source-preservation/v1",manifestDigest="manifest",pageCount=1),
        styleSnapshot=dict(digest=style_digest,source_template=dict(digest=template_digest,composition_mode="free-svg")))})
    assert source_preservation_receipts(presentation,[slide],canvas) == [receipt]
    receipt["template_application"]["template_digest"] = "forged"
    with pytest.raises(PublicExportOperationError, match="svg_source_export_template_evidence_invalid"):
        source_preservation_receipts(presentation,[slide],canvas)



def test_normal_master_furniture_attestation_is_derived_from_pinned_stamp():
    from services.svg_master_furniture import stamp_master_furniture, master_furniture_receipt, attested_svg_furniture

    base = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="80" y="100" font-family="Arial" font-size="24" fill="#111111">Body</text></svg>')
    snapshot = {"system_furniture":{"digest":"sha256:"+"a"*64,"elements":[
        dict(id="svg-system-master-bar",kind="rect",x=20,y=20,width=80,height=8,fill="#112233"),
    ]}}
    stamped = stamp_master_furniture(base,snapshot,0,2)
    receipt = master_furniture_receipt(stamped,snapshot,width=1280,height=720,index=0,n=2)
    assert set(attested_svg_furniture(stamped,receipt,width=1280,height=720)) == {
        "svg-system-master-furniture","svg-system-master-bar"}
    with pytest.raises(ValueError, match="svg_source_furniture_receipt_mismatch"):
        master_furniture_receipt(stamped.replace("#112233","#ffffff"),snapshot,
                                width=1280,height=720,index=0,n=2)
