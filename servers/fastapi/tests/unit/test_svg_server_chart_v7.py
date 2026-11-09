"""Server chart renderer revision seven (labels as written) and source names in the writing policy."""
from __future__ import annotations

import pytest

from services.generation_core.drivers.svg import server_chart
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, apply_requested_native_quality,
)
from tests.unit.test_svg_server_chart_v4 import CASES, NS, _group, _page, _trusted

R6, R7 = server_chart.SERVER_CHART_REVISION_V6, server_chart.SERVER_CHART_REVISION_V7
#: The measured declaration: the source states 9.0%, revisions up to six drew "9%".
MARGIN = {"chartType": "line", "categories": ["2025년 4분기", "2026년 1분기", "2026년 2분기", "2026년 3분기"],
          "series": [{"name": "영업이익률", "values": [8.1, 9.0, 9.8, 10.4]}], "title": "영업이익률 (%)"}
MARGIN_SOURCE = "chart: 영업이익률: 2025년 4분기 8.1%, 2026년 1분기 9.0%, 2026년 2분기 9.8%, 2026년 3분기 10.4%"


def _labels(page):
    return [node.text for node in _group(page).iter(NS + "text") if node.text]


def test_revision_seven_keeps_declared_decimals_where_six_dropped_them():
    six, _ = _page(MARGIN, render=R6)
    seven, _ = _page(MARGIN, render=R7)
    assert "9%" in _labels(six) and "9.0%" not in _labels(six)
    assert "9.0%" in _labels(seven) and {"8.1%", "9.8%", "10.4%"} <= set(_labels(seven))


def test_revision_seven_native_format_writes_the_same_digits():
    page, _ = _page(MARGIN, render=R7)
    (chart,) = page_native_objects(page, pt_per_px=.75, revision=V4)[0]["objects"]
    assert chart["svgAppearance"]["serverChartStyle"]["dataLabels"]["numberFormat"] == '#,##0.0"%"'
    six, _ = _page(MARGIN, render=R6)
    (old,) = page_native_objects(six, pt_per_px=.75, revision=V4)[0]["objects"]
    assert old["svgAppearance"]["serverChartStyle"]["dataLabels"]["numberFormat"] == 'General"%"'


def test_integer_values_keep_their_integer_labels():
    page, _ = _page(CASES["column"][0], render=R7)
    assert {"120억원", "150억원", "180억원", "210억원"} <= set(_labels(page))


@pytest.mark.parametrize("name", [*CASES, "margin"])
@pytest.mark.parametrize("revision", [V4, V5])
def test_revision_seven_is_trusted_and_passes_the_checks(name, revision):
    raw, source = (MARGIN, MARGIN_SOURCE) if name == "margin" else CASES[name]
    page, record = _page(raw, render=R7)
    assert record["dropped"] == [] and _trusted(page) == {"native-chart-x"}
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], source, pt_per_px=.75,
                                   revision=revision, paint_deferred=True)
    assert quality["failures"] == []
    (chart,) = page_native_objects(page, pt_per_px=.75, revision=revision)[0]["objects"]
    assert "serverChartStyle" in chart["svgAppearance"]


@pytest.mark.parametrize("name", list(CASES))
def test_revision_seven_draws_six_when_no_value_has_trailing_zeros(name):
    raw = CASES[name][0]
    if any(isinstance(value, float) and value.is_integer() for item in raw["series"] for value in item["values"]):
        pytest.skip("a declared x.0 value is drawn differently by design")
    six, _ = _page(raw, render=R6)
    seven, _ = _page(raw, render=R7)
    assert six["svg_content"] == seven["svg_content"]


def test_revision_seven_is_current_and_has_its_own_rule():
    assert server_chart.SERVER_CHART_CURRENT == R7 and R7 in server_chart.TRUSTED_RENDERERS
    assert server_chart.server_chart_rule({"server_chart_render": R7}) == server_chart.SERVER_CHART_RULE_V4.replace(
        f'"{server_chart.SERVER_CHART_REVISION_V4}"', f'"{R7}"')


#: Published output of revision seven. A change here means a stored deck would replay
#: differently: publish a new revision instead of editing v7.
V7_DIGESTS = {
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
    "margin": "sha256:5342c3fe396c5a2543f3f66b1f7eec4d75ee163f35d90c8a7fc136f03bbbc235",
}


@pytest.mark.parametrize("name", [*CASES, "margin"])
def test_revision_seven_output_is_frozen(name):
    from services.svg_contract import source_digest
    page, _ = _page(MARGIN if name == "margin" else CASES[name][0], render=R7)
    assert source_digest(page["svg_content"]) == V7_DIGESTS[name]


# --- Names the source joins with a middle dot keep their dot ---------------------------------

SOURCE = "지역별 격차 - 2025년 권역별 이용자: 강원·제주권 189, 호남권 175. 업종: 금융·보험업, 운수ㆍ창고."


def test_middle_dot_names_of_the_source_are_literal_values_on_request():
    from services.ppt_writing_visible import literal_source_values
    assert {"강원·제주권", "금융·보험업", "운수ㆍ창고"} <= set(literal_source_values(SOURCE, middle_dot_names=True))
    assert literal_source_values(SOURCE) == ()


def test_a_frozen_header_title_with_a_source_name_is_no_longer_a_violation():
    from services.ppt_writing_visible import assess_visible_nodes, literal_source_values
    texts = ("권역별 이용 격차: 강원·제주권 189, 수도권 142로 47 차이",)
    assert assess_visible_nodes(texts, source_values=())["hard_violation_counts"] == {"middle_dot": 1}
    protected = literal_source_values(SOURCE, middle_dot_names=True)
    assert assess_visible_nodes(texts, source_values=protected)["hard_violation_counts"] == {}
    # A list the model joined with a dot is still writing the policy judges.
    assert assess_visible_nodes(("인건비·연구개발이 크다",), source_values=protected)["hard_violation_counts"] == {
        "middle_dot": 1}


def test_svg_render_asks_for_middle_dot_names():
    import inspect
    from services.generation_core.drivers.svg import render
    assert "header_binding=header_page, middle_dot_names=True," in inspect.getsource(render)
