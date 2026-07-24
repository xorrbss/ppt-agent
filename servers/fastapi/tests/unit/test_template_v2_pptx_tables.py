from __future__ import annotations

import hashlib
import io
from pathlib import Path
import zipfile

from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)


def _pptx() -> bytes:
    presentation_rels = (
        f'<Relationships xmlns="{REL_NS}">'
        f'<Relationship Id="rId1" Type="{OFFICE_REL}/slide" '
        'Target="slides/slide1.xml"/></Relationships>'
    )
    slide = b"""
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
       <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>
        <p:graphicFrame>
         <p:nvGraphicFramePr><p:cNvPr id="8" name="Revenue Table"/></p:nvGraphicFramePr>
         <p:xfrm><a:off x="914400" y="914400"/><a:ext cx="4572000" cy="1828800"/></p:xfrm>
         <a:graphic><a:graphicData><a:tbl>
          <a:tr><a:tc><a:txBody><a:p><a:r><a:t>Quarter</a:t></a:r></a:p></a:txBody></a:tc>
           <a:tc><a:txBody><a:p><a:r><a:t>Revenue</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
          <a:tr><a:tc><a:txBody><a:p><a:r><a:t>Q1</a:t></a:r></a:p></a:txBody></a:tc>
           <a:tc><a:txBody><a:p><a:r><a:t>42</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
         </a:tbl></a:graphicData></a:graphic>
        </p:graphicFrame>
       </p:spTree></p:cSld>
      </p:sld>
    """
    parts = {
        "[Content_Types].xml": b"<Types/>",
        "ppt/presentation.xml": b"""
          <p:presentation
           xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
           <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
           <p:sldSz cx="12192000" cy="6858000"/>
          </p:presentation>
        """,
        "ppt/_rels/presentation.xml.rels": presentation_rels.encode(),
        "ppt/slides/slide1.xml": slide,
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def test_ooxml_table_becomes_editable_template_v2_table(tmp_path: Path) -> None:
    payload = _pptx()
    source = tmp_path / "table.pptx"
    source.write_bytes(payload)
    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )

    table = candidates.slides[0].shapes[0]
    assert table.kind == "table"
    assert table.table_rows == [["Quarter", "Revenue"], ["Q1", "42"]]

    draft = assemble_template_v2_draft(candidates)
    element = draft.raw_layouts.layouts[0].elements[0]
    assert element.type == "table"
    assert [run.text for run in element.columns[0].runs] == ["Quarter"]
    assert [run.text for run in element.rows[0][1].runs] == ["42"]
    component_id = draft.layouts.layouts[0].components[0].id
    assert draft.contents[0][component_id]["revenue_table"] == {
        "columns": ["Quarter", "Revenue"],
        "rows": [["Q1", "42"]],
    }
