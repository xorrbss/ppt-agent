"""OOXML embedded-font parts on a real pptx, exercised against a real python-pptx template.

Neither python-pptx nor the vendored slide-master converter write
``embeddedFontLst``; this exercises the raw zip/XML writer end to end instead
of trusting the ECMA-376 layout from memory.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pptx import Presentation

from services.svg_pptx_font_embedding import (
    FONT_CONTENT_TYPE,
    FONT_RELATIONSHIP_TYPE,
    NS_CT,
    NS_P,
    NS_R,
    NS_RELS,
    embed_trusted_fonts,
)
from services.svg_trusted_fonts import FONT_FAMILY, read_trusted_pretendard_faces


def _blank_pptx(tmp_path: Path) -> Path:
    target = tmp_path / "result.pptx"
    Presentation().save(str(target))
    return target


def test_embed_trusted_fonts_adds_parts_relationships_and_content_type(tmp_path):
    target = _blank_pptx(tmp_path)
    expected = read_trusted_pretendard_faces()

    digests = embed_trusted_fonts(target)

    assert digests == tuple(digest for _weight, digest, _payload in expected)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        font_parts = sorted(name for name in names if name.startswith("ppt/fonts/"))
        assert len(font_parts) == 2
        payload_by_digest = {
            "sha256:" + __import__("hashlib").sha256(archive.read(part)).hexdigest(): archive.read(part)
            for part in font_parts
        }
        for _weight, digest, payload in expected:
            assert payload_by_digest[digest] == payload

        presentation_root = ET.fromstring(archive.read("ppt/presentation.xml"))
        assert presentation_root.get("embedTrueTypeFonts") == "1"
        children = list(presentation_root)
        lst_index = next(i for i, c in enumerate(children) if c.tag == f"{{{NS_P}}}embeddedFontLst")
        notes_sz_index = next(i for i, c in enumerate(children) if c.tag == f"{{{NS_P}}}notesSz")
        assert lst_index == notes_sz_index + 1

        embedded_font_lst = children[lst_index]
        embedded_fonts = embedded_font_lst.findall(f"{{{NS_P}}}embeddedFont")
        assert len(embedded_fonts) == 1
        font_element = embedded_fonts[0].find(f"{{{NS_P}}}font")
        assert font_element.get("typeface") == FONT_FAMILY
        regular = embedded_fonts[0].find(f"{{{NS_P}}}regular")
        bold = embedded_fonts[0].find(f"{{{NS_P}}}bold")
        assert regular is not None and regular.get(f"{{{NS_R}}}id")
        assert bold is not None and bold.get(f"{{{NS_R}}}id")

        rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        font_relationships = [
            element for element in rels_root.findall(f"{{{NS_RELS}}}Relationship")
            if element.get("Type") == FONT_RELATIONSHIP_TYPE
        ]
        assert len(font_relationships) == 2
        relationship_ids = {element.get("Id") for element in font_relationships}
        assert relationship_ids == {regular.get(f"{{{NS_R}}}id"), bold.get(f"{{{NS_R}}}id")}

        content_types_root = ET.fromstring(archive.read("[Content_Types].xml"))
        fntdata_defaults = [
            element for element in content_types_root.findall(f"{{{NS_CT}}}Default")
            if element.get("Extension") == "fntdata"
        ]
        assert len(fntdata_defaults) == 1
        assert fntdata_defaults[0].get("ContentType") == FONT_CONTENT_TYPE

    # python-pptx's own oxml parser must still accept the file: proves the XML
    # this module wrote is well-formed and namespace-correct, not just that
    # ElementTree could read back what it itself wrote.
    reopened = Presentation(str(target))
    assert len(reopened.slides) == 0


def test_embed_trusted_fonts_does_not_collide_with_existing_relationship_ids(tmp_path):
    target = _blank_pptx(tmp_path)
    with zipfile.ZipFile(target) as archive:
        rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    existing_ids = {element.get("Id") for element in rels_root.findall(f"{{{NS_RELS}}}Relationship")}
    assert existing_ids, "a default python-pptx template always has at least one relationship"

    embed_trusted_fonts(target)

    with zipfile.ZipFile(target) as archive:
        rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    all_ids = [element.get("Id") for element in rels_root.findall(f"{{{NS_RELS}}}Relationship")]
    assert len(all_ids) == len(set(all_ids)), "relationship ids must stay unique after embedding"
    assert existing_ids <= set(all_ids)


def test_whole_page_measured_symbol_runs_and_used_font_bytes(tmp_path):
    from copy import deepcopy
    import hashlib
    import pytest
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from utils.svg_template_fonts import FONT_FAMILY as NOTO, materialize_template_fallbacks
    from workers.public_svg_whole_page_convert import (
        _project_trusted_symbol_runs, _verify_trusted_symbol_runs, _embed_verified_whole_page_fonts,
    )
    from services.svg_pptx_font_embedding import trusted_export_font_faces

    source = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
        '<text id="source-title" x="80" y="100" font-family="Noto Sans KR" font-size="28" '
        'font-weight="700">한글 ▸ ▾ ☐ ↻ 끝</text><text id="source-cell" x="80" y="200" '
        'font-family="Noto Sans KR" font-size="20" font-weight="400">표 ▸ 항목</text></svg>')
    _, measured = materialize_template_fallbacks(source)
    receipt = {"svg_digest": measured["canonical_svg_digest"], "rendering_verified": True,
        "render_font_fallback": deepcopy(measured)}
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(1))
    run = shape.text_frame.paragraphs[0].add_run()
    run.text = "한글 ▸ ▾ ☐ ↻ 끝"
    run.font.name, run.font.bold, run.font.size = NOTO, True, Pt(20)
    run.font.color.rgb = RGBColor(0x12, 0x34, 0x56)
    table_shape = slide.shapes.add_table(1, 2, Inches(1), Inches(3), Inches(7), Inches(1))
    for cell, value in zip(table_shape.table.rows[0].cells, ("표 ▸ 항목", "일반 한글")):
        cell.text = value
        cell_run = cell.text_frame.paragraphs[0].runs[0]
        cell_run.font.name, cell_run.font.bold, cell_run.font.size = NOTO, False, Pt(16)
    geometry = [(item.shape_id, item.left, item.top, item.width, item.height) for item in slide.shapes]
    source_before, receipt_before = source, deepcopy(receipt)
    usage, proof = _project_trusted_symbol_runs(deck, [source], [receipt])
    assert set(usage) == {NOTO, "Segoe UI Symbol"}
    assert usage["Segoe UI Symbol"] == {400, 700}
    assert geometry == [(item.shape_id, item.left, item.top, item.width, item.height) for item in slide.shapes]
    assert shape.text_frame.text == "한글 ▸ ▾ ☐ ↻ 끝"
    assert table_shape.table.cell(0, 0).text == "표 ▸ 항목"
    assert all(item.font.bold is True and item.font.size == Pt(20)
        and item.font.color.rgb == RGBColor(0x12, 0x34, 0x56) for item in shape.text_frame.paragraphs[0].runs)
    assert all(item._r.t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
        for item in shape.text_frame.paragraphs[0].runs if item.text[:1].isspace() or item.text[-1:].isspace())
    assert source == source_before and receipt == receipt_before
    target = tmp_path / "measured-symbols.pptx"
    deck.save(target)
    with zipfile.ZipFile(target) as archive:
        original_slide = archive.read("ppt/slides/slide1.xml")
    embedded = _embed_verified_whole_page_fonts(target, usage)
    assert embedded["families"] == [NOTO, "Segoe UI Symbol"]
    assert embedded["ooxmlVerified"] is True and embedded["sourcePartsPreserved"] is True
    verified = _verify_trusted_symbol_runs(target, usage, proof)
    assert verified["ooxmlVerified"] is True and verified["fallbackRunCount"] == 5
    with zipfile.ZipFile(target) as archive:
        assert archive.read("ppt/slides/slide1.xml") == original_slide
        for family, slot, digest, payload in trusted_export_font_faces(usage):
            assert digest == "sha256:" + hashlib.sha256(payload).hexdigest()
            assert any(archive.read(name) == payload for name in archive.namelist() if name.endswith(".fntdata"))
    for violation in ("missing_measurement", "digest", "not_verified", "unavailable"):
        altered_source, altered = source, deepcopy(receipt)
        if violation == "missing_measurement": altered.pop("render_font_fallback")
        elif violation == "digest": altered["render_font_fallback"]["fallback_font_digest"] = "sha256:" + "0" * 64
        elif violation == "not_verified": altered["rendering_verified"] = False
        else: altered_source = source.replace("끝", "\U0010ffff")
        with pytest.raises(ValueError):
            _project_trusted_symbol_runs(Presentation(), [altered_source], [altered])
    for violation in ("ordinary_as_symbol", "symbol_as_primary"):
        tampered = Presentation(target)
        runs = tampered.slides[0].shapes[0].text_frame.paragraphs[0].runs
        selected = next(item for item in runs if (item.font.name == NOTO) == (violation == "ordinary_as_symbol"))
        selected.font.name = "Segoe UI Symbol" if violation == "ordinary_as_symbol" else NOTO
        changed = tmp_path / f"{violation}.pptx"
        tampered.save(changed)
        with pytest.raises(ValueError):
            _verify_trusted_symbol_runs(changed, usage, proof)
    plain = Presentation()
    text = plain.slides.add_slide(plain.slide_layouts[6]).shapes.add_textbox(0, 0, Inches(2), Inches(1))
    text.text = "기존 일반 본문"
    text.text_frame.paragraphs[0].runs[0].font.name = NOTO
    before = text._element.xml
    plain_source = source.replace("한글 ▸ ▾ ☐ ↻ 끝", "기존 일반 본문").replace("표 ▸ 항목", "일반")
    plain_usage, plain_proof = _project_trusted_symbol_runs(plain, [plain_source], [{"rendering_verified": True}])
    assert plain_proof["runs"] == [] and plain_usage == {NOTO: {400}}
    assert text._element.xml == before
    with pytest.raises(ValueError):
        trusted_export_font_faces({"Untrusted User Font": {400}})
