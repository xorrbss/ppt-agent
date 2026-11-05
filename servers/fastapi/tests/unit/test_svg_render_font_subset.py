"""Per-page render subsets keep the bundled faces' glyphs, layout and metrics."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from fontTools.ttLib import TTFont

from services import svg_default_fonts, svg_render_font_subset
from services.svg_render_font_subset import (
    SUBSET_REVISION,
    SubsetFace,
    charset_digest,
    render_charset,
    subset_face,
)
from services.svg_renderer import svg_preview_html
from utils.renderer_isolation import admit_authored_html, admit_static_output_html
from utils.svg_template_fonts import FONT_DIGEST, font_material

SMOKE = Path(__file__).resolve().parents[1] / "fixtures" / "svg_smoke"


def _admit(wrapper: str):
    try:
        return admit_authored_html(wrapper)
    except Exception:
        return admit_static_output_html(wrapper)


@pytest.fixture(scope="module")
def noto_subset():
    svg = (SMOKE / "slide-5.svg").read_text(encoding="utf-8")
    charset = render_charset(ET.fromstring(svg))
    _digest, payload = font_material("Noto Sans KR")
    face = SubsetFace("Noto Sans KR", "100 900", "wght:100-900", FONT_DIGEST)
    return charset, payload, subset_face(face, payload, charset)


def test_charset_covers_text_normal_forms_and_the_space_advance():
    root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg"><text>é가 a‑b</text></svg>')
    charset = render_charset(root)
    assert {"é", "e", "́", "가", "ᄀ", "ᅡ", " ", "‑", "‐"} <= set(charset)
    assert list(charset) == sorted(set(charset))
    assert charset_digest(charset).startswith("sha256:")


def test_subset_keeps_outlines_variations_metrics_and_layout(noto_subset):
    charset, payload, subset = noto_subset
    assert len(subset) < len(payload) // 50
    with TTFont(BytesIO(payload), lazy=True) as full, TTFont(BytesIO(subset)) as part:
        assert [(a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in part["fvar"].axes] == [
            (a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in full["fvar"].axes]
        for table, fields in {
            "head": ("unitsPerEm", "xMin", "yMin", "xMax", "yMax"),
            "hhea": ("ascent", "descent", "lineGap"),
            "OS/2": ("sTypoAscender", "sTypoDescender", "sTypoLineGap", "usWinAscent", "usWinDescent",
                     "fsSelection", "usWeightClass", "fsType"),
        }.items():
            assert {name: getattr(part[table], name) for name in fields} == {
                name: getattr(full[table], name) for name in fields}, table
        assert set(full.keys()) == set(part.keys())
        for table in ("GSUB", "GPOS"):
            # Only features left without any lookup on the kept glyphs are pruned.
            assert _features(part, table) <= _features(full, table), table
            assert _scripts(part, table) == _scripts(full, table), table
        assert {"locl", "ccmp", "calt"} <= _features(part, "GSUB")
        assert "kern" in _features(part, "GPOS")
        full_cmap, part_cmap = full.getBestCmap(), part.getBestCmap()
        covered = [ord(c) for c in charset if ord(c) in full_cmap]
        assert covered and all(code in part_cmap for code in covered)
        full_glyf, part_glyf = full["glyf"], part["glyf"]
        full_gvar, part_gvar = full["gvar"].variations, part["gvar"].variations
        for code in covered:
            source, kept = full_cmap[code], part_cmap[code]
            assert full["hmtx"][source] == part["hmtx"][kept], chr(code)
            assert full["vmtx"][source] == part["vmtx"][kept], chr(code)
            source_coordinates = full_glyf[source].getCoordinates(full_glyf)
            kept_coordinates = part_glyf[kept].getCoordinates(part_glyf)
            assert list(source_coordinates[0]) == list(kept_coordinates[0]), chr(code)
            assert list(source_coordinates[1]) == list(kept_coordinates[1]), chr(code)
            assert list(source_coordinates[2]) == list(kept_coordinates[2]), chr(code)
            assert [(v.axes, v.coordinates) for v in full_gvar.get(source, [])] == [
                (v.axes, v.coordinates) for v in part_gvar.get(kept, [])], chr(code)


def _features(font, table):
    return {record.FeatureTag for record in font[table].table.FeatureList.FeatureRecord}


def _scripts(font, table):
    return {record.ScriptTag for record in font[table].table.ScriptList.ScriptRecord}


def test_conjoining_jamo_keep_the_korean_jamo_features():
    root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg"><text>각 가</text></svg>')
    _digest, payload = font_material("Noto Sans KR")
    face = SubsetFace("Noto Sans KR", "100 900", "wght:100-900", FONT_DIGEST)
    with TTFont(BytesIO(subset_face(face, payload, render_charset(root)))) as part:
        assert {"ljmo", "vjmo", "tjmo", "locl"} <= _features(part, "GSUB")


def test_subset_is_deterministic_and_cached(noto_subset):
    charset, payload, subset = noto_subset
    face = SubsetFace("Noto Sans KR", "100 900", "wght:100-900", FONT_DIGEST)
    assert subset_face(face, payload, charset) is subset
    svg_render_font_subset.clear_subset_cache()
    assert subset_face(face, payload, charset) == subset


def test_default_profile_embeds_the_page_subset_and_records_provenance():
    svg = (SMOKE / "slide-3.svg").read_text(encoding="utf-8")
    wrapper = svg_preview_html(svg)
    admitted = _admit(wrapper)
    materialized = svg_default_fonts.materialize_default_fonts(wrapper, admitted)
    assert "font-src data:" in materialized.html
    assert len(materialized.html) < 512 * 1024
    assert FONT_DIGEST in materialized.asset_digests
    # The parent validates without materializing and keeps the source identity.
    validated = svg_default_fonts.validate_default_fonts(wrapper, admitted)
    assert validated is admitted and validated.source_digest == materialized.source_digest
    with pytest.raises(ValueError, match="svg_default_font_profile_invalid"):
        svg_default_fonts.validate_default_fonts(wrapper.replace("</head>", "<style>@font-face{}</style></head>"),
                                                 admitted)
    provenance = svg_default_fonts.default_font_subset_provenance(svg)
    assert provenance["revision"] == SUBSET_REVISION
    assert provenance["subsetter"].startswith("fontTools/")
    assert provenance["charset_digest"] == charset_digest(render_charset(ET.fromstring(svg)))
    assert provenance["faces"] == [{"family": "Noto Sans KR", "weight": "100 900", "axes": "wght:100-900",
                                    "source_digest": FONT_DIGEST}]
