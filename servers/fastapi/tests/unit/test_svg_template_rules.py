from dataclasses import dataclass

import pytest

from utils.svg_style_adapter import adapt_svg_style
from utils.svg_template_fonts import (FONT_DIGEST, FONT_FAMILY, font_material,
    measure_font, require_font_coverage, source_font_profile)
from utils.svg_template_rules import resolve_source_template_style, template_snapshot
from services.svg_renderer import materialize_trusted_svg_fonts, svg_preview_html


@pytest.mark.parametrize("style_id", ["default", "osb-smart-banking", "screenshot-blue"])
@pytest.mark.parametrize("canvas", [(1280, 905), (1188, 840), (840, 1188)])
def test_actual_selected_catalogue_snapshot_has_layout_rules_and_font_bytes(style_id, canvas):
    style = resolve_source_template_style(style_id)
    width, height = canvas
    spec = adapt_svg_style(style, width=width, height=height, source_template=True)
    snapshot = spec["source_template"]
    assert spec["canvas"] == snapshot["canvas"] == {"width": width, "height": height}
    assert spec["unresolved"] == []
    assert snapshot["style_id"] == style_id
    assert snapshot["fonts"]["heading"]["family"] == "Noto Sans KR"
    assert snapshot["fonts"]["heading"]["weight"] > snapshot["fonts"]["body"]["weight"]
    if style_id == "screenshot-blue" and height > width:
        assert len(snapshot["layouts"]) == 3
        assert max(layout["columns"] for layout in snapshot["layouts"]) == 2
    else:
        assert len(snapshot["layouts"]) == 4
        comparison = next(layout for layout in snapshot["layouts"] if layout["id"] == "content-comparison-3")
        assert comparison["applicability"]["requires_source_parallel_groups"] is True
        assert comparison["applicability"]["minimum_text_blocks_per_group"] == 3
        assert comparison["intra_group_gap"] < comparison["min_gap"]
        assert comparison["allowed_variations"]["requires_same_source_parallel_group"] is True
        assert comparison["title_box"]["y"] + comparison["title_box"]["height"] < comparison["body_box"]["y"]
        assert comparison["body_box"]["y"] < snapshot["layouts"][0]["body_box"]["y"]
    if style_id == "screenshot-blue":
        assert snapshot["tokens"]["accent"] == "#0866FF"
        assert snapshot["tokens"]["heading"] == "#081D49"
        assert snapshot["tokens"]["background"] == "#FFFFFF"
        assert spec["overrides"]["common_guidance"]
        assert spec["overrides"]["layout_hints"]
        for layout in snapshot["layouts"]:
            for box_name in ("title_box", "body_box"):
                box = layout[box_name]
                assert box["x"] >= 0 and box["y"] >= 0
                assert box["x"] + box["width"] <= width
                assert box["y"] + box["height"] <= height
    if style_id == "default":
        assert snapshot["shape"]["border"] == snapshot["tokens"]["border"]
    assert all(layout["allowed_variations"]["min_gap"] > 0 for layout in snapshot["layouts"])
    assert snapshot["decorations"][0]["fill"] == snapshot["tokens"]["accent"]
    assert snapshot["provenance"]["asset_digests"]
    assert source_font_profile(spec).endswith(FONT_DIGEST)
    # Cached rules are not mutable shared authority, and canvas invalidates it.
    snapshot["layouts"][0]["body_box"]["width"] = 0
    assert template_snapshot(spec, width, height)["layouts"][0]["body_box"]["width"] > 0
    assert template_snapshot(spec, width, height - 1)["digest"] != snapshot["digest"]


def test_unsupported_selection_and_private_same_id_do_not_use_default():
    with pytest.raises(ValueError, match="template_unsupported"):
        resolve_source_template_style("unknown-style")
    spec = adapt_svg_style(resolve_source_template_style("default"))
    spec["provenance"]["registry_authority"] = "private"
    with pytest.raises(ValueError, match="authority_unsupported"):
        template_snapshot(spec, 1280, 905)


def test_real_font_bytes_have_hangul_coverage_and_weighted_measurements():
    digest, payload = font_material(FONT_FAMILY, 800)
    assert digest == FONT_DIGEST and len(payload) > 1000
    require_font_coverage("한글 금융 proposal 123")
    regular = measure_font(FONT_FAMILY, 400, 20)
    bold = measure_font(FONT_FAMILY, 800, 20)
    assert regular.getlength("한글 proposal") > 0
    assert regular.getlength("proposal") != bold.getlength("proposal")
    assert measure_font(FONT_FAMILY, 400, 20) is regular
    with pytest.raises(ValueError, match="font_unsupported"):
        font_material("https://example.invalid/font.ttf")
    with pytest.raises(ValueError, match="glyph_unavailable"):
        require_font_coverage("\U0010ffff")


@dataclass(frozen=True)
class Admitted:
    html: str
    asset_digests: tuple = ()
    node_count: int = 3


def test_only_pinned_template_wrapper_materializes_one_fixed_variable_face():
    spec = adapt_svg_style(resolve_source_template_style("default"), source_template=True)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#FFFFFF"/><text x="40" y="60" font-family="Noto Sans KR" font-size="24" font-weight="800" fill="#0F172A">한글</text></svg>'
    profile = source_font_profile(spec)
    wrapper = svg_preview_html(svg, source_font_profile=profile)
    admitted = Admitted('<html><head><meta content="font-src &#x27;none&#x27;"></head><body></body></html>')
    materialized = materialize_trusted_svg_fonts(wrapper, admitted)
    assert materialized.html.count("data:font/ttf;base64,") == 1
    assert "font-weight:100 900" in materialized.html
    assert materialized.asset_digests == (FONT_DIGEST,)
    assert "svg-template-noto" not in svg_preview_html(svg)
    with pytest.raises(ValueError, match="profile_invalid"):
        materialize_trusted_svg_fonts(wrapper.replace(FONT_DIGEST, "sha256:forged"), admitted)
    with pytest.raises(ValueError, match="profile_invalid"):
        materialize_trusted_svg_fonts(wrapper + "<style>@font-face{}</style>", admitted)
