"""The advisory drawn-bar proportion check (``drivers/svg/bar_proportion.py``, Q7)."""
from __future__ import annotations

import pytest

from services.generation_core.drivers.svg.bar_proportion import (
    ADVISORY_KIND, PROPORTION_TOLERANCE, apply_bar_proportion_advisory, bar_proportion_mismatches,
)

NS = "http://www.w3.org/2000/svg"
BASELINE = 560
VALUES = ("16.2만대", "14.7만대", "18.5만대", "22만대")
YEARS = ("2023", "2024", "2025", "2026")


def _column_chart(heights, labels=VALUES, *, extra=""):
    """The D2 p6 outlook page: four columns on one axis, value labels above, years below."""
    bars = []
    for number, (height, label, year) in enumerate(zip(heights, labels, YEARS, strict=True)):
        x = 200 + number * 200
        top = BASELINE - height
        bars.append(f'<rect x="{x}" y="{top}" width="120" height="{height}" rx="6" fill="#1E5EFF"/>'
                    f'<text x="{x + 60}" y="{top - 14}" font-size="22" font-weight="700" '
                    f'text-anchor="middle" fill="#172033">{label}</text>'
                    f'<text x="{x + 60}" y="{BASELINE + 34}" font-size="18" text-anchor="middle" '
                    f'fill="#5B6475">{year}</text>')
    return (f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'
            '<rect x="0" y="0" width="1280" height="720" fill="#F7F9FC"/>'
            '<text x="80" y="96" font-size="40" font-weight="700" fill="#172033">국내 전기차 판매 전망</text>'
            f'<line x1="160" y1="{BASELINE}" x2="1040" y2="{BASELINE}" stroke="#C8D0DC" stroke-width="2"/>'
            f'<g transform="translate(0 0)">{"".join(bars)}</g>'
            '<rect x="1080" y="300" width="160" height="96" rx="12" fill="#FFFFFF"/>'
            '<text x="1100" y="340" font-size="18" fill="#172033">2026년 전망치</text>'
            f'{extra}</svg>')


def test_the_live_d2_p6_chart_is_flagged():
    findings = bar_proportion_mismatches(_column_chart((160, 110, 190, 250)), width=1280, height=720)

    assert len(findings) == 1
    finding = findings[0]
    assert finding["orientation"] == "vertical" and finding["unit"] == "만대"
    assert [bar["extentPx"] for bar in finding["bars"]] == [160, 110, 190, 250]
    assert [bar["value"] for bar in finding["bars"]] == [16.2, 14.7, 18.5, 22.0]
    # The 14.7 bar is drawn about a quarter shorter than its label says.
    assert finding["maxDeviation"] > 0.2 and finding["tolerance"] == PROPORTION_TOLERANCE


def test_a_proportional_chart_is_not_flagged():
    assert bar_proportion_mismatches(_column_chart((160, 145, 183, 217)), width=1280, height=720) == []


def test_a_bar_within_three_percent_is_not_flagged_and_one_beyond_is():
    assert bar_proportion_mismatches(_column_chart((160, 145, 183, 222)), width=1280, height=720) == []
    assert bar_proportion_mismatches(_column_chart((160, 145, 183, 232)), width=1280, height=720)


def test_horizontal_bars_are_checked_from_their_left_edge():
    rows = "".join(f'<rect x="300" y="{200 + 80 * number}" width="{length}" height="48" fill="#1E5EFF"/>'
                   f'<text x="{300 + length + 12}" y="{232 + 80 * number}" font-size="20">{label}</text>'
                   f'<text x="280" y="{232 + 80 * number}" font-size="20" text-anchor="end">{brand}</text>'
                   for number, (length, label, brand) in enumerate(
                       [(380, "38%", "현대"), (380, "27%", "기아"), (180, "18%", "테슬라")]))
    svg = f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">{rows}</svg>'
    finding, = bar_proportion_mismatches(svg, width=1280, height=720)
    assert finding["orientation"] == "horizontal" and finding["unit"] == "%"
    fixed = svg.replace('width="380" height="48" fill="#1E5EFF"/><text x="692" y="312"',
                        'width="270" height="48" fill="#1E5EFF"/><text x="582" y="312"')
    assert bar_proportion_mismatches(fixed, width=1280, height=720) == []


@pytest.mark.parametrize("svg", [
    # A KPI card row: equal heights whatever the numbers say.
    "".join(f'<rect x="{100 + 300 * n}" y="300" width="260" height="160" fill="#FFF"/>'
            f'<text x="{230 + 300 * n}" y="380" font-size="48" text-anchor="middle">{value}</text>'
            for n, value in enumerate(("38%", "27%", "18%"))),
    # Mixed units are not one series.
    _column_chart((160, 110, 190, 250), labels=("16.2만대", "14.7%", "18.5만대", "22만대")),
    # A bar without a value label.
    _column_chart((160, 110, 190, 250), labels=("16.2만대", "증가", "18.5만대", "22만대")),
    # Two interleaved series on their own axes (tests/fixtures/svg_smoke/slide-3.svg): each fill is a series.
    "".join(f'<rect x="{174 + 180 * n}" y="{554 - a}" width="30" height="{a}" fill="#2563EB"/>'
            f'<text x="{189 + 180 * n}" y="{544 - a}" font-size="13" text-anchor="middle">{va}억원</text>'
            f'<rect x="{226 + 180 * n}" y="{554 - b}" width="30" height="{b}" fill="#94A3B8"/>'
            f'<text x="{241 + 180 * n}" y="{544 - b}" font-size="13" text-anchor="middle">{vb}억원</text>'
            for n, (a, va, b, vb) in enumerate([(146, 120, 117, 12), (177, 145, 146, 15),
                                                (195, 160, 175, 18), (226, 185, 214, 22)])),
])
def test_drawings_that_are_not_one_labelled_series_are_not_read(svg):
    if not svg.startswith("<svg"):
        svg = f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">{svg}</svg>'
    assert bar_proportion_mismatches(svg, width=1280, height=720) == []


def test_a_light_track_behind_every_column_does_not_hide_the_chart():
    tracks = "".join(f'<rect x="{200 + 200 * n}" y="300" width="120" height="260" fill="#E8EEF8"/>'
                     for n in range(4))
    wrong = _column_chart((160, 110, 190, 250)).replace(
        '<g transform="translate(0 0)">', f'<g transform="translate(0 0)">{tracks}')
    right = _column_chart((160, 145, 183, 217)).replace(
        '<g transform="translate(0 0)">', f'<g transform="translate(0 0)">{tracks}')
    assert len(bar_proportion_mismatches(wrong, width=1280, height=720)) == 1
    assert bar_proportion_mismatches(right, width=1280, height=720) == []


def test_server_furniture_is_not_read():
    chart = _column_chart((160, 110, 190, 250)).replace('<g transform="translate(0 0)">',
                                                        '<g id="svg-system-header">')
    assert bar_proportion_mismatches(chart, width=1280, height=720) == []


def test_the_finding_is_an_advisory_and_never_fails_the_page():
    quality = {"passed": True, "geometry_passed": True, "failures": []}
    apply_bar_proportion_advisory(quality, _column_chart((160, 110, 190, 250)), width=1280, height=720)
    assert quality["passed"] is True and quality["failures"] == []
    assert quality["advisories"][0]["kind"] == ADVISORY_KIND

    clean = {"passed": True, "failures": []}
    apply_bar_proportion_advisory(clean, _column_chart((160, 145, 183, 217)), width=1280, height=720)
    apply_bar_proportion_advisory(clean, "<svg", width=1280, height=720)  # unparsable: nothing added
    assert clean == {"passed": True, "failures": []}


def test_the_deck_summary_names_the_advised_pages():
    from services.generation_core.drivers.svg.quality import with_chart_advisories

    flagged = {"quality": {"advisories": [{"kind": ADVISORY_KIND, "groups": []}]}}
    summary = with_chart_advisories({"status": "passed"}, [({"quality": {}}, b""), (flagged, b"")])
    assert summary == {"status": "passed", "chartAdvisories": {ADVISORY_KIND: [1]}}
