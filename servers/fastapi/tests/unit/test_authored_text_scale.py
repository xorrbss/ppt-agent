"""Unit tests for the authored text-scale applier (design doc M2).

See docs/authored-fit-magnitude-measurement-design-20260904.md section 4.
"""

from __future__ import annotations

from utils.authored_infographic.library import inject_library_css, library_css
from utils.authored_text_scale import _DECL_RE, ScaledHtml, scale_authored_text


class _Brand:
    """Minimal stand-in for the deck Brand model tokens_for_brand reads."""

    primary = "#2563EB"
    fonts = "Noto Sans KR"


def test_inline_style_font_size_is_scaled() -> None:
    html = '<p style="font-size: 20px;">hi</p>'
    result = scale_authored_text(html, 0.9)

    assert result.html == '<p style="font-size: 18px;">hi</p>'
    assert result.scaled_declarations == 1
    assert result.floored_declarations == 0
    assert result.unscaled_declarations == 0
    assert result.skipped_figure_declarations == 0


def test_style_block_rule_is_scaled() -> None:
    html = "<html><head><style>.title{font-size:40px;line-height:48px}</style></head><body></body></html>"
    result = scale_authored_text(html, 0.5)

    assert ".title{font-size:20px;line-height:24px}" in result.html
    assert result.scaled_declarations == 2
    assert result.unscaled_declarations == 0
    assert result.skipped_figure_declarations == 0


def test_important_is_preserved() -> None:
    html = '<span style="font-size: 30px !important;">x</span>'
    result = scale_authored_text(html, 0.5)

    assert "font-size: 15px !important" in result.html
    assert result.scaled_declarations == 1


def test_style_block_ig_selector_rule_is_skipped() -> None:
    html = (
        "<html><head><style>"
        ".ig-label{font-size:26px}"
        ".body{font-size:40px}"
        "</style></head><body></body></html>"
    )
    result = scale_authored_text(html, 0.5)

    assert ".ig-label{font-size:26px}" in result.html
    assert ".body{font-size:20px}" in result.html
    assert result.skipped_figure_declarations == 1
    assert result.scaled_declarations == 1


def test_inline_style_on_ig_element_is_skipped() -> None:
    html = '<div class="ig n3"><span class="ig-label" style="font-size:26px">x</span></div>'
    result = scale_authored_text(html, 0.5)

    assert result.html == html
    assert result.skipped_figure_declarations == 1
    assert result.scaled_declarations == 0
    assert result.unscaled_declarations == 0


def test_library_stylesheet_is_skipped_in_full() -> None:
    """Uses the real library injection so the marker-based comparison is exercised."""
    base_html = (
        "<html><head><style>.title{font-size:40px}</style></head>"
        '<body><div class="ig n3"><span class="ig-label">a</span></div></body></html>'
    )
    css = library_css(_Brand())
    html_with_library = inject_library_css(base_html, css)

    # Sanity: the library block really landed once, and it carries its own
    # font-size/line-height declarations (.ig-label, .ig-num, ... at various
    # px sizes and the unitless line-height on .ig itself).
    assert html_with_library.count("<style>") == 2
    library_decl_count = len(list(_DECL_RE.finditer(css)))
    assert library_decl_count > 0

    result = scale_authored_text(html_with_library, 0.5)

    # The library block's CSS text is untouched, byte for byte.
    assert f"<style>{css}</style>" in result.html
    # The model's own rule outside the library is still scaled.
    assert ".title{font-size:20px}" in result.html
    # Every font-size/line-height declaration inside the library block is
    # counted as skipped, none as scaled/floored/unscaled.
    assert result.skipped_figure_declarations == library_decl_count
    assert result.scaled_declarations == 1
    assert result.unscaled_declarations == 0
    assert result.floored_declarations == 0


def test_floor_is_applied_and_counted() -> None:
    html = '<p style="font-size: 20px;">hi</p>'
    result = scale_authored_text(html, 0.5, floor_px=15.0)

    # 20 * 0.5 = 10, below the 15px floor -> clamped to the floor.
    assert result.html == '<p style="font-size: 15px;">hi</p>'
    assert result.floored_declarations == 1
    assert result.scaled_declarations == 0
    assert result.unscaled_declarations == 0


def test_below_floor_input_is_left_alone() -> None:
    html = '<p style="font-size: 12px;">hi</p>'
    result = scale_authored_text(html, 0.5, floor_px=15.0)

    assert result.html == html
    assert result.unscaled_declarations == 1
    assert result.scaled_declarations == 0
    assert result.floored_declarations == 0


def test_relative_units_are_counted_unscaled() -> None:
    html = (
        '<p style="font-size: 2em; line-height: 150%;">a</p>'
        '<p style="font-size: clamp(12px, 2vw, 20px);">b</p>'
        '<p style="line-height: 1.4;">c</p>'
    )
    result = scale_authored_text(html, 0.8)

    assert result.html == html
    assert result.unscaled_declarations == 4
    assert result.scaled_declarations == 0
    assert result.floored_declarations == 0
    assert result.skipped_figure_declarations == 0


def test_factor_one_is_identity() -> None:
    html = (
        '<p style="font-size: 20.0px;">a</p>'
        "<style>.x{font-size:30px}</style>"
    )
    result = scale_authored_text(html, 1.0)

    # Byte-for-byte identical input, even though 20.0px would reformat to
    # 20px if the rewrite were actually applied to the output.
    assert result.html == html
    assert result.scaled_declarations == 2


def test_realistic_authored_slide_mix() -> None:
    html = (
        "<html><head><style>"
        ".ig-label{font-size:26px}"
        "h1{font-size:48px;line-height:56px}"
        ".note{font-size:12px}"
        ".pct{font-size:2em}"
        "</style></head><body>"
        '<h1 style="font-size: 48px;">Title</h1>'
        '<p style="font-size:20px;line-height:28px">Body copy</p>'
        '<span style="font-size:12px">Fine print</span>'
        '<div class="ig n5"><span class="ig-num" style="font-size:38px">1</span></div>'
        "</body></html>"
    )
    result = scale_authored_text(html, 0.8, floor_px=15.0)

    # h1 rule (font-size + line-height) + h1 inline font-size
    # + p inline (font-size + line-height) = 5 scaled
    # (48*0.8=38.4, 56*0.8=44.8, 48*0.8=38.4, 20*0.8=16, 28*0.8=22.4 -- all >= floor)
    assert result.scaled_declarations == 5
    # .note (12px, already below floor) + .pct (2em) + span inline (12px) = 3 unscaled
    assert result.unscaled_declarations == 3
    # nothing scales down below the floor in this mix
    assert result.floored_declarations == 0
    # .ig-label rule + ig-num inline style = 2 skipped
    assert result.skipped_figure_declarations == 2
    assert result.scaled_declarations + result.unscaled_declarations + result.skipped_figure_declarations == 10

    assert "h1{font-size:38.4px;line-height:44.8px}" in result.html
    assert 'style="font-size: 38.4px;"' in result.html
    assert 'style="font-size:16px;line-height:22.4px"' in result.html
    assert ".note{font-size:12px}" in result.html
    assert ".pct{font-size:2em}" in result.html
    assert 'style="font-size:12px"' in result.html
    assert ".ig-label{font-size:26px}" in result.html
    assert '<span class="ig-num" style="font-size:38px">' in result.html


def test_scaled_html_is_frozen_dataclass() -> None:
    result = scale_authored_text("<p></p>", 1.0)
    assert isinstance(result, ScaledHtml)
    try:
        result.scaled_declarations = 5  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("ScaledHtml should be frozen")
