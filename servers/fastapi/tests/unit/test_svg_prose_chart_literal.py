"""Server-written ``chart:`` literals from prose series, and the v3 literal grammar (plan item S2)."""
import re

import pytest

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION, REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4,
    SNAPSHOT_REVISION_KEY, requested_native_data,
    requirements_revision, same_requirements_as_v2)
from services.generation_core.drivers.svg.prose_chart_literal import derive_chart_literals, with_chart_literals
from services.outline_chart_grounding import derive_outline_chart_literals, ground_outline_charts

V3 = REQUIREMENTS_VERSION_V3
V4 = REQUIREMENTS_VERSION_V4
SALES = "- 판매 추이: 2023년 16.2만대, 2024년 14.7만대, 2025년 18.5만대, 2026년 22만대(전망)"
SHARE = "점유율은 현대 38%, 기아 27%, 테슬라 18%, BMW 5%, 기타 12%로 집계됐다."


def _chart(content, revision=V3):
    [chart] = requested_native_data(content, revision)["charts"]
    return chart


def test_sales_series_becomes_the_literal_requirement():
    content, derived = with_chart_literals("# 판매 추이\n" + SALES + "\n- 2026년은 전망치")
    assert [item.literal for item in derived] == [
        "chart: 2023년 16.2만대, 2024년 14.7만대, 2025년 18.5만대, 2026년(전망) 22만대"]
    # The literal sits on its own line right after the line that states the series.
    assert content.split("\n")[2] == derived[0].literal
    chart = _chart(content)
    assert chart["categories"] == ["2023년", "2024년", "2025년", "2026년(전망)"]
    assert chart["values"] == ["16.2", "14.7", "18.5", "22"]
    assert chart["requiredUnitLabels"] == [{"text": "만대", "unit": "만대"}]
    assert "unsupportedReason" not in chart


def test_share_series_becomes_the_literal_requirement():
    content, derived = with_chart_literals(SHARE)
    assert [item.literal for item in derived] == ["chart: 현대 38%, 기아 27%, 테슬라 18%, BMW 5%, 기타 12%"]
    chart = _chart(content)
    assert chart["categories"] == ["현대", "기아", "테슬라", "BMW", "기타"]
    assert chart["values"] == ["38", "27", "18", "5", "12"]
    assert chart["units"] == ["%"] * 5


@pytest.mark.parametrize("prose", [
    "서울 120명, 부산 80명",                                  # two values
    "2023년 16.2만대, 2024년 14.7만대로 9% 감소했다.",          # two values plus a computed change
    "A 30%, B 60억원, C 10%",                                # mixed units
    "매출 120억원, 영업이익 30억원, 순이익 20억원",              # one unit, different measures
    "현재 30%, 2025년 목표 40%, 2027년 목표 50%",             # current versus target
    "A 10~20%, B 30%, C 40%",                                # a range
    "A 30%, B 20%, C 10%, D 5% 이상",                         # a qualified value next to the run
    "A 약 10%, B 20%, C 30%",                                 # an approximate value
    "착수 2024년, 중간 2025년, 완료 2026년",                   # dates are not values
    "A 1조 2,000억원, B 8,000억원, C 5,000억원",               # a compound amount
    "A 10, B 20, C 30",                                      # no unit stated
    "서울 강남 12%, 부산 8%, 대구 5%",                          # first label not separable
    "매출은 1,200억원이며 전환율은 30%에서 60%로 개선됐다.",    # prose without a series
])
def test_prose_without_one_stated_series_derives_nothing(prose):
    assert derive_chart_literals(prose) == []
    assert with_chart_literals(prose) == (prose, [])


@pytest.mark.parametrize("prose", [SALES, SHARE, "A 30%p, B 20%p, C 10%p 상승", "A $1,200, B $800, C $450",
                                   "국내 판매는 2023년 16.2만대, 2024년 14.7만대, 2025년 18.5만대로 증가했다"])
def test_derived_values_and_units_are_written_in_the_prose(prose):
    [item] = derive_chart_literals(prose)
    for value in item.values:
        assert re.search(rf"(?<![\d.,]){re.escape(value)}(?![\d])", prose)
    assert item.unit.lstrip("$") in prose
    assert all(label.split("(")[0] in prose for label in item.categories)


def test_existing_literal_is_never_duplicated():
    content = SHARE + "\nchart: 현대 38%, 기아 27%, 테슬라 18%"
    assert derive_chart_literals(content) == []


def test_only_a_v3_deck_derives():
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=SHARE)])
    for revision in (None, REQUIREMENTS_VERSION):
        same, added = derive_outline_chart_literals(outline, revision=revision)
        assert same is outline and added == 0
    derived, added = derive_outline_chart_literals(outline, revision=V3)
    assert added == 1 and "chart: 현대 38%" in derived.slides[0].content


def test_derived_line_passes_the_source_guard_only_with_written_values():
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="A 30%, B 20%, C 50%")])
    derived, _ = derive_outline_chart_literals(outline, revision=V3)
    kept, dropped = ground_outline_charts(derived, "A 30%, B 20%, C 50%", revision=V3)
    assert "chart: A 30%, B 20%, C 50%" in kept.slides[0].content and not dropped
    # C 50% is not in the source (the outline computed it): the derived line is removed.
    kept, dropped = ground_outline_charts(derived, "A 30%, B 20%", revision=V3)
    assert kept.slides[0].content == "A 30%, B 20%, C 50%" and len(dropped) == 1


@pytest.mark.parametrize(("literal", "categories", "values", "unit"), [
    ("chart: A 30%p, B 20%p, C 10%p", ["A", "B", "C"], ["30", "20", "10"], "%p"),
    ("chart: 2025년 18.5만대, 2026년 22만대(전망)", ["2025년", "2026년(전망)"], ["18.5", "22"], "만대"),
    ("chart: 매출(국내, 해외) 30%, 기타 70%", ["매출(국내, 해외)", "기타"], ["30", "70"], "%"),
    ("chart: 서울, 경기 45%; 부산 20%; 대구 15%", ["서울, 경기", "부산", "대구"], ["45", "20", "15"], "%"),
    ("chart: A 1,200, B 3,400; C 5", ["A", "B", "C"], ["1200", "3400", "5"], None),
])
def test_v3_literal_grammar(literal, categories, values, unit):
    chart = _chart(literal)
    assert (chart["categories"], chart["values"]) == (categories, values)
    assert set(chart.get("units", [None])) == {unit}
    assert requested_native_data(literal, V3)["version"] == V3


@pytest.mark.parametrize("literal", ["간트 차트: 분석 2개월, 설계 3개월, 개발 6개월",
                                     "- Gantt chart: Design 2 months, Build 4 months",
                                     "chart: A 1조 2,000억원, B 8,000억원"])
def test_v3_refuses_non_data_markers_and_compound_amounts(literal):
    assert requested_native_data(literal, V3) == {}


def test_v2_reads_every_line_exactly_as_before():
    assert requested_native_data("간트 차트: 분석 2개월, 설계 3개월")["charts"][0]["categories"] == ["분석", "설계"]
    assert requested_native_data("chart: A 1조 2,000억원, B 8,000억원")["charts"][0]["categories"] == ["A 1조", "B"]
    assert requested_native_data("chart: A 30%p, B 20%p") == {}
    assert requested_native_data("막대 차트: A 30, B 60.")["version"] == REQUIREMENTS_VERSION
    assert _chart("막대 차트: A 30, B 60.")["categories"] == ["A", "B"]


def test_snapshot_pins_the_revision():
    assert requirements_revision(None) == REQUIREMENTS_VERSION
    assert requirements_revision({}) == REQUIREMENTS_VERSION
    assert requirements_revision({SNAPSHOT_REVISION_KEY: V3}) == V3
    with pytest.raises(ValueError, match="svg_native_requirements_revision_invalid"):
        requirements_revision({SNAPSHOT_REVISION_KEY: "svg-native-requirements/v9"})
    assert same_requirements_as_v2("간트 차트: A 2개월, B 3개월", {})
    assert not same_requirements_as_v2("간트 차트: A 2개월, B 3개월", {SNAPSHOT_REVISION_KEY: V3})
    assert same_requirements_as_v2("차트: A 10, B 20", {SNAPSHOT_REVISION_KEY: V3})


@pytest.mark.parametrize("header", [
    "# 매출 (억원)",
    "### 매출 (단위: 억 원)",
    "(억 원)",
    "단위: 억 원",
    "매출(억원):",
    "매출(억원): ",
    "매출(억원) ",
])
def test_v4_copies_unit_from_heading_parenthesis_or_label_header(header):
    separator = "" if header.endswith(" ") else "\n"
    prose = header + separator + "서울 1,200.00, 부산 800.50, 대구 450"
    [item] = derive_chart_literals(prose, revision=V4)
    assert item.literal == "chart: 서울 1,200.00억원, 부산 800.50억원, 대구 450억원"
    assert item.values == ("1,200.00", "800.50", "450")
    assert item.unit == "억원"
    assert derive_chart_literals(prose, revision=V3) == []


def test_v4_joins_twelve_months_and_inserts_literal_after_the_last_source_line():
    first = ", ".join(f"{month}월 {month}.50" for month in range(1, 7))
    second = ", ".join(f"{month}월 {month}.50" for month in range(7, 13))
    prose = "### 월별 매출(억원)\n" + first + ",\n" + second + "\n- 매출 추이"
    content, [item] = with_chart_literals(prose, revision=V4)
    assert item.categories == tuple(f"{month}월" for month in range(1, 13))
    assert item.values == tuple(f"{month}.50" for month in range(1, 13))
    assert item.line_index == 2
    assert content == prose.replace(second + "\n", second + "\n" + item.literal + "\n")
    assert with_chart_literals(content, revision=V4) == (content, [])


def test_v4_joins_one_item_per_line_and_keeps_written_units_and_notes():
    prose = "단위: %\n- A 30%\n- B 20\n- C 50(전망)"
    [item] = derive_chart_literals(prose, revision=V4)
    assert item.literal == "chart: A 30%, B 20%, C(전망) 50%"


def test_v4_never_reinterprets_a_conflicting_unit_as_a_particle():
    prose = "### 인원(명)\nA 10명, B 20명, C 30가구"
    assert derive_chart_literals(prose, revision=V4) == []


@pytest.mark.parametrize("last", ["30", "30가구", "30가구로 집계됐다", "30으로 집계됐다"])
def test_v4_keeps_a_header_unit_beginning_with_a_particle_and_explicit_unit_particles(last):
    prose = "단위: 가구\nA 10가구, B 20가구, C " + last
    [item] = derive_chart_literals(prose, revision=V4)
    assert item.literal == "chart: A 10가구, B 20가구, C 30가구"
    assert item.unit == "가구"


@pytest.mark.parametrize("prose", [
    "### 매출(억원)\nA 10, B 20, C 30\nD 40명",             # mixed unit on continuation
    "### 매출(억원)\nA 10, B 20, C 30\nD 40~50",           # range on continuation
    "### 매출(억원)\nA 10, B 20, C 30\nD 약 40",           # qualified continuation
    "### 매출(억원)\nA 10, B 20, C 30\nD 대략 40",         # another qualified continuation
    "### 매출(억원)\nA 10, B 20, C 30\nD approx. 40",      # English qualifier
    "### 매출(억원)\nA 10, B 20, C 30\nD 40 이상",         # qualified tail
    "### 매출(억원)\nA 10, B 20, C 30\nD 1조 2,000억원",   # compound amount
    "### 매출(억원)\nA 10, B 20, 현재 30",                  # state label
    "### 비중(%)\n매출률 10, 이익률 20, 전환율 30",          # different measures
    "### 매출(억원)\nA 10%, B 20, C 30",                    # header disagrees with item
    "### 매출(억원)(명)\nA 10, B 20, C 30",                # ambiguous header
    "### 일정(년)\n착수 2024, 중간 2025, 완료 2026",        # calendar unit
    "### 매출(전망)\nA 10, B 20, C 30",                    # a note is not a unit
    "### 매출(억원)\n### 인원\nA 10, B 20, C 30",          # heading ends unit scope
    "### 매출(억원)\n다른 자료의 설명\nA 10, B 20, C 30",  # prose ends unit scope
    "### 매출(억원)\nA 10, B 20\n\nC 30",                 # not consecutive
    "A 10, B 20, C 30",                                    # no unit to inherit
])
def test_v4_refuses_entire_ambiguous_or_inexact_series(prose):
    assert derive_chart_literals(prose, revision=V4) == []
    assert with_chart_literals(prose, revision=V4) == (prose, [])


@pytest.mark.parametrize("prose", [SALES, SHARE, "A 30%p, B 20%p, C 10%p 상승",
                                   "A $1,200, B $800, C $450"])
def test_v4_retains_explicit_unit_series(prose):
    assert [item.literal for item in derive_chart_literals(prose, revision=V4)] == [
        item.literal for item in derive_chart_literals(prose, revision=V3)]


def test_v3_default_output_stays_byte_identical_for_split_series_and_unit_headers():
    first, second = "A 10%, B 20%, C 30%", "D 15%, E 15%, F 10%"
    prose = "### 비중(%)\n" + first + "\n" + second
    expected = "### 비중(%)\n" + first + "\nchart: " + first + "\n" + second + "\nchart: " + second
    assert with_chart_literals(prose)[0].encode("utf-8") == expected.encode("utf-8")
    assert with_chart_literals(prose, revision=V3) == with_chart_literals(prose)
    [joined] = derive_chart_literals(prose, revision=V4)
    assert joined.literal == "chart: " + first + ", " + second
    no_units = "### 비중(%)\nA 10, B 20, C 70"
    assert with_chart_literals(no_units, revision=V3) == (no_units, [])
    assert with_chart_literals(no_units) == (no_units, [])


def test_v4_outline_derivation_still_grounds_every_joined_point_against_the_source():
    source = "### 매출(억원)\n서울 1,200.00, 부산 800.50\n대구 450, 광주 300"
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=source)])
    derived, added = derive_outline_chart_literals(outline, revision=V4)
    assert added == 1
    kept, dropped = ground_outline_charts(derived, source, revision=V4)
    assert kept is derived and dropped == []
    # The remaining points cannot ground a computed/altered value or a swapped label.
    for changed in (source.replace("광주 300", "광주 301"), source.replace("광주 300", "제주 300")):
        kept, dropped = ground_outline_charts(derived, changed, revision=V4)
        assert kept.slides[0].content == source
        assert len(dropped) == 1
        assert dropped[0].ungrounded_values == ("광주 300",)
