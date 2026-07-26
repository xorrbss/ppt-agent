from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from templates.v2.pptx.analyzer import analyze_ooxml_candidates
from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader
from templates.v2.pptx.smartart_parser import (
    SmartArtEvidenceLimits,
    parse_smartart_data_model,
)

PRESENTATION_XML = b"""\
<p:presentation
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
 <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>"""
PRESENTATION_RELS = b"""\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
  Target="slides/slide1.xml"/>
</Relationships>"""
SMARTART_SLIDE_XML = b"""\
<p:sld
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:cSld><p:spTree>
  <p:nvGrpSpPr/><p:grpSpPr/>
  <p:graphicFrame>
   <p:nvGraphicFramePr><p:cNvPr id="4" name="Process"/></p:nvGraphicFramePr>
   <p:xfrm><a:off x="914400" y="914400"/>
    <a:ext cx="5486400" cy="3657600"/></p:xfrm>
   <a:graphic><a:graphicData
    uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">
    <dgm:relIds r:dm="rIdData"/>
   </a:graphicData></a:graphic>
  </p:graphicFrame>
 </p:spTree></p:cSld>
</p:sld>"""
SMARTART_RELS = b"""\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdData"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData"
  Target="../diagrams/data1.xml"/>
</Relationships>"""
SMARTART_DATA = b"""\
<dgm:dataModel
 xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <dgm:ptLst>
  <dgm:pt modelId="root" type="doc"/>
  <dgm:pt modelId="step-1"><dgm:t><a:p><a:r><a:t>Discover</a:t></a:r>
  </a:p></dgm:t></dgm:pt>
  <dgm:pt modelId="step-2"><dgm:t><a:p><a:r><a:t>Deliver</a:t></a:r>
  </a:p></dgm:t></dgm:pt>
 </dgm:ptLst>
 <dgm:cxnLst>
  <dgm:cxn modelId="edge-1" srcId="step-1" destId="step-2" type="parOf"/>
 </dgm:cxnLst>
</dgm:dataModel>"""


def _pptx_bytes(*, include_relationship: bool = True) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", PRESENTATION_XML)
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            PRESENTATION_RELS,
        )
        archive.writestr("ppt/slides/slide1.xml", SMARTART_SLIDE_XML)
        if include_relationship:
            archive.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                SMARTART_RELS,
            )
            archive.writestr("ppt/diagrams/data1.xml", SMARTART_DATA)
    return stream.getvalue()


def test_smartart_data_model_is_evidence_only_and_keeps_manual_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "smartart-structured.pptx"
    payload = _pptx_bytes()
    source.write_bytes(payload)

    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    smartart = candidates.slides[0].shapes[0]

    assert smartart.kind == "unsupported"
    assert smartart.unsupported_reason == "unsupported_ooxml:smartArt"
    assert smartart.smartart_evidence is not None
    assert smartart.smartart_evidence.status == "structured"
    assert [node.text for node in smartart.smartart_evidence.nodes] == [
        None,
        "Discover",
        "Deliver",
    ]
    assert smartart.smartart_evidence.connections[0].source_id == "step-1"
    assert smartart.smartart_evidence.connections[0].destination_id == "step-2"

    analysis = analyze_ooxml_candidates(candidates)
    analyzed_evidence = analysis.candidates.slides[0].shapes[0].smartart_evidence
    assert analyzed_evidence is not None
    assert analyzed_evidence.status == "structured"

    draft = assemble_template_v2_draft(candidates)
    unsupported = draft.manifest["slides"][0]["unsupported"][0]
    assert unsupported["contract"] == {
        "editable": False,
        "source_preserved": True,
        "action": "manual_rebuild",
    }
    assert unsupported["smartart_evidence"]["status"] == "structured"
    assert draft.manifest["slides"][0]["fallback"]["kind"] == "manual_review"


def test_smartart_missing_data_relationship_is_explicit_unavailable_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "smartart-no-data-relationship.pptx"
    payload = _pptx_bytes(include_relationship=False)
    source.write_bytes(payload)

    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    evidence = candidates.slides[0].shapes[0].smartart_evidence

    assert evidence is not None
    assert evidence.model_dump(mode="json") == {
        "evidence_version": 1,
        "status": "unavailable",
        "diagnostic": "data_relationship_missing",
        "data_part": None,
        "nodes": [],
        "connections": [],
    }


def test_smartart_evidence_limits_fail_closed_without_partial_structure() -> None:
    from xml.etree.ElementTree import fromstring

    evidence = parse_smartart_data_model(
        fromstring(SMARTART_DATA),
        data_part="ppt/diagrams/data1.xml",
        limits=SmartArtEvidenceLimits(max_nodes=2),
    )

    assert evidence.status == "unavailable"
    assert evidence.diagnostic == "data_model_limits_exceeded"
    assert evidence.nodes == []
    assert evidence.connections == []
