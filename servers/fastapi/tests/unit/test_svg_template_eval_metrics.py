"""The pure metrics of scripts/svg_template_eval: what a page drew and how much source it kept."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval" / "metrics.py"
_spec = importlib.util.spec_from_file_location("svg_template_eval_metrics", _PATH)
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)

NS = 'xmlns="http://www.w3.org/2000/svg"'


def _svg(body):
    return f'<svg {NS} width="1280" height="912" viewBox="0 0 1280 912">{body}</svg>'


def test_frame_furniture_and_icons_are_not_counted_as_the_pages_own_shapes():
    svg = _svg(
        '<g id="svg-system-template-background"><polygon points="0,0 1,1 2,0"/></g>'
        '<g id="svg-system-template-foreground"><line x1="0" y1="0" x2="9" y2="9"/></g>'
        '<g id="svg-system-template-body">'
        '<rect x="0" y="0" width="10" height="10"/>'
        '<g id="body-asset-source-slot-1"><polyline points="1,1 2,2"/><path d="M0 0"/></g>'
        '<polygon points="0,0 9,5 0,10"/><line x1="0" y1="0" x2="50" y2="0"/>'
        '<text x="1" y="2">1단계 → 2단계</text></g>')
    result = metrics.svg_structure(svg)
    assert (result["polygon"], result["line"], result["rect"], result["icons"]) == (1, 1, 1, 1)
    assert result["polyline"] == 0 and result["path"] == 0  # the icon's strokes are one icon
    assert result["flow_shapes"] == 2 and result["arrow_glyphs"] == 1 and result["text"] == 1
    assert result["texts"] == ["1단계 → 2단계"]


def test_a_deck_without_a_template_counts_every_shape_and_pictures_are_counted_once():
    svg = _svg('<image x="0" y="0" width="1" height="1" href="data:image/png;base64,AAAA" id="body-i"/>'
               '<g><path d="M0 0 L1 1"/><text x="1" y="2">a<tspan>b</tspan></text></g>')
    result = metrics.svg_structure(svg)
    assert result["pictures"] == 1 and result["path"] == 1 and result["texts"] == ["ab"]


def test_retention_is_the_share_of_source_words_and_numbers_that_survive():
    source = "월 마감 6일 재고 오차율 4.2% 장애 14건"
    kept = metrics.retention(source, "월 마감 6일 재고 4.2% 14건")
    assert kept["numbers"] == 1.0
    # Words are runs of two or more letters: the source has 마감 재고 오차율 장애, the deck kept 마감 and 재고.
    assert kept["words"] == 0.5
    assert metrics.retention("", "x") == {"words": None, "numbers": None}


def test_package_objects_count_native_tables_and_charts_from_slide_parts():
    parts = {"ppt/slides/slide1.xml": "<a:tbl></a:tbl> <a:tbl></a:tbl>",
             "ppt/slides/slide2.xml": ('<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
                                       '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
                                       ' r:id="rId2"/></a:graphicData>'),
             "ppt/slides/slide3.xml": "<p:sp/>", "ppt/slideLayouts/slideLayout1.xml": "<a:tbl>"}
    assert metrics.package_objects(parts) == {"tables": 2, "charts": 1, "pages_with_table": 1}


def test_summarize_reports_mean_min_max_of_numeric_fields_only():
    runs = [{"a": 2, "b": True, "c": "x"}, {"a": 4, "b": False}, {"a": 3}]
    assert metrics.summarize(runs) == {"a": {"mean": 3.0, "min": 2, "max": 4, "n": 3}}


def test_template_text_leaks_count_template_wording_the_source_does_not_explain():
    template_text_leaks = metrics.template_text_leaks
    template = "퀵카필드 운영 센터는 연간 194명의 전문 인력과 33% 성장률을 기록했다. Zephyrine Platform launch"
    source = "운영 센터는 24시간 운영되며 12명이 근무한다."
    # A deck that copies a template phrase and two template figures, and writes the source's own facts.
    generated = "퀵카필드 운영 센터의 전문 인력은 194명이며 33% 성장했다. 운영 센터는 12명이 근무한다."
    leaks = template_text_leaks(template, generated, source)
    # "퀵카필드 운영" and "전문 인력" are template bigrams outside the source; "운영 센터" is in the source.
    assert leaks == {"ngrams": 2, "figures": 2}
    assert template_text_leaks(template, "운영 센터는 12명이 근무한다.", source) == {"ngrams": 0, "figures": 0}
    # Counts only: the result never carries the words.
    assert set(leaks) == {"ngrams", "figures"} and all(isinstance(value, int) for value in leaks.values())


def test_page_rates_need_valid_actual_page_counts_and_never_fill_missing_counts_with_zero():
    assert metrics.normalized_page_metrics({"pages": 4, "first_try_ok": 3, "fallback_pages": 1}) == {
        "first_try_ok_rate": 0.75, "fallback_rate": 0.25}
    for pages in (None, 0, -1, True, 4.5, float("nan")):
        assert metrics.normalized_page_metrics({"pages": pages, "first_try_ok": 1}) == {}
    assert metrics.normalized_page_metrics({"pages": 4, "first_try_ok": 5, "fallback_pages": None}) == {}


def test_attempt_accounting_retains_failed_retry_and_unknown_outcome_with_separate_page_coverage():
    runs = [{"completed": False, "run": 1},
            {"completed": True, "run": 1, "retry_of": "attempt-1", "pages": 4,
             "first_try_ok": 3, "fallback_pages": 1},
            {"completed": True, "run": 2, "pages": 6, "first_try_ok": 6},
            {"run": 3}]
    result = metrics.attempt_accounting(runs, requested_runs=3)
    assert (result["attempted_runs"], result["completed_runs"], result["failed_runs"],
            result["unknown_outcome_runs"], result["retry_attempts"]) == (4, 2, 1, 1, 1)
    assert result["completion_rate"] == 0.5 and result["failure_rate"] == 0.25
    assert result["first_try_ok_rate"] == {"value": 0.9, "numerator": 9, "pages": 10,
                                           "available_runs": 2, "missing_runs": 0}
    assert result["fallback_rate"] == {"value": 0.25, "numerator": 1, "pages": 4,
                                      "available_runs": 1, "missing_runs": 1}
    assert result["requested_runs"] == 3
    assert metrics.attempt_accounting([])["completion_rate"] is None


def _row(category, value, unit="%", series="current"):
    return {"category": category, "value": value, "unit": unit, "series": series}


def test_structured_integrity_detects_reassigned_categories_units_series_and_duplicate_values():
    expected = [_row("Alpha", 20), _row("Beta", 80)]
    assert metrics.structured_data_integrity(expected, [_row("Beta", "80.0"), _row("Alpha", 20)])["status"] == "matched"
    variants = [[_row("Alpha", 80), _row("Beta", 20)],
                [_row("Alpha", 20, "USD"), _row("Beta", 80)],
                [_row("Alpha", 20, series="previous"), _row("Beta", 80)],
                [_row("Alpha", 20), _row("Alpha", 20), _row("Beta", 80)]]
    for observed in variants:
        result = metrics.structured_data_integrity(expected, observed)
        assert result["status"] == "mismatch"
        assert result["missing_rows"] or result["unexpected_rows"]
    # Retained text tokens do not establish their category/value association.
    assert metrics.retention("Alpha 20 Beta 80", "Alpha 80 Beta 20")["numbers"] == 1.0


def test_structured_integrity_is_unavailable_without_bounded_explicit_evidence():
    for expected, observed in ((None, None), ([_row("Alpha", 20)], None), ([], []),
                               ([{"value": 20}], [_row("Alpha", 20)]),
                               ([_row("Alpha", float("nan"))], [_row("Alpha", 20)])):
        assert metrics.structured_data_integrity(expected, observed)["status"] == "unavailable"
    assert metrics.structured_data_integrity([_row("Alpha", 20)] * 2, [], max_rows=1)["status"] == "unavailable"


def test_common_phrase_candidates_are_separated_only_with_supplied_common_phrase_evidence():
    result = metrics.template_text_leaks("Unique Platform 운영 센터", "Unique Platform 운영 센터", "",
                                         common_phrase_text="운영 센터")
    assert result["ngrams"] == 3
    assert result["common_phrase_ngrams"] == 1 and result["specific_ngrams"] == 2


def test_nonfinite_metric_values_are_not_summarized_as_observations():
    assert metrics.summarize([{"a": float("nan"), "b": float("inf")}, {"a": 2}]) == {
        "a": {"mean": 2.0, "min": 2, "max": 2, "n": 1}}


def test_run_eval_runs_as_a_script_without_the_scripts_package(tmp_path):
    """The README runs run_eval.py directly; its imports must not need `scripts` on sys.path."""
    import subprocess
    import sys
    from pathlib import Path

    directory = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval"
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import run_eval; "
            "print(run_eval.svg_content_sizing.__name__)")
    result = subprocess.run([sys.executable, "-c", code, str(directory)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "svg_content_sizing"
