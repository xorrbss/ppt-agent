from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

from templates.v2.pptx.placeholder_evidence import (
    extract_runtime_placeholder_evidence,
)


_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)


def _relationships(*entries: tuple[str, str, str]) -> str:
    rows = "".join(
        f'<Relationship Id="{relationship_id}" Type="{kind}" Target="{target}"/>'
        for relationship_id, kind, target in entries
    )
    return f'<Relationships xmlns="{_REL_NS}">{rows}</Relationships>'


def _shape(name: str, placeholder: str, *, geometry: bool = False) -> str:
    xfrm = (
        '<p:spPr><a:xfrm><a:off x="1371600" y="3886200"/>'
        '<a:ext cx="6400800" cy="1752600"/></a:xfrm></p:spPr>'
        if geometry
        else "<p:spPr/>"
    )
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="2" name="{name}"/><p:cNvSpPr/><p:nvPr>{placeholder}</p:nvPr>'
        f"</p:nvSpPr>{xfrm}<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody>"
        "</p:sp>"
    )


def _slide_document(shape: str) -> str:
    return (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<p:cSld><p:spTree>{shape}</p:spTree></p:cSld></p:sld>"
    )


def _layout_document(shape: str, *, master: bool = False) -> str:
    root = "p:sldMaster" if master else "p:sldLayout"
    return (
        f'<{root} xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<p:cSld><p:spTree>{shape}</p:spTree></p:cSld></{root}>"
    )


def test_extracts_placeholder_attributes_and_layout_master_inheritance(tmp_path):
    source = tmp_path / "placeholder-evidence.pptx"
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
            "</p:presentation>",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            _relationships(
                ("rId1", f"{_OFFICE_REL}/slide", "slides/slide1.xml"),
            ),
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            _slide_document(
                _shape(
                    "사용자 지정 제목",
                    '<p:ph idx="7" orient="vert" sz="half"/>',
                    geometry=True,
                )
            ),
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            _relationships(
                (
                    "rId1",
                    f"{_OFFICE_REL}/slideLayout",
                    "../slideLayouts/slideLayout1.xml",
                ),
            ),
        )
        archive.writestr(
            "ppt/slideLayouts/slideLayout1.xml",
            _layout_document(
                _shape("Layout Title", '<p:ph idx="7" type="title"/>')
            ),
        )
        archive.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _relationships(
                (
                    "rId1",
                    f"{_OFFICE_REL}/slideMaster",
                    "../slideMasters/slideMaster1.xml",
                ),
            ),
        )
        archive.writestr(
            "ppt/slideMasters/slideMaster1.xml",
            _layout_document(
                _shape("Master Title", '<p:ph idx="7" type="title"/>'),
                master=True,
            ),
        )

    evidence = extract_runtime_placeholder_evidence(source)

    assert evidence.status == "available"
    assert evidence.slide_count == 1
    shape = evidence.shapes[0]
    assert shape.shape_name == "사용자 지정 제목"
    assert shape.slide_placeholder.idx == "7"
    assert shape.slide_placeholder.orient == "vert"
    assert shape.slide_placeholder.size == "half"
    assert shape.layout_placeholder.type == "title"
    assert shape.master_placeholder.type == "title"
    assert shape.resolved_type == "title"
    assert shape.status == "resolved"
    assert shape.geometry == {
        "x": 144.0,
        "y": 408.0,
        "width": 672.0,
        "height": 184.0,
    }
