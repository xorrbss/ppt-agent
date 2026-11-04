from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.outline_chart_grounding import (
    ground_chart_lines, ground_outline_charts, normalize_number, source_index)

SOURCE = """## Region report
Revenue by region: North 4,820 million, South 3,100 million, East 1,250.5 million.
Share of sales: North 48%, South 31%, East 12.5%.
The project runs from March 2027 to April 2027 and then from May 2027 to June 2027.
| Phase | Period | Staff |
| --- | --- | --- |
| Analysis | 2027 Mar-Apr | 4 |
| Design | 2027 May-Jun | 6 |
"""


def run(content: str, source: str = SOURCE):
    return ground_chart_lines(content, source_index(source))


def test_values_written_in_source_are_kept_with_separators_units_and_percent():
    for line in ("chart: North 4820 million, South 3100 million, East 1250.5 million",
                 "chart: North 48%, South 31%, East 12.5%",
                 "chart: North 48 퍼센트, South 31 퍼센트, East 12.50 퍼센트"):
        content = f"- text\n{line}"
        assert run(content) == (content, [])


def test_computed_duration_is_dropped_and_other_lines_stay():
    content = "- Plan\nchart: Analysis 2 months, Design 2 months\n- End"
    kept, dropped = run(content)
    assert kept == "- Plan\n- End"
    assert [d.reason for d in dropped] == ["chart_values_not_in_source"]
    assert dropped[0].ungrounded_values == ("Analysis 2", "Design 2")


def test_number_elsewhere_in_source_does_not_ground_another_label():
    kept, dropped = run("chart: Analysis 6, Design 4")
    assert kept == "" and dropped


def test_table_values_are_grounded_by_their_row():
    content = "chart: Analysis 4, Design 6"
    assert run(content) == (content, [])


def test_one_altered_digit_drops_the_whole_line():
    kept, dropped = run("chart: North 4,830, South 3,100, East 1,250.5")
    assert kept == "" and dropped[0].ungrounded_values == ("North 4830",)


def test_partial_number_does_not_match():
    kept, dropped = run("chart: North 482, South 31")
    assert dropped and "North 482" in dropped[0].ungrounded_values


def test_fullwidth_digits_and_other_language():
    source = "지역별 매출: 서울 １２０명, 부산 80명, 대구 45명"
    content = "chart: 서울 120명, 부산 80명, 대구 45명"
    assert ground_chart_lines(content, source_index(source)) == (content, [])


def test_label_wording_may_differ_slightly():
    source = "데이터·통계 영역: 6개 시스템\n인프라 공통 영역: 10개 시스템"
    kept, dropped = ground_chart_lines("chart: 데이터와 통계 6개, 인프라 공통 10개", source_index(source))
    assert dropped == []


def test_multi_series_checks_every_series():
    content = "chart: Sales: North 4820, South 3100; Share: North 48, South 99"
    kept, dropped = run(content)
    assert dropped and dropped[0].ungrounded_values == ("South 99",)


def test_unparsed_chart_text_and_plain_numbers_are_untouched():
    content = "- chart: see below\n- Revenue 999 million"
    assert run(content) == (content, [])


def test_outline_wrapper_drops_only_ungrounded_lines_and_never_edits_values():
    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="chart: North 4820, South 3100"),
        SlideOutlineModel(content="chart: Analysis 2, Design 2\n- keep"),
    ])
    result, dropped = ground_outline_charts(outline, SOURCE)
    assert [s.content for s in result.slides] == ["chart: North 4820, South 3100", "- keep"]
    assert [d.slide_index for d in dropped] == [1]
    same, none = ground_outline_charts(PresentationOutlineModel(slides=outline.slides[:1]), SOURCE)
    assert none == [] and same.slides[0].content == outline.slides[0].content


def test_normalize_number():
    assert [normalize_number(x) for x in ("4,820", "12.50", "3.0", "007", "0.5")] == ["4820", "12.5", "3", "7", "0.5"]
