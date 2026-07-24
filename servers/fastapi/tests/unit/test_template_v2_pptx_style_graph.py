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
    relationships = "".join(
        f'<Relationship Id="{rel_id}" Type="{OFFICE_REL}/{kind}" '
        f'Target="{target}"/>'
        for rel_id, kind, target in entries
    )
    return (
        f'<Relationships xmlns="{REL_NS}">{relationships}</Relationships>'
    ).encode()


def _pptx() -> bytes:
    parts = {
        "[Content_Types].xml": b"<Types/>",
        "ppt/presentation.xml": b"""
            <p:presentation
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:sldIdLst><p:sldId id="256" r:id="rIdSlide"/></p:sldIdLst>
              <p:sldSz cx="12192000" cy="6858000"/>
            </p:presentation>
        """,
        "ppt/_rels/presentation.xml.rels": _relationships(
            ("rIdSlide", "slide", "slides/slide1.xml"),
            ("rIdMaster", "slideMaster", "slideMasters/slideMaster1.xml"),
        ),
        "ppt/slides/slide1.xml": b"""
            <p:sld
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/></p:spTree></p:cSld>
            </p:sld>
        """,
        "ppt/slides/_rels/slide1.xml.rels": _relationships(
            ("rIdLayout", "slideLayout", "../slideLayouts/slideLayout1.xml"),
        ),
        "ppt/slideLayouts/slideLayout1.xml": b"""
            <p:sldLayout
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:cSld name="Title and Content"><p:spTree>
                <p:nvGrpSpPr/><p:grpSpPr/>
                <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr></p:sp>
              </p:spTree></p:cSld>
            </p:sldLayout>
        """,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _relationships(
            ("rIdMaster", "slideMaster", "../slideMasters/slideMaster1.xml"),
        ),
        "ppt/slideMasters/slideMaster1.xml": b"""
            <p:sldMaster
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:cSld><p:spTree>
                <p:nvGrpSpPr/><p:grpSpPr/>
                <p:sp><p:nvSpPr><p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr></p:sp>
              </p:spTree></p:cSld>
            </p:sldMaster>
        """,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _relationships(
            ("rIdTheme", "theme", "../theme/theme1.xml"),
            ("rIdLayout", "slideLayout", "../slideLayouts/slideLayout1.xml"),
        ),
        "ppt/theme/theme1.xml": b"""
            <a:theme
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              name="Corporate">
              <a:themeElements>
                <a:clrScheme name="Corporate colors">
                  <a:dk1><a:sysClr val="windowText" lastClr="101820"/></a:dk1>
                  <a:accent1><a:srgbClr val="245BE7"/></a:accent1>
                </a:clrScheme>
                <a:fontScheme name="Corporate fonts">
                  <a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont>
                  <a:minorFont><a:latin typeface="Aptos"/></a:minorFont>
                </a:fontScheme>
                <a:fmtScheme name="Corporate formats"/>
              </a:themeElements>
            </a:theme>
        """,
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def test_style_graph_resolves_slide_layout_master_and_theme(tmp_path: Path) -> None:
    payload = _pptx()
    source = tmp_path / "style-evidence.pptx"
    source.write_bytes(payload)

    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert candidates.style_graph is not None
    binding = candidates.style_graph.slide_bindings[0]
    assert binding.model_dump() == {
        "slide_part": "ppt/slides/slide1.xml",
        "layout_part": "ppt/slideLayouts/slideLayout1.xml",
        "master_part": "ppt/slideMasters/slideMaster1.xml",
        "theme_part": "ppt/theme/theme1.xml",
    }
    assert candidates.style_graph.layouts[0].name == "Title and Content"
    assert candidates.style_graph.layouts[0].placeholder_types == ["title"]
    assert candidates.style_graph.masters[0].placeholder_types == ["ftr"]
    theme = candidates.style_graph.themes[0]
    assert theme.major_font == "Aptos Display"
    assert theme.minor_font == "Aptos"
    assert theme.colors == {"dk1": "101820", "accent1": "245BE7"}

    draft = assemble_template_v2_draft(candidates)
    assert draft.manifest["style_evidence"] == (
        candidates.style_graph.model_dump(mode="json")
    )
