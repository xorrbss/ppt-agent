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
