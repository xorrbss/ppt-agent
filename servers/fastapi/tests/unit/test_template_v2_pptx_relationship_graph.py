from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.models import PresentationCandidates
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from templates.v2.pptx.relationship_graph import (
    RelationshipGraphLimits,
    build_relationship_graph_evidence,
    relationship_graph_manifest_summary,
)


SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)
SLIDE_LAYOUT_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "slideLayout"
)
THEME_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
)
HYPERLINK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "hyperlink"
)
OLE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "oleObject"
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

PRESENTATION_XML = b"""\
<p:presentation
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
 <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>"""
SLIDE_XML = b"""\
<p:sld
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree>
  <p:nvGrpSpPr/><p:grpSpPr/>
  <p:sp>
   <p:nvSpPr><p:cNvPr id="2" name="Title 1"/></p:nvSpPr>
   <p:spPr><a:xfrm><a:off x="1219200" y="685800"/>
    <a:ext cx="6096000" cy="914400"/></a:xfrm>
    <a:prstGeom prst="rect"/>
   </p:spPr>
   <p:txBody><a:p><a:r><a:t>Structure evidence</a:t></a:r></a:p></p:txBody>
  </p:sp>
 </p:spTree></p:cSld>
</p:sld>"""


def _relationships(*entries: str) -> bytes:
    body = "\n".join(entries)
    return (
        f'<Relationships xmlns="{REL_NS}">\n{body}\n</Relationships>'
    ).encode()


def _relationship(
    relationship_id: str,
    relationship_type: str,
    target: str,
    *,
    external: bool = False,
) -> str:
    target_mode = ' TargetMode="External"' if external else ""
    return (
        f'<Relationship Id="{relationship_id}" Type="{relationship_type}" '
        f'Target="{target}"{target_mode}/>'
    )


def _pptx_bytes(
    *,
    presentation_relationships: list[str] | None = None,
    slide_relationships: list[str] | None = None,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    presentation_relationships = presentation_relationships or [
        _relationship("rId1", SLIDE_REL_TYPE, "slides/slide1.xml")
    ]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", PRESENTATION_XML)
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            _relationships(*presentation_relationships),
        )
        archive.writestr("ppt/slides/slide1.xml", SLIDE_XML)
        if slide_relationships is not None:
            archive.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                _relationships(*slide_relationships),
            )
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return stream.getvalue()


def _reader(tmp_path: Path, payload: bytes) -> PptxPackageReader:
    source = tmp_path / "source.pptx"
    source.write_bytes(payload)
    return PptxPackageReader(source)


def test_external_targets_are_not_retained_or_dereferenced(tmp_path: Path) -> None:
    private_url = "https://private.example.invalid/path?token=super-secret"
    payload = _pptx_bytes(
        presentation_relationships=[
            _relationship("rId1", SLIDE_REL_TYPE, "slides/slide1.xml"),
            _relationship(
                "rIdExternal",
                HYPERLINK_REL_TYPE,
                private_url,
                external=True,
            ),
        ],
        slide_relationships=[
            _relationship(
                "rIdOle",
                OLE_REL_TYPE,
                "../embeddings/workbook.xlsx",
            )
        ],
        extra={"ppt/embeddings/workbook.xlsx": b"must-not-be-read"},
    )

    evidence = build_relationship_graph_evidence(_reader(tmp_path, payload))
    summary = relationship_graph_manifest_summary(evidence)
    serialized = json.dumps(
        {
            "evidence": evidence.model_dump(mode="json"),
            "summary": summary,
        },
        sort_keys=True,
    )

    assert private_url not in serialized
    assert "super-secret" not in serialized
    external = [item for item in evidence.relationships if item.external]
    assert external[0].target_part is None
    assert summary["external_relationships"] == [
        {
            "source_part": "ppt/presentation.xml",
            "relationship_id": "rIdExternal",
            "relationship_kind": "hyperlink",
        }
    ]
    assert evidence.skipped_relationship_count == 1
    assert evidence.blocked_relationship_kind_counts == {"ole_object": 1}
    assert "ppt/embeddings/workbook.xlsx" not in evidence.nodes
    assert summary["embedded_content_policy"] == {
        "dereference_enabled": False,
        "execution_enabled": False,
        "retained_target_identifiers": False,
    }


def test_cycles_and_missing_parts_are_bounded_anomalies(tmp_path: Path) -> None:
    payload = _pptx_bytes(
        slide_relationships=[
            _relationship(
                "rIdCycle",
                SLIDE_LAYOUT_REL_TYPE,
                "../presentation.xml",
            ),
            _relationship("rIdMissing", THEME_REL_TYPE, "../theme/missing.xml"),
        ]
    )

    evidence = build_relationship_graph_evidence(_reader(tmp_path, payload))
    summary = relationship_graph_manifest_summary(evidence)

    assert evidence.cycle_count == 1
    assert evidence.missing_parts == ["ppt/theme/missing.xml"]
    assert summary["cycle_count"] == 1
    assert summary["missing_part_count"] == 1
    assert summary["processing"] == {
        "local_render_enabled": False,
        "ocr_enabled": False,
        "external_model_access": False,
    }


def test_disallowed_internal_relationship_still_rejects_unsafe_path(
    tmp_path: Path,
) -> None:
    payload = _pptx_bytes(
        slide_relationships=[
            _relationship("rIdOle", OLE_REL_TYPE, "../../../escape.bin")
        ]
    )

    with pytest.raises(UnsafePptxPackage) as caught:
        build_relationship_graph_evidence(_reader(tmp_path, payload))

    assert caught.value.code == "unsafe_relationship_target"


@pytest.mark.parametrize(
    ("limits", "expected_code"),
    [
        (
            RelationshipGraphLimits(max_nodes=1),
            "relationship_graph_node_limit_exceeded",
        ),
        (
            RelationshipGraphLimits(max_edges=1),
            "relationship_graph_edge_limit_exceeded",
        ),
    ],
)
def test_relationship_graph_hard_limits_have_stable_rejections(
    tmp_path: Path,
    limits: RelationshipGraphLimits,
    expected_code: str,
) -> None:
    payload = _pptx_bytes(
        presentation_relationships=[
            _relationship("rId1", SLIDE_REL_TYPE, "slides/slide1.xml"),
            _relationship(
                "rIdExternal",
                HYPERLINK_REL_TYPE,
                "https://example.invalid/",
                external=True,
            ),
        ]
    )
    reader = _reader(tmp_path, payload)

    with pytest.raises(UnsafePptxPackage) as caught:
        build_relationship_graph_evidence(reader, limits=limits)

    assert caught.value.code == expected_code


def test_structure_evidence_does_not_change_editable_assembly(
    tmp_path: Path,
) -> None:
    payload = _pptx_bytes()
    candidates = parse_presentation_candidates(
        _reader(tmp_path, payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    with_evidence = assemble_template_v2_draft(candidates)
    legacy_candidates = PresentationCandidates(
        source_sha256=candidates.source_sha256,
        slides=candidates.slides,
    )
    without_evidence = assemble_template_v2_draft(legacy_candidates)

    assert with_evidence.raw_layouts == without_evidence.raw_layouts
    assert with_evidence.layouts == without_evidence.layouts
    assert with_evidence.contents == without_evidence.contents
    assert {
        key: value
        for key, value in with_evidence.manifest.items()
        if key != "structure_evidence"
    } == without_evidence.manifest
    assert "structure_evidence" in with_evidence.manifest
    assert legacy_candidates.relationship_graph is None
