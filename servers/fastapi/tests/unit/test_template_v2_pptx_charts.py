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


def _relationships(*entries: tuple[str, str, str]) -> bytes:
    values = "".join(
        f'<Relationship Id="{rel_id}" Type="{OFFICE_REL}/{kind}" '
        f'Target="{target}"/>'
        for rel_id, kind, target in entries
    )
    return f'<Relationships xmlns="{REL_NS}">{values}</Relationships>'.encode()


def _pptx() -> bytes:
    slide = b"""
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
       <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>
        <p:graphicFrame>
         <p:nvGraphicFramePr><p:cNvPr id="9" name="Sales Chart"/></p:nvGraphicFramePr>
         <p:xfrm><a:off x="914400" y="914400"/><a:ext cx="4572000" cy="2743200"/></p:xfrm>
         <a:graphic><a:graphicData><c:chart r:id="rIdChart"/></a:graphicData></a:graphic>
        </p:graphicFrame>
       </p:spTree></p:cSld>
      </p:sld>
    """
    chart = b"""
      <c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
       <c:chart><c:plotArea><c:barChart>
        <c:barDir val="col"/><c:grouping val="clustered"/>
        <c:ser><c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>Sales</c:v></c:pt>
         </c:strCache></c:strRef></c:tx>
         <c:cat><c:strRef><c:strCache>
          <c:pt idx="0"><c:v>Q1</c:v></c:pt><c:pt idx="1"><c:v>Q2</c:v></c:pt>
         </c:strCache></c:strRef></c:cat>
         <c:val><c:numRef><c:numCache>
          <c:pt idx="0"><c:v>42</c:v></c:pt><c:pt idx="1"><c:v>51.5</c:v></c:pt>
         </c:numCache></c:numRef></c:val>
        </c:ser>
       </c:barChart></c:plotArea></c:chart>
      </c:chartSpace>
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
        "ppt/_rels/presentation.xml.rels": _relationships(
            ("rId1", "slide", "slides/slide1.xml")
        ),
        "ppt/slides/slide1.xml": slide,
        "ppt/slides/_rels/slide1.xml.rels": _relationships(
            ("rIdChart", "chart", "../charts/chart1.xml")
        ),
        "ppt/charts/chart1.xml": chart,
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def test_chart_cache_becomes_editable_template_v2_chart(tmp_path: Path) -> None:
    payload = _pptx()
    source = tmp_path / "chart.pptx"
    source.write_bytes(payload)
    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )

    chart = candidates.slides[0].shapes[0]
    assert chart.kind == "chart"
    assert chart.chart_type == "bar"
    assert chart.chart_categories == ["Q1", "Q2"]
    assert chart.chart_series[0].values == [42, 51.5]

    draft = assemble_template_v2_draft(candidates)
    element = draft.raw_layouts.layouts[0].elements[0]
    assert element.type == "chart"
    assert element.categories == ["Q1", "Q2"]
    assert element.series[0].name == "Sales"
