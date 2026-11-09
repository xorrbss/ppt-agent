"""Server chart renderer revision six: the drawing keeps its text inside the box."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.generation_core.drivers.svg import server_chart
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, apply_requested_native_quality,
)
from tests.unit.test_svg_server_chart_v4 import CASES, NS, _group, _page, _trusted

R4, R6 = server_chart.SERVER_CHART_REVISION_V4, server_chart.SERVER_CHART_REVISION_V6
BOX = (64.0, 140.0, 700.0, 460.0)
_FONT = Path(__file__).resolve().parents[4] / "servers/nextjs/public/fonts/noto-sans-kr/NotoSansKR-Variable.ttf"


@pytest.fixture(scope="module")
def advance():
    """Real Noto Sans KR advance width (em) of a string, the face the render worker draws with."""
    from fontTools.ttLib import TTFont
    font = TTFont(str(_FONT))
    cmap, metrics, units = font.getBestCmap(), font["hmtx"], font["head"].unitsPerEm
    return lambda text: sum(metrics[cmap[ord(char)]][0] for char in text if ord(char) in cmap) / units


def _text_extents(page, advance):
    for node in _group(page).iter(NS + "text"):
        for part in (node, *node.iter(NS + "tspan")):
            text = part.text or ""
            if not text.strip():
                continue
            x = float(part.get("x", node.get("x")))
            size = float(node.get("font-size"))
            width = advance(text) * size * (1.06 if node.get("font-weight") == "bold" else 1.0)
            anchor = node.get("text-anchor", "start")
            left = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
            yield text, left, left + width


@pytest.mark.parametrize("name", list(CASES))
def test_revision_six_text_stays_inside_the_box_at_real_glyph_widths(name, advance):
    page, record = _page(CASES[name][0], render=R6)
    assert record["dropped"] == []
    for text, left, right in _text_extents(page, advance):
        assert left >= BOX[0] and right <= BOX[0] + BOX[2], (text, left, right)


#: A real declaration whose v4/v5 ``100%`` tick label crossed the page body edge (2026-11-09).
MEASURED = {"chartType": "line", "categories": ["2024년", "2025년", "2026년"],
            "series": [{"name": "온라인 구매 경험 비율", "values": [82.4, 86.1, 89.7]}],
            "categoryOrder": "leftToRight", "valueLabels": {"shown": True, "position": "top"}, "markers": True,
            "title": "온라인 구매 경험 비율 (%)"}


@pytest.mark.parametrize("render,crosses", [(R4, True), (server_chart.SERVER_CHART_REVISION_V5, True), (R6, False)])
def test_the_measured_tick_label_crosses_the_box_edge_before_revision_six(render, crosses, advance):
    page, record = _page(MEASURED, render=render, box="64 140 640 440")
    assert record["dropped"] == []
    lefts = [left for _text, left, _right in _text_extents(page, advance)]
    assert (min(lefts) < BOX[0]) is crosses


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("revision", [V4, V5])
def test_revision_six_is_trusted_passes_the_checks_and_exports_natively(name, revision):
    page, record = _page(CASES[name][0], render=R6)
    assert record["objects"][0]["box"] == list(BOX)
    assert _trusted(page) == {"native-chart-x"}
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], CASES[name][1], pt_per_px=.75,
                                   revision=revision, paint_deferred=True)
    assert quality["failures"] == []
    (chart,) = page_native_objects(page, pt_per_px=.75, revision=revision)[0]["objects"]
    assert chart["anchor"] == {"x": BOX[0] + 8, "y": BOX[1], "width": BOX[2] - 16, "height": BOX[3]}
    assert "serverChartStyle" in chart["svgAppearance"]


@pytest.mark.parametrize("name", list(CASES))
def test_revision_six_lines_stay_inside_its_drawing_box(name):
    page, _ = _page(CASES[name][0], render=R6)
    left, right = BOX[0] + 8, BOX[0] + BOX[2] - 8
    for node in _group(page).iter(NS + "line"):
        half = float(node.get("stroke-width", "1")) / 2
        for key in ("x1", "x2"):
            assert left + half - 1e-6 <= float(node.get(key)) <= right - half + 1e-6


def test_revision_six_is_current_and_has_its_own_rule():
    assert server_chart.SERVER_CHART_CURRENT == R6 and R6 in server_chart.TRUSTED_RENDERERS
    rule = server_chart.server_chart_rule({"server_chart_render": R6})
    assert rule == server_chart.SERVER_CHART_RULE_V4.replace(f'"{R4}"', f'"{R6}"')


#: Published output of revision six. A change here means a stored deck would replay
#: differently: publish a new revision instead of editing v6.
V6_DIGESTS = {
    "column": "sha256:42c8f2bce1616e4f2cb16bb44252f7bf57060f2915684b835c13bf868f69acd5",
    "bar_many": "sha256:91c8d877841f2e7eb3d25a899fe27fba866d5243c426acda21bde933f554a481",
    "clustered": "sha256:533e34eb1810254545e7aacf04785d8f630a7c4ba94b058cf07164afc18d1f9c",
    "stacked": "sha256:ff4464d829839123cf5f3bd0f1efcd68bba45078e6e7a404fe5a989d385a1269",
    "percent": "sha256:2f98c014eabbb9a9c3d2c536a4f628041ac96dc0ef9c2e556e01bf8ac4ef53e3",
    "line": "sha256:9d6d83a92822598a5b50ce96d501c6b5e310aa9d6acff56b6af3747904f652b0",
    "lines_few": "sha256:f89fe3ac87c702aa3fdf923d82a4a87eb9f6e56777f3ef41d7eb8513de774fe1",
    "lines_many": "sha256:6175076a6b18bd5ae384d1cfe02b11e14e04e27631bf435e4ce3214409db4c2d",
    "area": "sha256:cac8d06de7827cb4fbc87063f0a5bda02cddb8279e7db185c6c37b9f36b4cdd1",
    "areas": "sha256:aca389d67b9575d15e1b7978ca17b98ba83176e7a05c34aa6b212839c9821f5a",
    "pie": "sha256:825deef7e5027af275142ab3695e87071aa2da3e051c7d462387cc2d607f2d4b",
    "pie_small": "sha256:afd4b3a93fd07d26e3ddabbe3fea07e371871ffeeabe2d893f1eb1fe8770b56a",
    "donut": "sha256:252fbe8dd8fe98cd4a802a4b39711232225dd5cc04849e724e4ff192690dbba6",
    "donut_legend": "sha256:636260d786910c70f4fd3bc00d2c836b081c2274657fe8dfd0043cc26d95d305",
    "zero": "sha256:8c050955491e2783767f702263daf9c8d613eb06e9f2d0715e306bfd6e91ad94",
}


@pytest.mark.parametrize("name", list(CASES))
def test_revision_six_output_is_frozen(name):
    from services.svg_contract import source_digest
    page, _ = _page(CASES[name][0], render=R6)
    assert source_digest(page["svg_content"]) == V6_DIGESTS[name]
