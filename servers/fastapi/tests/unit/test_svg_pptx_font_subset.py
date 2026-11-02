"""Subset font embedding for SVG PPTX: small, deterministic, glyph-complete faces."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

from fontTools.ttLib import TTFont
from pptx import Presentation

from services.svg_pptx_font_embedding import embed_trusted_fonts, export_font_faces
from services.svg_pptx_font_subset import BASELINE_TEXT, package_text, subset_face
from services.svg_trusted_fonts import read_trusted_pretendard_faces
from utils.svg_template_fonts import FONT_FAMILY as NOTO, font_material

DECK_TEXT = "분기 실적 요약 매출 18% 증가"


def _deck(tmp_path: Path, text: str = DECK_TEXT) -> Path:
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = text
    target = tmp_path / "deck.pptx"
    deck.save(str(target))
    return target


def _cmap(payload: bytes) -> set[int]:
    with TTFont(BytesIO(payload)) as font:
        return set(font.getBestCmap())


def test_package_text_collects_slide_text(tmp_path):
    assert set(DECK_TEXT) <= set(package_text(_deck(tmp_path)))


def test_default_embedding_subsets_to_deck_glyphs_and_is_deterministic(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = _deck(tmp_path / "a"), _deck(tmp_path / "b")

    digests = embed_trusted_fonts(first)
    assert digests == embed_trusted_fonts(second)

    whole = {digest for _weight, digest, _payload in read_trusted_pretendard_faces()}
    assert not whole & set(digests)
    with zipfile.ZipFile(first) as archive:
        parts = [archive.read(name) for name in archive.namelist() if name.startswith("ppt/fonts/")]
    assert len(parts) == 2
    for payload in parts:
        assert len(payload) < 1_000_000
        covered = _cmap(payload)
        assert {ord(character) for character in DECK_TEXT} <= covered
        assert ord("A") in covered
        # A syllable absent from the deck and the baseline is not shipped.
        assert ord("뷁") not in covered


def test_export_font_faces_matches_what_embedding_writes(tmp_path):
    target = _deck(tmp_path)
    expected = export_font_faces(target)
    assert embed_trusted_fonts(target) == tuple(digest for _f, _s, digest, _p in expected)


def test_variable_noto_is_instantiated_per_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    _digest, payload = font_material(NOTO, 700)
    bold = subset_face(payload, weight=700, text=DECK_TEXT + BASELINE_TEXT)
    with TTFont(BytesIO(bold)) as font:
        assert "fvar" not in font
        assert font["OS/2"].usWeightClass == 700
        assert font["head"].macStyle & 1
        assert font["name"].getDebugName(1) == NOTO
    assert len(bold) < len(payload) // 10
    # The on-disk instance cache serves the next process the same bytes.
    cached = list((tmp_path / "cache").rglob("*.ttf"))
    assert cached
    assert subset_face(payload, weight=700, text=DECK_TEXT + BASELINE_TEXT) == bold


def test_ooxml_usage_names_the_trusted_families_the_runs_use(tmp_path):
    from pptx.util import Pt

    from services.svg_pptx_font_embedding import ooxml_trusted_font_usage

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box = slide.shapes.add_textbox(0, 0, Pt(400), Pt(80)).text_frame
    regular = box.paragraphs[0].add_run()
    regular.text, regular.font.name = "본문", NOTO
    bold = box.add_paragraph().add_run()
    bold.text, bold.font.name, bold.font.bold = "제목", NOTO, True
    other = box.add_paragraph().add_run()
    other.text, other.font.name = "system", "Arial"
    target = tmp_path / "usage.pptx"
    deck.save(str(target))

    assert ooxml_trusted_font_usage(target) == {NOTO: {400, 700}}
    faces = export_font_faces(target, ooxml_trusted_font_usage(target))
    assert sorted((family, slot) for family, slot, _d, _p in faces) == [(NOTO, "bold"), (NOTO, "regular")]


def test_embedding_keeps_package_parts_in_the_default_namespace(tmp_path):
    """A prefixed [Content_Types].xml or .rels (``<ct:Types>``) makes LibreOffice refuse the file."""
    target = _deck(tmp_path)
    embed_trusted_fonts(target)
    with zipfile.ZipFile(target) as archive:
        content_types = archive.read("[Content_Types].xml").decode("utf-8")
        relationships = archive.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
    assert "<Types xmlns=" in content_types and "<ct:" not in content_types
    assert "<Relationships xmlns=" in relationships and ":Relationship " not in relationships
