"""Undeclared bar/column charts (SVG_SERVER_CHARTS decks)."""
from __future__ import annotations

import json

from services.generation_core.drivers.svg import undeclared_chart
from services.generation_core.quality.page_failure_kind import LAYOUT, page_failure_kind

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'


def _bars(values, *, x=150, y=200, step=42, thickness=30, labels=True, wrap=None):
    parts = []
    for index, value in enumerate(values):
        top = y + index * step
        parts.append(f'<rect x="{x}" y="{top}" width="{value * 4}" height="{thickness}" fill="#2563EB"/>')
        if labels:
            parts.append(f'<text x="{x + value * 4 + 8}" y="{top + 20}" font-size="16" fill="#0F172A">{value}%</text>')
    body = "".join(parts)
    if wrap:
        body = f'<g id="{wrap}">{body}</g>'
    return HEAD + body + "</svg>"


def _columns(values, *, bottom=500, x=700, step=80, width=48):
    parts = []
    for index, value in enumerate(values):
        left = x + index * step
        parts.append(f'<rect x="{left}" y="{bottom - value * 3}" width="{width}" height="{value * 3}" fill="#94A3B8"/>')
        parts.append(f'<text x="{left + 24}" y="{bottom - value * 3 - 8}" font-size="16" text-anchor="middle" '
                     f'fill="#0F172A">{value}억</text>')
    return HEAD + "".join(parts) + "</svg>"


def test_a_drawn_bar_chart_with_value_labels_is_found():
    (found,) = undeclared_chart.undeclared_bar_charts(_bars([46, 22, 19, 13]))
    assert found["orientation"] == "bar" and found["bars"] == 4


def test_a_drawn_column_chart_is_found():
    (found,) = undeclared_chart.undeclared_bar_charts(_columns([38, 45, 52, 61]))
    assert found["orientation"] == "column"


def test_declared_and_server_groups_are_ignored():
    assert undeclared_chart.undeclared_bar_charts(_bars([46, 22, 19, 13], wrap="native-chart-cost")) == []
    assert undeclared_chart.undeclared_bar_charts(_bars([46, 22, 19, 13], wrap="svg-system-template-body")) == []


def test_equal_cards_unlabelled_bars_and_short_rows_are_not_charts():
    assert undeclared_chart.undeclared_bar_charts(_bars([50, 50, 50, 50])) == []
    assert undeclared_chart.undeclared_bar_charts(_bars([46, 22, 19, 13], labels=False)) == []
    assert undeclared_chart.undeclared_bar_charts(_bars([46, 22])) == []


def test_shapes_under_a_transform_are_not_read():
    svg = _bars([46, 22, 19, 13]).replace(HEAD, HEAD + '<g transform="translate(10 0)">').replace("</svg>", "</g></svg>")
    assert undeclared_chart.undeclared_bar_charts(svg) == []


def test_an_undeclared_chart_is_a_layout_failure_with_a_declare_hint():
    quality = {"passed": True, "failures": [{"kind": "text_overlap"}]}
    undeclared_chart.apply_undeclared_chart_quality(quality, _bars([46, 22, 19, 13]))
    assert quality["passed"] is False
    assert page_failure_kind(undeclared_chart.UNDECLARED_KIND) == LAYOUT
    hint = json.loads(undeclared_chart.undeclared_chart_repair_hint(quality))
    assert hint["kind"] == "undeclared_chart_drawn" and "placeholder" in hint["instruction"]
    assert hint["boxes"] and hint["other_failures"] == [{"kind": "text_overlap"}]
    clean = {"passed": True, "failures": []}
    undeclared_chart.apply_undeclared_chart_quality(clean, _bars([50, 50, 50, 50]))
    assert clean == {"passed": True, "failures": []}
