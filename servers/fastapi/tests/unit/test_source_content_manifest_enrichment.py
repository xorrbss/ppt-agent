"""Manifest enrichment: run typography, placeholder identity and slide size.

Builds a small deck with python-pptx and checks that
``extract_pptx_source_content`` captures the hierarchy signals the Composer
classifier needs, and that manifests sealed before this capture still
validate and re-derive their recorded digest.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Emu, Pt
import pytest

from services.source_content.contracts import SourceContentManifest, SourceSlideSize
from services.source_content.digests import digest_json
from services.source_content.pptx import extract_pptx_source_content

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)
_PICTURE_GEOMETRY = {"x": 914400, "y": 1828800, "cx": 2743200, "cy": 1828800}
_STYLE_KEYS = {
    "sizePt",
    "bold",
    "italic",
    "color",
    "fontFamily",
    "sourceParagraphIndex",
    "sourceRunIndex",
    "sourceFlatRunIndex",
    "textStart",
    "textEnd",
}


def _override_layout_body_size(layout: Any, *, idx: int, size_hundredths: str) -> None:
    """Give the layout's body placeholder an explicit level-1 default size."""

    shape = next(ph for ph in layout.placeholders if ph.placeholder_format.idx == idx)
    tx_body = shape.text_frame._txBody
    list_style = tx_body.find(qn("a:lstStyle"))
    if list_style is None:
        list_style = OxmlElement("a:lstStyle")
        tx_body.insert(1, list_style)
    level = OxmlElement("a:lvl1pPr")
    default_run = OxmlElement("a:defRPr")
    default_run.set("sz", size_hundredths)
    level.append(default_run)
    list_style.append(level)


def _append_bullet(paragraph: Any, kind: str, **attributes: str) -> None:
    properties = paragraph._p.get_or_add_pPr()
    bullet = OxmlElement(f"a:{kind}")
    for name, value in attributes.items():
        bullet.set(name, value)
    properties.append(bullet)


@pytest.fixture
def deck_path(tmp_path: Path) -> Path:
    image_path = tmp_path / "pixel.png"
    image_path.write_bytes(_PNG_1X1)
    deck = Presentation()
    layout = deck.slide_layouts[1]  # Title and Content
    _override_layout_body_size(layout, idx=1, size_hundredths="2000")
    slide = deck.slides.add_slide(layout)

    title = slide.shapes.title
    title.text_frame.text = "Quarterly Review"
    title_run = title.text_frame.paragraphs[0].runs[0]
    title_run.font.size = Pt(40)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(0x1F, 0x2A, 0x44)

    frame = slide.placeholders[1].text_frame
    frame.text = "Explicit fourteen"
    explicit = frame.paragraphs[0].runs[0]
    explicit.font.size = Pt(14)
    explicit.font.italic = True
    inherited = frame.add_paragraph()
    inherited.text = "Inherited from the layout placeholder"
    _append_bullet(inherited, "buAutoNum", type="arabicPeriod")
    nested = frame.add_paragraph()
    nested.text = "Inherited from master"
    nested.level = 1
    _append_bullet(nested, "buFont", typeface="Wingdings")
    _append_bullet(nested, "buChar", char="§")

    footer_layout = next(
        ph for ph in layout.placeholders if ph.placeholder_format.type == PP_PLACEHOLDER.FOOTER
    )
    slide.shapes.clone_placeholder(footer_layout)
    footer = slide.placeholders[footer_layout.placeholder_format.idx]
    footer.text_frame.text = "Confidential"

    box = slide.shapes.add_textbox(Emu(457200), Emu(5486400), Emu(3657600), Emu(457200))
    box.text_frame.text = "Plain text box"

    table = slide.shapes.add_table(1, 2, Emu(457200), Emu(4572000), Emu(3657600), Emu(457200)).table
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(0, 0).text_frame.paragraphs[0].runs[0].font.size = Pt(11)

    picture = slide.shapes.add_picture(
        str(image_path),
        Emu(_PICTURE_GEOMETRY["x"]),
        Emu(_PICTURE_GEOMETRY["y"]),
        Emu(_PICTURE_GEOMETRY["cx"]),
        Emu(_PICTURE_GEOMETRY["cy"]),
    )
    picture_rel_id = picture._element.blipFill.blip.get(qn("r:embed"))
    assert picture_rel_id is not None
    color_change = OxmlElement("a:clrChange")
    color_change.set("useA", "1")
    color_from = OxmlElement("a:clrFrom")
    from_srgb = OxmlElement("a:srgbClr")
    from_srgb.set("val", "FFFFFF")
    color_from.append(from_srgb)
    color_to = OxmlElement("a:clrTo")
    to_srgb = OxmlElement("a:srgbClr")
    to_srgb.set("val", "FFFFFF")
    to_alpha = OxmlElement("a:alpha")
    to_alpha.set("val", "0")
    to_srgb.append(to_alpha)
    color_to.append(to_srgb)
    color_change.extend((color_from, color_to))
    picture._element.blipFill.blip.append(color_change)
    luminance = OxmlElement("a:lum")
    luminance.set("bright", "-10000")
    luminance.set("contrast", "60000")
    picture._element.blipFill.blip.append(luminance)
    picture_bullet = OxmlElement("a:buBlip")
    bullet_blip = OxmlElement("a:blip")
    bullet_blip.set(qn("r:embed"), picture_rel_id)
    picture_bullet.append(bullet_blip)
    table.cell(0, 1).text_frame.paragraphs[0]._p.get_or_add_pPr().append(picture_bullet)
    path = tmp_path / "deck.pptx"
    deck.save(str(path))
    return path


@pytest.fixture
def manifest(deck_path: Path) -> SourceContentManifest:
    return extract_pptx_source_content(
        deck_path, owner_id="owner-a", reference_snapshot_id="snapshot-a"
    ).manifest


@pytest.fixture
def manifest_with_document_identity(deck_path: Path) -> SourceContentManifest:
    deck = Presentation(str(deck_path))
    deck.core_properties.title = "  Quarterly   Review  Deck  "
    deck.save(str(deck_path))
    return extract_pptx_source_content(
        deck_path,
        owner_id="owner-a",
        reference_snapshot_id="snapshot-a",
        original_file_name="Q3_Board_Deck_2026-01-05 (2).pptx",
    ).manifest


def _text_blocks(manifest: SourceContentManifest) -> dict[str, Any]:
    """Text blocks of the only slide keyed by their first line."""

    [unit] = manifest.units
    return {
        block.payload["text"].split("\n", 1)[0]: block
        for block in unit.blocks
        if block.kind == "text"
    }


def test_title_run_carries_explicit_size_weight_and_color(manifest: SourceContentManifest) -> None:
    block = _text_blocks(manifest)["Quarterly Review"]
    payload = block.payload
    [style] = payload["paragraphs"][0]["runStyles"]
    assert {key: style[key] for key in ("sizePt", "bold", "italic", "color")} == {
        "sizePt": 40.0,
        "bold": True,
        "italic": False,
        "color": "#1F2A44",
    }
    assert style["fontFamily"]
    assert (style["sourceParagraphIndex"], style["sourceRunIndex"]) == (0, 0)
    assert (style["textStart"], style["textEnd"]) == (0, len("Quarterly Review"))
    assert payload["runStyles"] == [style]
    assert payload["dominantSizePt"] == 40.0
    assert (payload["placeholderType"], payload["placeholderIdx"]) == ("title", 0)
    assert payload["geometry"]["source"] in {"layout", "master"}
    assert payload["geometry"]["sourceBlockId"] == block.source_block_id
    assert payload["textBody"]["marginLeftEmu"] > 0
    assert block.semantic_role == "title"


def test_connector_native_ends_and_targets_reach_whole_page_facts(tmp_path: Path) -> None:
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    start = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(100000), Emu(100000), Emu(1500000), Emu(700000))
    start.text = "Source"
    end = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(4000000), Emu(2000000), Emu(1500000), Emu(700000))
    end.text = "Target"
    connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
        Emu(1600000), Emu(450000), Emu(4000000), Emu(2350000))
    expected = {
        "headEnd": {"type": "none", "w": "sm", "len": "lg"},
        "tailEnd": {"type": "triangle", "w": "lg", "len": "sm"},
        "stCxn": {"id": str(start.shape_id), "idx": "3"},
        "endCxn": {"id": str(end.shape_id), "idx": "1"},
    }
    shape_properties = connector._element.find(qn("p:spPr"))
    line = shape_properties.find(qn("a:ln"))
    if line is None:
        line = OxmlElement("a:ln")
        shape_properties.append(line)
    for name in ("headEnd", "tailEnd"):
        node = OxmlElement("a:" + name)
        for key, value in expected[name].items():
            node.set(key, value)
        line.append(node)
    properties = connector._element.find(qn("p:nvCxnSpPr")).find(qn("p:cNvCxnSpPr"))
    for name in ("stCxn", "endCxn"):
        node = OxmlElement("a:" + name)
        for key, value in expected[name].items():
            node.set(key, value)
        properties.append(node)
    plain = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
        Emu(100000), Emu(3000000), Emu(4000000), Emu(3000000))
    path = tmp_path / "directed.pptx"
    deck.save(path)
    original_bytes = path.read_bytes()
    extracted = extract_pptx_source_content(path, owner_id="owner-a", reference_snapshot_id="snapshot-a")
    page = extracted.manifest.units[0]
    blocks = {block.payload["shapeId"]: block for block in page.blocks}
    payload = blocks[str(connector.shape_id)].payload
    assert {key: payload[key] for key in expected} == expected
    assert not set(expected).intersection(blocks[str(plain.shape_id)].payload)
    facts = _whole_page_facts(page, {})
    by_shape = {fact["data"]["shapeId"]: fact for fact in facts}
    connection_fact = by_shape[str(connector.shape_id)]
    assert connection_fact["semantic_role"] == "connector"
    assert {key: connection_fact["data"][key] for key in expected} == expected
    for key, target in (("stCxn", start), ("endCxn", end)):
        target_fact = by_shape[str(target.shape_id)]
        assert connection_fact["connections"][key] == {
            **expected[key], "source_id": target_fact["source_id"], "element_id": target_fact["element_id"]}
    assert "connections" not in by_shape[str(plain.shape_id)]
    packed = extracted.manifest.model_dump(mode="json", by_alias=True)
    recorded_digest = packed.pop("manifestDigest")
    assert digest_json(packed) == recorded_digest
    assert path.read_bytes() == original_bytes


def test_body_runs_inherit_size_from_layout_then_master(manifest: SourceContentManifest) -> None:
    payload = _text_blocks(manifest)["Explicit fourteen"].payload
    paragraphs = payload["paragraphs"]
    assert [paragraph["runStyles"][0]["sizePt"] for paragraph in paragraphs] == [14.0, 20.0, 28.0]
    assert paragraphs[0]["runStyles"][0]["italic"] is True
    # No ``sz`` on the run: the layout placeholder's ``a:lstStyle`` level 1 wins.
    assert {
        key: paragraphs[1]["runStyles"][0][key]
        for key in ("sizePt", "bold", "italic", "color")
    } == {
        "sizePt": 20.0,
        "bold": False,
        "italic": False,
        "color": None,
    }
    # Level 2 is not overridden by the layout, so the master ``p:bodyStyle`` applies.
    assert paragraphs[2]["level"] == 1
    assert payload["dominantSizePt"] == 20.0
    assert (payload["placeholderType"], payload["placeholderIdx"]) == ("obj", 1)
    # Existing keys keep their shape: runs stay strings, styles ride alongside.
    assert [paragraph["runs"] for paragraph in paragraphs] == [
        ["Explicit fourteen"],
        ["Inherited from the layout placeholder"],
        ["Inherited from master"],
    ]
    assert paragraphs[1]["sourceParagraphIndex"] == 1
    assert paragraphs[1]["bulletAutoNumType"] == "arabicPeriod"
    assert paragraphs[1]["bulletStartAt"] is None
    assert paragraphs[2]["bulletChar"] == "§"
    assert paragraphs[2]["bulletFontFamily"] == "Wingdings"
    assert paragraphs[1]["textStart"] == len("Explicit fourteen") + 1
    assert {"alignment", "marginLeftEmu", "indentEmu", "spaceBeforePt", "lineSpacingPct"} <= set(
        paragraphs[0]
    )
    assert len(payload["runStyles"]) == len(payload["runs"]) == 5
    assert payload["runs"][1] == "\n"
    assert payload["runStyles"][1]["sizePt"] == 20.0
    assert all(set(style) == _STYLE_KEYS for style in payload["runStyles"])


def test_footer_and_text_box_resolve_master_styles(manifest: SourceContentManifest) -> None:
    blocks = _text_blocks(manifest)
    footer = blocks["Confidential"]
    assert footer.payload["runStyles"][0]["sizePt"] == 12.0
    assert (footer.payload["placeholderType"], footer.payload["placeholderIdx"]) == ("ftr", 11)
    assert footer.semantic_role == "source_note"
    box = blocks["Plain text box"].payload
    assert box["runStyles"][0]["sizePt"] == 18.0
    assert (box["placeholderType"], box["placeholderIdx"]) == (None, None)


def test_picture_and_table_blocks_keep_geometry_and_gain_styles(
    manifest: SourceContentManifest,
) -> None:
    [unit] = manifest.units
    [image] = [block for block in unit.blocks if block.kind == "image"]
    assert {key: image.payload["geometry"][key] for key in _PICTURE_GEOMETRY} == _PICTURE_GEOMETRY
    assert image.payload["crop"] == {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    assert image.payload["colorTransforms"] == [
        {
            "kind": "colorChange",
            "fromColor": "#FFFFFF",
            "fromAlpha": 1.0,
            "toColor": "#FFFFFF",
            "toAlpha": 0.0,
            "useAlpha": True,
        },
        {"kind": "luminance", "brightness": -0.1, "contrast": 0.6},
    ]
    assert (image.payload["placeholderType"], image.payload["placeholderIdx"]) == (None, None)
    [table] = [block for block in unit.blocks if block.kind == "table"]
    assert table.payload["cells"] == [["Metric", "Value"]]
    first, second = table.payload["rows"][0]
    assert first["paragraphs"][0]["runStyles"][0]["sizePt"] == 11.0
    assert second["paragraphs"][0]["runStyles"][0]["sizePt"] is None
    picture_bullet = second["paragraphs"][0]
    assert picture_bullet["bulletKind"] == "picture"
    assert picture_bullet["bulletImageDataUri"].startswith("data:image/png;base64,")
    assert picture_bullet["bulletImageDigest"].startswith("sha256:")
    assert len(table.payload["columnWidthsEmu"]) == 2
    assert sum(table.payload["columnWidthsEmu"]) == table.payload["geometry"]["cx"]
    assert table.payload["rowHeightsEmu"] == [457200]
    assert first["sourceCellId"].endswith(":row:0:column:0")
    assert len(first["runs"]) == len(first["runStyles"])
    assert manifest.counts.picture_bullet_count == 1
    assert "dominantSizePt" not in table.payload


def test_slide_size_is_sealed_into_the_manifest_digest(manifest: SourceContentManifest) -> None:
    assert manifest.slide_size == SourceSlideSize(cx=9144000, cy=6858000)
    assert manifest.source_layout_version == "source-layout/v2"
    public = manifest.model_dump(mode="json", by_alias=True)
    assert public["sourceLayoutVersion"] == "source-layout/v2"
    assert public["slideSize"] == {"cx": 9144000, "cy": 6858000}
    unsigned = dict(public)
    unsigned.pop("manifestDigest")
    assert digest_json(unsigned) == manifest.manifest_digest
    without_size = {key: value for key, value in unsigned.items() if key != "slideSize"}
    assert digest_json(without_size) != manifest.manifest_digest
    # A stored copy re-derives the same public payload (``_manifest_from_record`` path).
    restored = SourceContentManifest.model_validate({**public, "ownerId": "owner-a"})
    assert restored.model_dump(mode="json", by_alias=True) == public


def _legacy_public_payload(manifest: SourceContentManifest) -> dict[str, Any]:
    """The stored JSON of a manifest sealed before ``slideSize`` was captured."""

    public = manifest.model_dump(mode="json", by_alias=True)
    legacy = {
        key: value
        for key, value in public.items()
        if key not in {"slideSize", "sourceLayoutVersion", "manifestDigest"}
    }
    legacy["counts"] = dict(legacy["counts"])
    legacy["counts"].pop("pictureBulletCount")
    legacy["manifestDigest"] = digest_json(legacy)
    return legacy


def test_manifest_without_slide_size_still_validates_and_re_derives_digest(
    manifest: SourceContentManifest,
) -> None:
    legacy = _legacy_public_payload(manifest)
    restored = SourceContentManifest.model_validate({**legacy, "ownerId": "owner-a"})
    assert restored.slide_size is None
    assert restored.source_layout_version is None
    assert restored.counts.picture_bullet_count is None
    dumped = restored.model_dump(mode="json", by_alias=True)
    assert "slideSize" not in dumped
    assert "sourceLayoutVersion" not in dumped
    assert "pictureBulletCount" not in dumped["counts"]
    unsigned = dict(dumped)
    unsigned.pop("manifestDigest")
    assert digest_json(unsigned) == legacy["manifestDigest"] == restored.manifest_digest


def test_manifest_store_admits_legacy_and_enriched_records(manifest: SourceContentManifest) -> None:
    """The exact record-admission path accepts rows stored before and after capture."""

    from services.source_content import manifest_store

    enriched = manifest.model_dump(mode="json", by_alias=True)
    legacy = _legacy_public_payload(manifest)
    for stored in (legacy, enriched):
        row = SimpleNamespace(manifest_json=stored, manifest_digest=stored["manifestDigest"])
        admitted = manifest_store._manifest_from_record(row, owner_id=None)
        assert admitted.manifest_digest == stored["manifestDigest"]
    assert manifest_store._manifest_from_record(
        SimpleNamespace(manifest_json=legacy, manifest_digest=legacy["manifestDigest"]),
        owner_id=None,
    ).slide_size is None
    assert manifest_store._manifest_from_record(
        SimpleNamespace(manifest_json=enriched, manifest_digest=enriched["manifestDigest"]),
        owner_id=None,
    ).slide_size == SourceSlideSize(cx=9144000, cy=6858000)


# --- document title / uploaded file name -----------------------------------------------


def test_manifest_without_document_identity_still_validates_and_re_derives_digest(
    manifest: SourceContentManifest,
) -> None:
    """No core-properties title, no ``original_file_name`` -- both fields absent."""

    assert manifest.document_title is None
    assert manifest.source_file_title is None
    public = manifest.model_dump(mode="json", by_alias=True)
    assert "documentTitle" not in public
    assert "sourceFileTitle" not in public
    unsigned = dict(public)
    unsigned.pop("manifestDigest")
    assert digest_json(unsigned) == manifest.manifest_digest


def test_document_title_and_file_name_title_are_captured_and_sealed_into_the_digest(
    manifest_with_document_identity: SourceContentManifest,
) -> None:
    manifest = manifest_with_document_identity
    # docProps/core.xml dc:title, whitespace-collapsed.
    assert manifest.document_title == "Quarterly Review Deck"
    # Extension, trailing "(2)" copy suffix and trailing date stripped, then
    # underscores normalised to spaces.
    assert manifest.source_file_title == "Q3 Board Deck"

    public = manifest.model_dump(mode="json", by_alias=True)
    assert public["documentTitle"] == "Quarterly Review Deck"
    assert public["sourceFileTitle"] == "Q3 Board Deck"
    unsigned = dict(public)
    unsigned.pop("manifestDigest")
    assert digest_json(unsigned) == manifest.manifest_digest
    without_identity = {
        key: value for key, value in unsigned.items()
        if key not in {"documentTitle", "sourceFileTitle"}
    }
    assert digest_json(without_identity) != manifest.manifest_digest


def test_blank_core_properties_title_is_treated_as_absent(deck_path: Path) -> None:
    deck = Presentation(str(deck_path))
    deck.core_properties.title = "   "
    deck.save(str(deck_path))
    manifest = extract_pptx_source_content(
        deck_path, owner_id="owner-a", reference_snapshot_id="snapshot-a"
    ).manifest
    assert manifest.document_title is None
    assert "documentTitle" not in manifest.model_dump(mode="json", by_alias=True)


def test_generic_and_opaque_file_names_do_not_become_source_file_title(deck_path: Path) -> None:
    for original_name in ("Presentation1.pptx", "3fa85f64-5717-4562-b3fc-2c963f66afa6.pptx"):
        manifest = extract_pptx_source_content(
            deck_path,
            owner_id="owner-a",
            reference_snapshot_id="snapshot-a",
            original_file_name=original_name,
        ).manifest
        assert manifest.source_file_title is None
        assert "sourceFileTitle" not in manifest.model_dump(mode="json", by_alias=True)
