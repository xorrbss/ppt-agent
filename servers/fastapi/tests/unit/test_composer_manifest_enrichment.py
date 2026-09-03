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
_STYLE_KEYS = {"sizePt", "bold", "italic", "color"}


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
    nested = frame.add_paragraph()
    nested.text = "Inherited from master"
    nested.level = 1

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

    slide.shapes.add_picture(
        str(image_path),
        Emu(_PICTURE_GEOMETRY["x"]),
        Emu(_PICTURE_GEOMETRY["y"]),
        Emu(_PICTURE_GEOMETRY["cx"]),
        Emu(_PICTURE_GEOMETRY["cy"]),
    )
    path = tmp_path / "deck.pptx"
    deck.save(str(path))
    return path


@pytest.fixture
def manifest(deck_path: Path) -> SourceContentManifest:
    return extract_pptx_source_content(
        deck_path, owner_id="owner-a", reference_snapshot_id="snapshot-a"
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
    assert style == {"sizePt": 40.0, "bold": True, "italic": False, "color": "#1F2A44"}
    assert payload["runStyles"] == [style]
    assert payload["dominantSizePt"] == 40.0
    assert (payload["placeholderType"], payload["placeholderIdx"]) == ("title", 0)
    assert block.semantic_role == "title"


def test_body_runs_inherit_size_from_layout_then_master(manifest: SourceContentManifest) -> None:
    payload = _text_blocks(manifest)["Explicit fourteen"].payload
    paragraphs = payload["paragraphs"]
    assert [paragraph["runStyles"][0]["sizePt"] for paragraph in paragraphs] == [14.0, 20.0, 28.0]
    assert paragraphs[0]["runStyles"][0]["italic"] is True
    # No ``sz`` on the run: the layout placeholder's ``a:lstStyle`` level 1 wins.
    assert paragraphs[1]["runStyles"][0] == {
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
    assert set(paragraphs[0]) == {"runs", "text", "bullet", "bulletKind", "level", "runStyles"}
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
    assert image.payload["geometry"] == _PICTURE_GEOMETRY
    assert (image.payload["placeholderType"], image.payload["placeholderIdx"]) == (None, None)
    [table] = [block for block in unit.blocks if block.kind == "table"]
    assert table.payload["cells"] == [["Metric", "Value"]]
    first, second = table.payload["rows"][0]
    assert first["paragraphs"][0]["runStyles"][0]["sizePt"] == 11.0
    assert second["paragraphs"][0]["runStyles"][0]["sizePt"] is None
    assert "dominantSizePt" not in table.payload


def test_slide_size_is_sealed_into_the_manifest_digest(manifest: SourceContentManifest) -> None:
    assert manifest.slide_size == SourceSlideSize(cx=9144000, cy=6858000)
    public = manifest.model_dump(mode="json", by_alias=True)
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
    legacy = {key: value for key, value in public.items() if key not in {"slideSize", "manifestDigest"}}
    legacy["manifestDigest"] = digest_json(legacy)
    return legacy


def test_manifest_without_slide_size_still_validates_and_re_derives_digest(
    manifest: SourceContentManifest,
) -> None:
    legacy = _legacy_public_payload(manifest)
    restored = SourceContentManifest.model_validate({**legacy, "ownerId": "owner-a"})
    assert restored.slide_size is None
    dumped = restored.model_dump(mode="json", by_alias=True)
    assert "slideSize" not in dumped
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
