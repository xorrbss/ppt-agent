"""The paired decision rule and the request builder of scripts/svg_template_eval (synthetic results, no network)."""
import importlib.util
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"svg_template_eval_{name}", _DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decide_mod = _load("decide")
builder = _load("request_builder")


def _result(label, cells):
    """cells: {cell: [metric dicts, one per run]}"""
    runs = []
    for cell, items in cells.items():
        for number, metrics in enumerate(items, 1):
            runs.append({"run": number, "completed": True, "cell": cell, "pages": 10, "seconds": 99, **metrics})
    return {"label": label, "runs": runs}


def test_paired_difference_uses_cell_means_and_overall_is_the_mean_of_cells():
    a = _result("a", {"x": [{"judge_overall": 3.0}, {"judge_overall": 3.2}], "y": [{"judge_overall": 3.5}]})
    b = _result("b", {"x": [{"judge_overall": 3.5}, {"judge_overall": 3.5}], "y": [{"judge_overall": 3.9}]})
    entry = decide_mod.decide(a, b)["judge_overall"]
    assert entry["cells"] == {"x": 0.4, "y": 0.4} and entry["diff"] == 0.4
    assert entry["verdict"] == "better" and entry["agree"] == 2


def test_a_difference_below_the_threshold_is_no_difference():
    a = _result("a", {"x": [{"judge_tone": 3.0}], "y": [{"judge_tone": 3.0}]})
    b = _result("b", {"x": [{"judge_tone": 3.15}], "y": [{"judge_tone": 3.2}]})
    assert decide_mod.decide(a, b)["judge_tone"]["verdict"] == "no difference"


def test_a_large_mean_driven_by_one_cell_is_inconsistent_without_a_majority_of_cells():
    a = _result("a", {c: [{"judge_overall": 3.0}] for c in "xyz"})
    b = _result("b", {"x": [{"judge_overall": 4.5}], "y": [{"judge_overall": 2.95}], "z": [{"judge_overall": 2.9}]})
    entry = decide_mod.decide(a, b)["judge_overall"]
    assert entry["diff"] > 0.2 and entry["verdict"] == "inconsistent"


def test_lower_is_better_for_fallback_pages_and_higher_for_first_try():
    a = _result("a", {c: [{"fallback_pages": 4, "first_try_ok": 5}] for c in "xy"})
    b = _result("b", {c: [{"fallback_pages": 2, "first_try_ok": 4}] for c in "xy"})
    report = decide_mod.decide(a, b)
    assert report["fallback_pages"]["verdict"] == "better"
    assert report["first_try_ok"]["verdict"] == "worse"


def test_other_metrics_are_reported_only_and_unpaired_cells_and_failed_runs_are_left_out():
    a = _result("a", {"x": [{"tables": 1}], "only_a": [{"tables": 9}]})
    a["runs"].append({"run": 2, "completed": False, "cell": "x", "state": "error"})
    b = _result("b", {"x": [{"tables": 3}], "only_b": [{"tables": 0}]})
    report = decide_mod.decide(a, b)
    assert report["tables"]["verdict"] == "report only" and report["tables"]["cells"] == {"x": 2.0}
    assert decide_mod.unpaired_cells(a, b) == (["only_a"], ["only_b"])


def test_results_without_cells_pair_as_one_default_cell_and_table_prints():
    a = {"label": "a", "runs": [{"run": 1, "completed": True, "fallback_pages": 5}]}
    b = {"label": "b", "runs": [{"run": 1, "completed": True, "fallback_pages": 3}]}
    report = decide_mod.decide(a, b)
    assert list(report["fallback_pages"]["cells"]) == ["default"]
    table = decide_mod.format_table(report, "a", "b")
    assert "fallback_pages" in table and "better" in table


def test_request_templates_match_their_inputs_and_carry_no_pin():
    names = builder.list_inputs()
    assert {"structured_proposal_ko", "unstructured_report_ko", "data_report_ko"} <= set(names)
    for name in names:
        raw = json.loads((_DIR / "inputs" / "requests" / f"{name}.json").read_text(encoding="utf-8"))
        assert raw["content"] == (_DIR / "inputs" / f"{name}.txt").read_text(encoding="utf-8")
        assert "svgTemplatePin" not in raw and "svgCanvas" not in raw
        assert (raw["n_slides"], raw["strategy"], raw["export_as"]) == (10, "svg", "pptx")


def test_build_cells_is_the_input_by_template_cross_product():
    pin = {"templateId": "t", "revision": 1, "digest": "sha256:" + "0" * 64}
    specs = [{"id": "a", "pin": pin, "canvas_emu": [9144000, 6858000], "frame_map": {"k": "v"}},
             {"id": "b", "pin": pin}]
    cells = builder.build_cells(["data_report_ko", "unstructured_report_ko"], specs)
    assert [c["cell"] for c in cells] == ["data_report_ko__a", "data_report_ko__b",
                                          "unstructured_report_ko__a", "unstructured_report_ko__b"]
    first, second = cells[0]["payload"], cells[1]["payload"]
    assert first["svgCanvas"] == {"widthEmu": 9144000, "heightEmu": 6858000}
    assert first["svgTemplateFrameMap"] == {"k": "v"} and first["svgTemplatePin"] == pin
    assert "svgCanvas" not in second and "svgTemplateFrameMap" not in second
    assert second["svgTemplateMode"] == "template-redesign"


def test_judge_scores_of_different_judge_versions_are_not_compared():
    old = _result("a", {"x": [{"judge_overall": 3.4, "fallback_pages": 3}], "y": [{"judge_overall": 3.5}]})
    new = _result("b", {"x": [{"judge_overall": 2.1, "judge_version": 2, "fallback_pages": 1}],
                        "y": [{"judge_overall": 2.2, "judge_version": 2}]})
    assert decide_mod.judge_versions(old) == {1} and decide_mod.judge_versions(new) == {2}
    report = decide_mod.decide(old, new)
    assert report["judge_overall"]["verdict"] == "not comparable"
    assert report["judge_overall"]["judge_versions"] == [[1], [2]]
    assert report["fallback_pages"]["verdict"] == "better"  # other metrics are still decided
    assert "judge_version" not in report  # the version is not a metric
    same = decide_mod.decide(new, _result("c", {"x": [{"judge_overall": 2.6, "judge_version": 2}],
                                                 "y": [{"judge_overall": 2.7, "judge_version": 2}]}))
    assert same["judge_overall"]["verdict"] == "better"
    mixed = _result("m", {"x": [{"judge_overall": 2.6, "judge_version": 2}], "y": [{"judge_overall": 3.5}]})
    assert decide_mod.decide(new, mixed)["judge_overall"]["verdict"] == "not comparable"
    assert decide_mod.judge_versions(_result("n", {"x": [{"fallback_pages": 2}]})) == set()
    assert "not comparable" in decide_mod.format_table(report)
