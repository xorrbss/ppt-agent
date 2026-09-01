from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from services.authored_vnext.design_pack import DesignPackRegistry, ingest_design_pack


_PRESENTATION = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/></p:sldIdLst>
 <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>
"""

_PRESENTATION_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>
"""

_SLIDE_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>
"""

_LAYOUT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>
"""


def _shape(
    shape_id: int,
    text: str,
    *,
    placeholder: str | None = "title",
    geometry: bool = True,
    typography: bool = True,
) -> str:
    placeholder_xml = f'<p:ph type="{placeholder}"/>' if placeholder else ""
    geometry_xml = (
        '<a:xfrm><a:off x="685800" y="685800"/><a:ext cx="5486400" cy="914400"/></a:xfrm>'
        if geometry
        else ""
    )
    run_properties = (
        '<a:rPr sz="3200" b="1"><a:solidFill><a:srgbClr val="0F172A"/></a:solidFill><a:latin typeface="Noto Sans KR"/><a:ea typeface="Noto Sans KR"/></a:rPr>'
        if typography
        else ""
    )
    return f"""
    <p:sp>
      <p:nvSpPr><p:cNvPr id="{shape_id}" name="{(placeholder or 'Decoration').title()} {shape_id}"/><p:cNvSpPr/><p:nvPr>{placeholder_xml}</p:nvPr></p:nvSpPr>
      <p:spPr>{geometry_xml}<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:ln w="12700"><a:solidFill><a:srgbClr val="2563EB"/></a:solidFill></a:ln></p:spPr>
      <p:txBody><a:bodyPr lIns="91440" tIns="45720" rIns="91440" bIns="45720" anchor="ctr"/><a:lstStyle/><a:p><a:pPr algn="l"><a:lnSpc><a:spcPct val="120000"/></a:lnSpc></a:pPr><a:r>{run_properties}<a:t>{text}</a:t></a:r></a:p></p:txBody>
    </p:sp>
    """


def _slide(text: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{_shape(2, text, geometry=False, typography=False)}{_shape(3, "Protected brand", placeholder=None)}</p:spTree></p:cSld>
</p:sld>
"""


def _layout_or_master(root: str, text: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:{root} xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:cSld name="Title Layout"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{_shape(9, text, placeholder="title")}</p:spTree></p:cSld>
</p:{root}>
"""


def _synthetic_pptx(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", _PRESENTATION)
        archive.writestr("ppt/_rels/presentation.xml.rels", _PRESENTATION_RELS)
        archive.writestr("ppt/slides/slide1.xml", _slide("Cover title"))
        archive.writestr("ppt/slides/slide2.xml", _slide("Closing title"))
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", _SLIDE_RELS)
        archive.writestr("ppt/slides/_rels/slide2.xml.rels", _SLIDE_RELS)
        archive.writestr(
            "ppt/slideLayouts/slideLayout1.xml",
            _layout_or_master("sldLayout", "Layout chrome"),
        )
        archive.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _LAYOUT_RELS)
        archive.writestr(
            "ppt/slideMasters/slideMaster1.xml",
            _layout_or_master("sldMaster", "Master chrome"),
        )


def test_source_native_design_pack_register_reload_and_seal(tmp_path: Path) -> None:
    source = tmp_path / "reference.pptx"
    storage = tmp_path / "design-packs"
    _synthetic_pptx(source)

    manifest = ingest_design_pack(
        source,
        pack_id="synthetic-blue",
        package_version="1.0.0",
        storage_root=storage,
    )
    registry = DesignPackRegistry(storage)
    registered = registry.get("synthetic-blue", "1.0.0")

    assert registered.manifest.package_digest == manifest.package_digest
    assert registered.structure.structure_digest == manifest.structure_digest
    assert [frame.role for frame in registered.structure.frames] == ["cover", "closing"]
    assert registered.structure.frames[0].slots[0].semantic_role == "title"
    assert registered.structure.frames[0].slots[0].accepted_kinds == ("text",)
    assert registered.structure.frames[0].slots[0].constraints
    assert registered.structure.frames[0].slots[0].required is True
    assert registered.structure.frames[0].slots[0].capacity_envelope is not None
    assert registered.structure.frames[0].slots[0].patch_handle is not None
    assert registered.structure.frames[0].slots[0].patch_handle.kind == "pptx-shape"
    assert registered.structure.frames[0].slots[0].extraction_confidence == 1.0
    assert registered.structure.frames[0].slots[0].bounds.width == 5486400
    assert registered.structure.frames[0].slots[0].text_style.font_size == 32
    assert registered.structure.frames[0].slots[0].text_style.vertical_alignment == "middle"
    assert registered.structure.frames[0].slots[0].spacing.line_spacing == 1.2
    assert registered.structure.frames[0].capabilities.layout_pattern == "title-only"
    assert registered.structure.frames[0].capabilities.max_text_characters
    assert registered.structure.frames[0].chrome
    assert all(
        element.protected and not element.editable
        for element in registered.structure.frames[0].chrome
    )
    assert any(
        element.source_part == "ppt/slides/slide1.xml"
        and element.element_id.endswith("-3")
        for element in registered.structure.frames[0].chrome
    )
    assert registered.structure.representative_renders[0].evidence_level == "structural"

    authored_manifest = ingest_design_pack(
        {
            "schemaVersion": "authored-design-source/v1",
            "canvas": {"width": 1280, "height": 720},
            "frames": [
                {
                    "id": "cover",
                    "type": "scene",
                    "role": "cover",
                    "elements": [
                        {
                            "id": "headline",
                            "kind": "text",
                            "semanticRole": "title",
                            "editable": True,
                            "slotApproved": True,
                            "slotId": "slot:cover-headline",
                            "required": True,
                            "containerId": "scene:cover",
                            "extractionConfidence": 1.0,
                            "bounds": {"x": 80, "y": 90, "width": 720, "height": 120},
                            "textStyle": {
                                "latinFont": "Inter",
                                "eastAsianFont": "Noto Sans KR",
                                "fontSize": 32,
                                "fontWeight": 700,
                                "color": "#102A43",
                                "alignment": "left",
                            },
                            "spacing": {
                                "insetLeft": 12,
                                "insetTop": 8,
                                "insetRight": 12,
                                "insetBottom": 8,
                                "lineSpacing": 1.2,
                            },
                        },
                        {
                            "id": "hero-visual",
                            "kind": "image",
                            "editable": True,
                            "slotApproved": True,
                            "bounds": {"x": 820, "y": 120, "width": 360, "height": 300},
                        },
                        {
                            "id": "evidence-grid",
                            "kind": "table",
                            "editable": True,
                            "slotApproved": True,
                            "bounds": {"x": 80, "y": 280, "width": 680, "height": 280},
                        },
                        {
                            "id": "brand-lockup",
                            "kind": "text",
                            "semanticRole": "logo",
                            "editable": True,
                            "bounds": {"x": 980, "y": 40, "width": 220, "height": 60},
                        }
                    ],
                },
                {
                    "id": "closing",
                    "type": "html",
                    "role": "closing",
                    "elements": [
                        {
                            "id": "protected-footer",
                            "kind": "text",
                            "semanticRole": "logo",
                            "editable": True,
                            "bounds": {"x": 80, "y": 640, "width": 1120, "height": 32},
                        }
                    ],
                }
            ],
        },
        pack_id="synthetic-authored",
        package_version="2.1.0",
        storage_root=storage,
    )
    discovered = registry.reload()
    authored = registry.get("synthetic-authored", "2.1.0")
    assert len(discovered) == 2
    assert authored.manifest.package_digest == authored_manifest.package_digest
    assert authored.structure.frames[0].frame_type == "scene"
    assert authored.structure.frames[0].slots[0].selector == "scene:cover#headline"
    assert authored.structure.frames[0].slots[0].patch_handle is not None
    assert authored.structure.frames[0].slots[0].patch_handle.kind == "scene-element"
    assert authored.structure.frames[0].slots[0].hide_policy == "never"
    assert authored.structure.frames[0].slots[1].semantic_role == "visual"
    assert authored.structure.frames[0].slots[1].slot_id == "slot:cover-hero-visual"
    assert authored.structure.frames[0].slots[1].capacity_envelope.max_images == 1
    assert authored.structure.frames[0].slots[2].semantic_role == "data"
    assert authored.structure.frames[0].slots[2].capacity_envelope.max_table_rows > 1
    assert authored.structure.frames[0].capabilities.accepted_kinds == (
        "text",
        "image",
        "table",
    )
    assert authored.structure.frames[0].capabilities.supports_mixed_content is True
    assert authored.structure.frames[0].capabilities.data_slot_count == 1
    assert authored.structure.tokens.typography == ("Inter", "Noto Sans KR")
    assert len(authored.structure.frames[0].chrome) == 1
    assert authored.structure.frames[0].chrome[0].protected is True
    assert authored.structure.frames[0].chrome[0].editable is False
    assert authored.structure.frames[0].chrome[0].container_id == "scene:cover"
    assert authored.structure.frames[1].slots == ()
    assert authored.structure.frames[1].editability == "fidelity-only"
    assert authored.structure.frames[1].chrome[0].protected is True
