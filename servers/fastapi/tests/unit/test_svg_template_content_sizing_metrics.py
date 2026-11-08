"""Synthetic bounds/proxy contracts; no renderer, provider, database, or fixtures."""
import importlib.util
import json
from pathlib import Path

import pytest


_PATH = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval" / "content_sizing_metrics.py"
_SPEC = importlib.util.spec_from_file_location("svg_template_content_sizing_metrics", _PATH)
metrics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(metrics)


def _svg(body, *, dimensions='viewBox="0 0 1000 800"'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" {dimensions}>{body}</svg>'


def _card(*, width=400, height=300, text='Short', x=100, y=100):
    return (f'<g id="example-card"><rect x="{x}" y="{y}" width="{width}" height="{height}" fill="white"/>'
            f'<text x="{x + 20}" y="{y + 40}" font-size="20">{text}</text></g>')


def test_revisioned_proxy_distinguishes_card_interior_from_page_margin_and_frame():
    body = _card()
    frame = '<g id="svg-system-template-background"><rect width="1000" height="800"/></g>'
    small_margin = metrics.svg_content_sizing(_svg(frame + body), body_bounds=(80, 80, 500, 400))
    large_margin = metrics.svg_content_sizing(_svg(frame + body))
    assert small_margin["status"] == large_margin["status"] == "estimated"
    assert small_margin["card_unused_envelope_share"] == large_margin["card_unused_envelope_share"]
    assert small_margin["context"]["card_footprint_share"] > large_margin["context"]["card_footprint_share"]
    assert small_margin["policy_revision"] == metrics.CONTENT_SIZING_POLICY_REVISION
    assert len(small_margin["limitations"]) == 3
    json.dumps(small_margin, allow_nan=False)


def test_empty_explicit_card_is_evidence_but_unmarked_rectangle_is_not_a_card():
    explicit = metrics.svg_content_sizing(_svg('<rect id="empty-card" x="100" y="100" width="300" height="200"/>'))
    assert explicit["cards"][0]["unused_envelope_share"] == 1
    assert explicit["cards"][0]["content_count"] == 0
    plain = metrics.svg_content_sizing(_svg('<rect x="100" y="100" width="300" height="200"/>'))
    assert plain["candidate_count"] == 0
    assert plain["status"] == "unavailable"
    assert plain["card_unused_envelope_share"] is None


def test_two_contained_texts_support_unmarked_group_but_a_chart_bar_with_one_label_does_not():
    backing = '<rect x="100" y="100" width="400" height="300"/>'
    first = '<text x="120" y="140" font-size="20">Heading</text>'
    second = '<text x="120" y="190" font-size="16">Detail</text>'
    result = metrics.svg_content_sizing(_svg(f'<g>{backing}{first}{second}</g>'))
    assert result["cards"][0]["candidate_evidence"] == "group_backing_rect"
    assert result["available_card_count"] == 1
    bar = metrics.svg_content_sizing(_svg(f'<g>{backing}{first}</g>'))
    assert bar["candidate_count"] == 0


def test_smaller_card_improves_internal_proxy_without_increasing_body_content_extent():
    before = metrics.svg_content_sizing(_svg(_card()))
    after = metrics.svg_content_sizing(_svg(_card(width=250, height=150)))
    assert after["card_unused_envelope_share"] < before["card_unused_envelope_share"]
    assert after["context"]["card_footprint_share"] < before["context"]["card_footprint_share"]
    assert after["context"]["content_envelope_share"] == before["context"]["content_envelope_share"]


def test_content_proxy_crossing_card_boundary_is_unavailable():
    result = metrics.svg_content_sizing(_svg(_card(width=100, text="Text cannot fit")))
    assert result["available_card_count"] == 0
    assert result["card_unused_envelope_share"] is None
    assert "content_crosses_card_boundary" in result["cards"][0]["reasons"]


def test_nested_cards_are_ambiguous_and_not_double_counted():
    result = metrics.svg_content_sizing(_svg(
        '<g id="outer-card"><rect x="100" y="100" width="500" height="400"/>'
        + _card(width=200, height=100, x=150, y=150) + '</g>'))
    assert result["candidate_count"] == 2
    assert result["available_card_count"] == 0
    assert all("overlapping_or_nested_cards" in card["reasons"] for card in result["cards"])
    assert result["context"]["card_footprint_share"] is None


@pytest.mark.parametrize("content", [
    '<g transform="translate(10 0)">' + _card() + '</g>',
    _card().replace('<text ', '<text transform="rotate(5)" '),
    _card().replace('</g>', '<path d="M120 120 L150 150"/></g>'),
    _card().replace('<text ', '<text style="letter-spacing:3px" '),
    '<g letter-spacing="3">' + _card() + '</g>',
    _card().replace('<rect ', '<rect clip-path="url(#clip)" '),
])
def test_unsupported_geometry_and_styles_never_become_empty_card_evidence(content):
    result = metrics.svg_content_sizing(_svg(content))
    assert result["available_card_count"] == 0
    assert result["card_unused_envelope_share"] is None
    assert result["context"]["content_envelope_share"] is None
    assert result["reasons"]


def test_stylesheet_and_missing_dimensions_are_explicitly_unavailable():
    styled = metrics.svg_content_sizing(_svg('<style>text { display:none }</style>' + _card()))
    assert styled["reasons"] == ["stylesheet_not_resolved"]
    no_size = metrics.svg_content_sizing(_svg(_card(), dimensions=""))
    assert no_size["reasons"] == ["missing_or_invalid_page_or_body_bounds"]
    assert no_size["context"]["page_bounds"] is None


def test_page_user_coordinates_and_explicit_body_context_are_retained():
    result = metrics.svg_content_sizing(
        _svg(_card(x=200, y=200), dimensions='width="1000px" height="800px" viewBox="100 100 1000 800"'),
        body_bounds={"x": 150, "y": 150, "width": 700, "height": 600})
    assert result["context"]["page_bounds"] == (100, 100, 1000, 800)
    assert result["context"]["body_bounds"] == (150, 150, 700, 600)
    assert result["context"]["body_bounds_source"] == "explicit"
    invalid = metrics.svg_content_sizing(_svg(_card()), body_bounds=(-1, 0, 1000, 800))
    assert invalid["status"] == "unavailable"


def test_positioned_tspans_are_proxy_fragments_and_frame_text_is_excluded():
    result = metrics.svg_content_sizing(_svg(
        '<g id="svg-system-template-foreground"><text x="0" y="5">Frame</text></g>'
        '<g id="two-line-card"><rect x="100" y="100" width="400" height="300"/>'
        '<text x="120" y="140" font-size="20"><tspan>First</tspan>'
        '<tspan x="120" dy="30">Next</tspan></text></g>'))
    assert result["available_card_count"] == 1
    assert result["cards"][0]["content_count"] == 2
    assert result["context"]["content_envelope_bounds"] == (120, 120, 120, 56)


def test_malformed_and_non_svg_documents_return_no_fabricated_ratio():
    for source in ("<svg", "<div/>"):
        result = metrics.svg_content_sizing(source)
        assert result["status"] == "unavailable"
        assert result["card_unused_envelope_share"] is None


@pytest.mark.parametrize("attributes", [
    'opacity="0.0"', 'opacity="0%"', 'fill-opacity="0"', 'fill="none"', 'fill="transparent"',
])
def test_invisible_text_is_not_counted_as_card_content(attributes):
    result = metrics.svg_content_sizing(_svg(
        '<g id="hidden-text-card"><rect x="100" y="100" width="400" height="300" fill="white"/>'
        f'<text x="120" y="140" font-size="20" {attributes}>Hidden</text></g>'))
    assert result["cards"][0]["content_count"] == 0
    assert result["cards"][0]["unused_envelope_share"] == 1
    assert result["context"]["content_envelope_share"] == 0


def test_nested_svg_viewport_is_unavailable_instead_of_using_untransformed_positions():
    result = metrics.svg_content_sizing(_svg(
        '<svg x="500" y="200" viewBox="0 0 500 400">' + _card() + '</svg>'))
    assert result["available_card_count"] == 0
    assert "nested_svg_viewport_unsupported" in result["reasons"]
    assert result["context"]["content_envelope_share"] is None


def test_external_text_does_not_make_a_sidebar_a_card_and_explicit_ownership_is_uncertain():
    contents = ('<rect x="100" y="100" width="200" height="300"/>'
                '<text x="120" y="140" font-size="20">Inside</text>'
                '<text x="500" y="140" font-size="20">Outside</text>')
    unmarked = metrics.svg_content_sizing(_svg('<g>' + contents + '</g>'))
    assert unmarked["candidate_count"] == 0
    explicit = metrics.svg_content_sizing(_svg('<g id="explicit-card">' + contents + '</g>'))
    assert explicit["available_card_count"] == 0
    assert "owned_content_outside_card" in explicit["cards"][0]["reasons"]


def test_rounded_card_and_mixed_availability_do_not_report_an_all_card_scalar():
    result = metrics.svg_content_sizing(_svg(
        _card(width=250, height=150)
        + _card(x=550, width=250, height=150).replace('<rect ', '<rect rx="20" ')))
    assert result["candidate_count"] == 2
    assert result["available_card_count"] == 1
    assert result["status"] == "partial"
    assert result["card_unused_envelope_share"] is None
    assert result["context"]["card_footprint_share"] is None
    assert "rounded_card_geometry_unsupported" in result["cards"][1]["reasons"]


@pytest.mark.parametrize("text", [
    '<text x="120" y="140" font-size="20" fill="none">Hidden<tspan fill="black">Visible</tspan></text>',
    '<text x="120" y="140" font-size="20"><tspan opacity="0">Hidden</tspan>Visible tail</text>',
])
def test_structured_text_with_invisible_fragments_is_unavailable(text):
    result = metrics.svg_content_sizing(_svg(
        '<g id="visible-child-card"><rect x="100" y="100" width="400" height="300"/>' + text + '</g>'))
    assert result["available_card_count"] == 0
    assert result["cards"][0]["unused_envelope_share"] is None
    assert "structured_text_paint_not_resolved" in result["reasons"]


def test_negative_stroke_width_never_expands_card_interior():
    result = metrics.svg_content_sizing(_svg(_card().replace('<rect ', '<rect stroke="black" stroke-width="-2" ')))
    assert result["available_card_count"] == 0
    assert "unsupported_card_geometry" in result["cards"][0]["reasons"]


@pytest.mark.parametrize("sibling", [
    '<path d="M100 100 L500 400" stroke="black"/>',
    '<use href="#unknown"/>',
    '<image x="120" y="120" width="100" height="80" href="example.png"/>',
])
def test_external_unresolved_or_overlapping_geometry_suppresses_card_scalar(sibling):
    result = metrics.svg_content_sizing(_svg(_card() + sibling))
    assert result["available_card_count"] == 0
    assert result["card_unused_envelope_share"] is None
    assert result["cards"][0]["bounds"] is not None
    assert result["cards"][0]["reasons"]


@pytest.mark.parametrize("dimensions", [
    'viewBox="0 0 1e200 1e200"',
    'viewBox="1e308 0 1e308 1"',
    'viewBox="0 0 1e-200 1e-200"',
])
def test_finite_page_components_with_invalid_derived_geometry_are_unavailable(dimensions):
    result = metrics.svg_content_sizing(_svg('', dimensions=dimensions))
    assert result["status"] == "unavailable"
    assert result["context"]["page_bounds"] is None
    assert result["card_unused_envelope_share"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("width,height", [
    ("1e-200", "1e-200"), ("1e200", "1e200"),
])
def test_extreme_finite_empty_card_dimensions_never_divide_by_zero_or_emit_infinity(width, height):
    result = metrics.svg_content_sizing(_svg(
        f'<rect id="extreme-card" x="0" y="0" width="{width}" height="{height}"/>'))
    assert result["available_card_count"] == 0
    assert result["card_unused_envelope_share"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("size", ["1e308", "1e-200"])
def test_text_proxy_overflow_and_area_underflow_are_unavailable(size):
    result = metrics.svg_content_sizing(_svg(_card().replace('font-size="20"', f'font-size="{size}"')))
    assert result["available_card_count"] == 0
    assert "invalid_text_proxy_bounds" in result["reasons"]
    assert result["context"]["content_envelope_share"] is None
    json.dumps(result, allow_nan=False)
