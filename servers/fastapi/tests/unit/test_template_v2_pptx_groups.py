from __future__ import annotations

import hashlib
import io
from pathlib import Path
import zipfile

from templates.v2.pptx.analyzer import analyze_ooxml_candidates
from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)


def _pptx() -> bytes:
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
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="{OFFICE_REL}/slide" '
            'Target="slides/slide1.xml"/></Relationships>'
        ).encode(),
        "ppt/slides/slide1.xml": b"""
          <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
           <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>
            <p:grpSp>
             <p:nvGrpSpPr><p:cNvPr id="10" name="Metric Group"/></p:nvGrpSpPr>
             <p:grpSpPr><a:xfrm>
              <a:off x="1219200" y="685800"/><a:ext cx="6096000" cy="3429000"/>
              <a:chOff x="0" y="0"/><a:chExt cx="6000" cy="3000"/>
             </a:xfrm></p:grpSpPr>
             <p:sp>
              <p:nvSpPr><p:cNvPr id="11" name="Label"/></p:nvSpPr>
              <p:spPr><a:xfrm><a:off x="600" y="300"/>
               <a:ext cx="1800" cy="600"/></a:xfrm></p:spPr>
              <p:txBody><a:p><a:r><a:t>Revenue</a:t></a:r></a:p></p:txBody>
             </p:sp>
             <p:sp>
              <p:nvSpPr><p:cNvPr id="12" name="Card"/></p:nvSpPr>
              <p:spPr><a:xfrm><a:off x="3000" y="1500"/>
               <a:ext cx="2400" cy="900"/></a:xfrm>
               <a:prstGeom prst="roundRect"/>
              </p:spPr>
             </p:sp>
            </p:grpSp>
           </p:spTree></p:cSld>
          </p:sld>
        """,
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def test_ooxml_group_preserves_editable_children_and_coordinates(
    tmp_path: Path,
) -> None:
    payload = _pptx()
    source = tmp_path / "group.pptx"
    source.write_bytes(payload)
    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )

    group = candidates.slides[0].shapes[0]
    assert group.kind == "group"
    assert [child.kind for child in group.children or []] == [
        "text",
        "container",
    ]
    assert (group.x, group.y, group.width, group.height) == (
        128,
        72,
        640,
        360,
    )
    label = (group.children or [])[0]
    assert (label.x, label.y, label.width, label.height) == (64, 36, 192, 72)

    analysis = analyze_ooxml_candidates(candidates)
    assert analysis.summary.supported_shape_count == 1
    draft = assemble_template_v2_draft(candidates)
    element = draft.layouts.layouts[0].components[0].elements[0]
    assert element.type == "group"
    assert [child.type for child in element.children] == ["text", "container"]
