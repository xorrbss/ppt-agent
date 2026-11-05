"""Subset font embedding for SVG PPTX: small, deterministic, glyph-complete faces."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

from fontTools.ttLib import TTFont
from pptx import Presentation
import pytest

from scripts import build_svg_pptx_font_instances as instance_build
from services import svg_pptx_font_subset
from services.svg_pptx_font_embedding import embed_trusted_fonts, export_font_faces
from services.svg_pptx_font_subset import BASELINE_TEXT, package_text, subset_face
from services.svg_trusted_fonts import read_trusted_pretendard_faces
from utils.hashing import sha256_prefixed
from utils.svg_template_fonts import FONT_DIGEST as NOTO_DIGEST, FONT_FAMILY as NOTO, font_material

DECK_TEXT = "분기 실적 요약 매출 18% 증가"
WEIGHTS = (400, 700)


@pytest.fixture(scope="module")
def runtime_instances():
    """What export-time instancing produces for the pinned Noto source (slow, once)."""
    _digest, payload = font_material(NOTO, 400)
    return {weight: svg_pptx_font_subset.instantiate_static_face(payload, weight) for weight in WEIGHTS}


@pytest.fixture(scope="module")
def prebuilt_directory(tmp_path_factory, runtime_instances):
    directory = tmp_path_factory.mktemp("font-instances")
    for weight, instance in runtime_instances.items():
        svg_pptx_font_subset.prebuilt_instance_path(NOTO_DIGEST, weight, directory).write_bytes(instance)
    return directory


@pytest.fixture(autouse=True)
def _isolated_instances(monkeypatch, prebuilt_directory):
    """Every test sees a known prebuilt directory and no warm in-process instance."""
    monkeypatch.setattr(svg_pptx_font_subset, "PREBUILT_INSTANCE_DIRECTORY", prebuilt_directory)
    svg_pptx_font_subset._cached_static_instance.cache_clear()
    yield
    svg_pptx_font_subset._cached_static_instance.cache_clear()


def _refuse_instancing(monkeypatch):
    def refuse(_payload, _weight):
        raise AssertionError("instanced at export time")

    monkeypatch.setattr(svg_pptx_font_subset, "instantiate_static_face", refuse)


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


def test_variable_noto_is_instantiated_per_slot(runtime_instances):
    for weight in WEIGHTS:
        with TTFont(BytesIO(runtime_instances[weight])) as font:
            assert "fvar" not in font
            assert font["OS/2"].usWeightClass == weight
            assert bool(font["head"].macStyle & 1) == (weight == 700)
            assert font["name"].getDebugName(1) == NOTO


def test_pinned_prebuilt_instances_are_the_runtime_instancing_bytes(runtime_instances):
    """The pins equal export-time instancing, so prebuilt loading keeps PPTX bytes."""
    assert svg_pptx_font_subset.PREBUILT_INSTANCE_DIGESTS == {
        (NOTO_DIGEST, weight): sha256_prefixed(runtime_instances[weight]) for weight in WEIGHTS
    }


def test_export_loads_prebuilt_instances_without_instancing(monkeypatch, runtime_instances):
    monkeypatch.delenv("APP_DATA_DIRECTORY", raising=False)
    _refuse_instancing(monkeypatch)
    _digest, payload = font_material(NOTO, 700)
    text = DECK_TEXT + BASELINE_TEXT
    for weight in WEIGHTS:
        assert svg_pptx_font_subset._static_instance(payload, weight) == runtime_instances[weight]
        # The subset face is the one export-time instancing would have embedded.
        expected = subset_face(runtime_instances[weight], weight=weight, text=text)
        assert subset_face(payload, weight=weight, text=text) == expected
    bold = subset_face(payload, weight=700, text=text)
    assert len(bold) < len(payload) // 10


def test_tampered_prebuilt_instance_fails_closed(tmp_path, monkeypatch, runtime_instances):
    monkeypatch.setattr(svg_pptx_font_subset, "PREBUILT_INSTANCE_DIRECTORY", tmp_path)
    _refuse_instancing(monkeypatch)
    svg_pptx_font_subset.prebuilt_instance_path(NOTO_DIGEST, 700).write_bytes(
        runtime_instances[700] + b"\0")
    _digest, payload = font_material(NOTO, 700)
    with pytest.raises(ValueError, match="svg_pptx_font_instance_digest_mismatch"):
        svg_pptx_font_subset._static_instance(payload, 700)


def test_missing_prebuilt_instance_falls_back_to_the_app_data_cache(tmp_path, monkeypatch, runtime_instances):
    monkeypatch.setattr(svg_pptx_font_subset, "PREBUILT_INSTANCE_DIRECTORY", tmp_path / "not-built")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    calls = []

    def instantiate(_payload, weight):
        calls.append(weight)
        return runtime_instances[weight]

    monkeypatch.setattr(svg_pptx_font_subset, "instantiate_static_face", instantiate)
    _digest, payload = font_material(NOTO, 700)
    assert svg_pptx_font_subset._static_instance(payload, 700) == runtime_instances[700]
    assert calls == [700]
    # The on-disk instance cache serves the next process the same bytes.
    assert list((tmp_path / "cache").rglob("*.ttf"))
    svg_pptx_font_subset._cached_static_instance.cache_clear()
    assert svg_pptx_font_subset._static_instance(payload, 700) == runtime_instances[700]
    assert calls == [700]


def test_build_script_writes_exactly_the_pinned_instances(tmp_path, monkeypatch, runtime_instances):
    calls = []

    def instantiate(payload, weight):
        assert sha256_prefixed(payload) == NOTO_DIGEST
        calls.append(weight)
        return runtime_instances[weight]

    monkeypatch.setattr(svg_pptx_font_subset, "instantiate_static_face", instantiate)
    written = instance_build.build(tmp_path)
    assert calls == list(WEIGHTS)
    assert {path.name: path.read_bytes() for path in written} == {
        svg_pptx_font_subset.prebuilt_instance_path(NOTO_DIGEST, weight).name: runtime_instances[weight]
        for weight in WEIGHTS
    }
    # Rebuilding keeps verified instances without instancing again.
    assert instance_build.build(tmp_path) == written
    assert calls == list(WEIGHTS)


def test_build_script_refuses_bytes_that_differ_from_the_pins(tmp_path, monkeypatch, runtime_instances):
    monkeypatch.setattr(svg_pptx_font_subset, "instantiate_static_face",
                        lambda _payload, weight: runtime_instances[weight] + b"\0")
    with pytest.raises(instance_build.FontInstanceBuildError, match="pinned"):
        instance_build.build(tmp_path)
    assert not list(tmp_path.glob("*.ttf"))


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


def test_font_normalization_leaves_unparseable_model_output_to_validation():
    """A real run's malformed author output crashed the deck here as internal_defect."""
    from services.svg_default_fonts import normalize_model_font_families

    assert normalize_model_font_families("```svg\n<svg") == ("```svg\n<svg", [])
