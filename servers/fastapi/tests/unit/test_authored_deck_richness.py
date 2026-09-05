"""Unit tests for the authored-deck-richness measurement CLI.

No Chromium anywhere: the chart/table/library detectors and the
bottom_dead_band arithmetic are pure-HTML/pure-arithmetic functions, and the
renderer boundary (``authored_deck_richness_render.render_slide_metrics``) is
monkeypatched wherever ``measure_slide`` is exercised.
"""

from __future__ import annotations

import pytest

from scripts.authored_deck_richness import measure_slide
from scripts.authored_deck_richness_geometry import compute_bottom_dead_band
from scripts.authored_deck_richness_html import parse_structural_metrics
from scripts.authored_deck_richness_render import RenderOutcome
from scripts.authored_deck_richness_report import SlideMetrics, rollup_deck
from scripts.authored_deck_richness_sources import SourceSlide, matches_id_prefix
from utils.authored_infographic.library import LIBRARY_MARKER


# ---------------------------------------------------------------------------
# Chart detector
# ---------------------------------------------------------------------------


def test_chart_detector_positive_svg_with_two_text_nodes():
    html = (
        "<div><svg><path d='M0 0 L10 10'/><text>10</text><text>20</text></svg></div>"
    )
    metrics = parse_structural_metrics(html)
    assert metrics.has_chart is True
    assert "svg_chart" in metrics.chart_evidence


def test_chart_detector_negative_svg_needs_two_text_nodes():
    # One shape, only one text node: below the "axis + value" threshold.
    html = "<svg><rect x='0' y='0' width='10' height='10'/><text>10</text></svg>"
    metrics = parse_structural_metrics(html)
    assert "svg_chart" not in metrics.chart_evidence


def test_chart_detector_positive_canvas():
    html = "<div><canvas id='plot1'></canvas></div>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_chart is True
    assert metrics.chart_evidence == ("canvas",)


def test_chart_detector_positive_keyword_class():
    html = "<div class='legend-box'>Q1: 20%</div>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_chart is True
    assert "keyword:legend" in metrics.chart_evidence


def test_chart_detector_negative_bar_shaped_div():
    # A colored div meant to look like a bar chart, but with no svg/canvas and
    # no matching class/id keyword: must NOT count as a real chart.
    html = "<div class='stat-block' style='height:120px;background:#3366ff'></div>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_chart is False
    assert metrics.chart_evidence == ()


def test_chart_detector_keyword_does_not_match_substring():
    # "infographic"/"photograph" contain "graph" mid-word; the word-boundary
    # rule must not treat that as chart evidence.
    html = "<div class='infographic-photograph-panel'>content</div>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_chart is False


# ---------------------------------------------------------------------------
# Table detector
# ---------------------------------------------------------------------------


def test_table_detector_positive_with_thead():
    html = "<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_table is True


def test_table_detector_positive_two_rows_no_thead():
    html = "<table><tr><td>1</td></tr><tr><td>2</td></tr></table>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_table is True


def test_table_detector_negative_single_row_layout_table():
    html = "<table><tr><td>only one row</td></tr></table>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_table is False


def test_table_detector_negative_no_table_at_all():
    html = "<div>no table here</div>"
    metrics = parse_structural_metrics(html)
    assert metrics.has_table is False


# ---------------------------------------------------------------------------
# Library detector
# ---------------------------------------------------------------------------


def test_library_detector_positive():
    html = f"<html><head><style>{LIBRARY_MARKER}</style></head><body></body></html>"
    metrics = parse_structural_metrics(html)
    assert metrics.uses_library is True


def test_library_detector_negative():
    html = "<html><head><style>.a{color:red}</style></head><body></body></html>"
    metrics = parse_structural_metrics(html)
    assert metrics.uses_library is False


# ---------------------------------------------------------------------------
# css_rule_count
# ---------------------------------------------------------------------------


def test_css_rule_count_subtracts_container_at_rule_brace():
    html = (
        "<style>@media (max-width: 600px){ .a{color:red} .b{color:blue} }</style>"
    )
    metrics = parse_structural_metrics(html)
    # Three '{' total (@media, .a, .b) minus one container at-rule brace = 2.
    assert metrics.css_rule_count == 2


def test_css_rule_count_plain_rules_no_at_rule():
    html = "<style>.a{color:red} .b{color:blue}</style>"
    metrics = parse_structural_metrics(html)
    assert metrics.css_rule_count == 2


# ---------------------------------------------------------------------------
# inline image bytes / image count
# ---------------------------------------------------------------------------


def test_image_count_and_inline_bytes():
    # base64 of b"hi" is "aGk=" (2 decoded bytes).
    html = (
        "<img src='data:image/png;base64,aGk='>"
        "<img src='https://example.com/not-inline.png'>"
    )
    metrics = parse_structural_metrics(html)
    assert metrics.image_count == 2
    assert metrics.inline_image_bytes == 2


# ---------------------------------------------------------------------------
# bottom_dead_band arithmetic
# ---------------------------------------------------------------------------


def test_bottom_dead_band_with_owned_content_near_top():
    payload = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {
                "qid": "a",
                "tag": "div",
                "has_owned_text": True,
                "bbox": {"x": 0, "y": 600, "width": 200, "height": 60},
            }
        ],
    }
    # Lowest content bottom is 660; dead band is (720-660)/720.
    assert compute_bottom_dead_band(payload) == pytest.approx(0.0833, abs=1e-4)


def test_bottom_dead_band_no_owned_content_is_fully_dead():
    payload = {"viewport": {"width": 1280, "height": 720}, "elements": []}
    assert compute_bottom_dead_band(payload) == 1.0


def test_bottom_dead_band_unowned_element_is_excluded():
    payload = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {
                "qid": "a",
                "tag": "div",
                "has_owned_text": False,
                "bbox": {"x": 0, "y": 700, "width": 200, "height": 20},
            }
        ],
    }
    assert compute_bottom_dead_band(payload) == 1.0


def test_bottom_dead_band_legacy_payload_without_ownership_flag_counts():
    payload = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {"qid": "a", "tag": "div", "bbox": {"x": 0, "y": 0, "width": 100, "height": 720}}
        ],
    }
    assert compute_bottom_dead_band(payload) == 0.0


def test_bottom_dead_band_content_fills_full_canvas():
    payload = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {
                "qid": "a",
                "tag": "img",
                "has_owned_text": False,
                "bbox": {"x": 0, "y": 0, "width": 1280, "height": 720},
            }
        ],
    }
    assert compute_bottom_dead_band(payload) == 0.0


# ---------------------------------------------------------------------------
# id prefix matching
# ---------------------------------------------------------------------------


def test_matches_id_prefix_short_prefix():
    assert matches_id_prefix("2c89713d-1111-2222-3333-444444444444", "2c89713d")


def test_matches_id_prefix_full_uuid_must_match_exactly():
    full = "2c89713d-1111-2222-3333-444444444444"
    assert matches_id_prefix(full, full)
    assert not matches_id_prefix(full, "2c89713d-1111-2222-3333-444444444445")


def test_matches_id_prefix_rejects_unrelated_value():
    assert not matches_id_prefix("2c89713d-1111-2222-3333-444444444444", "abcdef12")


# ---------------------------------------------------------------------------
# Deck roll-up arithmetic
# ---------------------------------------------------------------------------


def _slide(**overrides) -> SlideMetrics:
    defaults = dict(
        deck_id="deck-1",
        deck_label="Deck One",
        source="database",
        slide_index=0,
        html_bytes=1000,
        uses_library=False,
        image_count=0,
        inline_image_bytes=0,
        has_chart=False,
        chart_evidence=(),
        has_table=False,
        css_rule_count=0,
        distinct_block_count=10,
        max_nesting_depth=3,
        structure_source="html_fallback",
        ink_fill=0.5,
        bottom_dead_band=0.1,
        geometry_defects={"total": 0, "by_category": {}, "by_severity": {}},
        telemetry_status="available",
        render_error=None,
    )
    defaults.update(overrides)
    return SlideMetrics(**defaults)


def test_rollup_deck_means_and_shares():
    slides = [
        _slide(html_bytes=1000, has_chart=True, image_count=1, geometry_defects={"total": 2}),
        _slide(html_bytes=2000, has_chart=False, image_count=0, geometry_defects={"total": 0}),
    ]
    result = rollup_deck(slides)
    assert result["mean_html_bytes"] == 1500.0
    assert result["median_html_bytes"] == 1500.0
    assert result["share_has_chart"] == 0.5
    assert result["share_with_image"] == 0.5
    assert result["total_geometry_defects"] == 2
    assert result["mean_geometry_defects"] == 1.0


def test_rollup_deck_empty_slides_is_all_none():
    result = rollup_deck([])
    assert result["mean_html_bytes"] is None
    assert result["share_has_chart"] is None
    assert result["total_geometry_defects"] == 0


def test_rollup_deck_counts_render_failures_and_telemetry_gaps():
    slides = [
        _slide(render_error="TimeoutError: boom", telemetry_status="unavailable"),
        _slide(render_error=None, telemetry_status="available"),
    ]
    result = rollup_deck(slides)
    assert result["render_failures"] == 1
    assert result["telemetry_unavailable"] == 1


# ---------------------------------------------------------------------------
# measure_slide: renderer mocked (no Chromium)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_measure_slide_uses_browser_metrics_structure_when_available(monkeypatch):
    fake_metrics = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {
                "qid": "root",
                "parent_qid": "",
                "tag": "div",
                "has_owned_text": True,
                "bbox": {"x": 0, "y": 0, "width": 1280, "height": 100},
            },
            {
                "qid": "child",
                "parent_qid": "root",
                "tag": "div",
                "has_owned_text": True,
                "bbox": {"x": 0, "y": 0, "width": 100, "height": 50},
            },
        ],
    }

    async def fake_render(html, *, timeout_s):
        return RenderOutcome(browser_metrics=fake_metrics, error=None)

    monkeypatch.setattr(
        "scripts.authored_deck_richness.render_slide_metrics", fake_render
    )
    source = SourceSlide(
        deck_id="deck-1", deck_label="Deck", source="database", slide_index=0,
        html="<div data-qid='root'><div data-qid='child'>hi</div></div>",
    )
    import asyncio

    result = await measure_slide(source, timeout_s=30, semaphore=asyncio.Semaphore(1))
    assert result.structure_source == "browser_metrics"
    assert result.distinct_block_count == 2
    assert result.max_nesting_depth == 2
    assert result.render_error is None


@pytest.mark.anyio
async def test_measure_slide_falls_back_to_html_parser_on_render_failure(monkeypatch):
    async def fake_render(html, *, timeout_s):
        return RenderOutcome(browser_metrics=None, error="RuntimeError: chrome unavailable")

    monkeypatch.setattr(
        "scripts.authored_deck_richness.render_slide_metrics", fake_render
    )
    source = SourceSlide(
        deck_id="deck-1", deck_label="Deck", source="database", slide_index=0,
        html="<div><p>hello</p><p>world</p></div>",
    )
    import asyncio

    result = await measure_slide(source, timeout_s=30, semaphore=asyncio.Semaphore(1))
    assert result.structure_source == "html_fallback"
    assert result.render_error == "RuntimeError: chrome unavailable"
    assert result.telemetry_status == "unavailable"
    assert result.distinct_block_count >= 2


@pytest.fixture
def anyio_backend():
    return "asyncio"
