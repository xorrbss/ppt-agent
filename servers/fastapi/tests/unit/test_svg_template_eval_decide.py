"""The paired decision rule and the request builder of scripts/svg_template_eval (synthetic results, no network)."""
import importlib.util
import hashlib
import json
import re
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
            identity = {key: hashlib.sha256(f"{cell}:{key}".encode()).hexdigest()
                        for key in decide_mod.CONDITION_KEYS}
            version = metrics.get("judge_version", 1)
            runs.append({"run": number, "completed": True, "cell": cell, "pages": 10, "seconds": 99,
                         "condition_identity": identity, "judge_version": version,
                         "evaluation_identity": {"code_revision": "runtime-source", "policy_revision": 5,
                                                 "policy_sha256": "policy-digest",
                                                 "generation_config_sha256": identity["generation_config_sha256"]},
                         "judge_identity": hashlib.sha256(f"judge:{version}".encode()).hexdigest(), **metrics})
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
    assert report["fallback_rate"]["verdict"] == "better"
    assert report["first_try_ok_rate"]["verdict"] == "worse"
    assert report["fallback_pages"]["verdict"] == "report only"
    assert report["first_try_ok"]["verdict"] == "report only"


def test_unpaired_cells_block_claims_while_completed_metric_differences_stay_readable():
    a = _result("a", {"x": [{"tables": 1}], "only_a": [{"tables": 9}]})
    a["runs"].append({"run": 2, "completed": False, "cell": "x", "state": "error",
                      "condition_identity": a["runs"][0]["condition_identity"],
                      "evaluation_identity": a["runs"][0]["evaluation_identity"]})
    b = _result("b", {"x": [{"tables": 3}], "only_b": [{"tables": 0}]})
    report = decide_mod.decide(a, b)
    assert report["tables"]["verdict"] == "not comparable" and report["tables"]["cells"] == {"x": 2.0}
    assert decide_mod.unpaired_cells(a, b) == (["only_a"], ["only_b"])


def test_results_without_cells_pair_as_one_default_cell_and_table_prints():
    a = _result("a", {"default": [{"fallback_pages": 5}]})
    b = _result("b", {"default": [{"fallback_pages": 3}]})
    for result in (a, b):
        result["runs"][0].pop("cell")
    report = decide_mod.decide(a, b)
    assert list(report["fallback_pages"]["cells"]) == ["default"]
    table = decide_mod.format_table(report, "a", "b")
    assert "fallback_pages" in table and "better" in table


def test_request_templates_match_their_inputs_and_carry_no_pin():
    names = builder.list_inputs()
    assert {"structured_proposal_ko", "unstructured_report_ko", "data_report_ko"} <= set(names)
    for name in names:
        raw = json.loads((_DIR / "inputs" / "requests" / f"{name}.json").read_text(encoding="utf-8"))
        assert raw["content"] == builder.input_source_text(name)
        assert "svgTemplatePin" not in raw and "svgCanvas" not in raw
        assert (raw["strategy"], raw["export_as"]) == ("svg", "pptx")
        if name.endswith("_sized"):
            # One body slide per 650 source characters (whitespace excluded) plus the cover.
            chars = len(re.sub(r"\s", "", raw["content"]))
            assert raw["n_slides"] == round(chars / 650) + 1
        else:
            assert raw["n_slides"] == 10


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
    old = _result("a", {"x": [{"judge_overall": 3.4, "fallback_pages": 3}],
                        "y": [{"judge_overall": 3.5, "fallback_pages": 3}]})
    new = _result("b", {"x": [{"judge_overall": 2.1, "judge_version": 2, "fallback_pages": 1}],
                        "y": [{"judge_overall": 2.2, "judge_version": 2, "fallback_pages": 1}]})
    assert decide_mod.judge_versions(old) == {1} and decide_mod.judge_versions(new) == {2}
    report = decide_mod.decide(old, new)
    assert report["judge_overall"]["verdict"] == "not comparable"
    assert report["judge_overall"]["judge_versions"] == [[1], [2]]
    assert report["fallback_rate"]["verdict"] == "better"  # other metrics are still decided
    assert "judge_version" not in report  # the version is not a metric
    same = decide_mod.decide(new, _result("c", {"x": [{"judge_overall": 2.6, "judge_version": 2}],
                                                 "y": [{"judge_overall": 2.7, "judge_version": 2}]}))
    assert same["judge_overall"]["verdict"] == "better"
    mixed = _result("m", {"x": [{"judge_overall": 2.6, "judge_version": 2}], "y": [{"judge_overall": 3.5}]})
    assert decide_mod.decide(new, mixed)["judge_overall"]["verdict"] == "not comparable"
    assert decide_mod.judge_versions(_result("n", {"x": [{"fallback_pages": 2}]})) == set()
    assert "not comparable" in decide_mod.format_table(report)


def test_legacy_results_are_readable_with_missing_identity_instead_of_an_invented_match():
    a = {"runs": [{"completed": True, "judge_overall": 3, "fallback_pages": 4, "pages": 10}]}
    b = {"runs": [{"completed": True, "judge_overall": 4, "fallback_pages": 1, "pages": 10}]}
    report = decide_mod.decide(a, b)
    assert report["judge_overall"]["diff"] == 1
    assert report["fallback_rate"]["verdict"] == "not comparable"
    assert report["_comparison"]["condition_issues"] == {
        "default": ["missing condition identity", "missing runtime identity"]}
    assert decide_mod.judge_versions(a) == {1}
    assert "missing condition identity" in decide_mod.format_table(report)


def test_each_condition_identity_component_must_match_but_code_revision_can_differ():
    for field in decide_mod.CONDITION_KEYS:
        a = _result("a", {"x": [{"tables": 1}]})
        b = _result("b", {"x": [{"tables": 2}]})
        b["runs"][0]["condition_identity"][field] = "different"
        assert decide_mod.decide(a, b)["tables"]["verdict"] == "not comparable"
    a = _result("a", {"x": [{"tables": 1, "code_identity": "revision-a"}]})
    b = _result("b", {"x": [{"tables": 2, "code_identity": "revision-b"}]})
    assert decide_mod.decide(a, b)["tables"]["verdict"] == "report only"


def test_matching_judge_version_does_not_hide_different_judge_settings_or_missing_identity():
    a = _result("a", {"x": [{"judge_overall": 3}]})
    b = _result("b", {"x": [{"judge_overall": 4, "judge_identity": "other-model-or-anchor"}]})
    assert decide_mod.decide(a, b)["judge_overall"]["verdict"] == "not comparable"
    b["runs"][0].pop("judge_identity")
    assert "missing judge identity" in decide_mod.decide(a, b)["judge_overall"]["comparison_issues"]["x"]


def test_failures_missing_metrics_and_missing_cells_have_separate_coverage():
    a = _result("a", {"x": [{"tables": 1}, {}], "failed": [{}]})
    a["runs"][-1]["completed"] = False
    a["cells"] = ["x", "failed", "unattempted"]
    b = _result("b", {"x": [{"tables": 2}], "failed": [{"tables": 3}]})
    report = decide_mod.decide(a, b)
    coverage = report["tables"]["coverage"]
    assert coverage["x"]["a"]["missing_metric"] == 1
    assert coverage["failed"]["a"]["failed"] == 1
    assert coverage["failed"]["a"]["missing_metric"] == 0
    assert coverage["unattempted"]["a"]["missing_cell"] is True
    assert report["_comparison"]["accounting_a"]["attempted_runs"] == 3
    assert report["_comparison"]["accounting_a"]["failed_runs"] == 1
    assert report["tables"]["verdict"] == "not comparable"
    assert decide_mod.unpaired_cells(a, b) == (["unattempted"], [])


def test_page_rate_uses_real_pooled_page_denominators_and_does_not_trust_stored_rate():
    a = _result("a", {"x": [{"pages": 4, "fallback_pages": 2}, {"pages": 6, "fallback_pages": 0}]})
    b = _result("b", {"x": [{"pages": 5, "fallback_pages": 1, "fallback_rate": 0.99}]})
    report = decide_mod.decide(a, b)
    assert report["fallback_rate"]["mean_a"] == report["fallback_rate"]["mean_b"] == 0.2
    assert report["fallback_rate"]["verdict"] == "no difference"
    assert report["fallback_rate"]["coverage"]["x"]["a"]["pages"] == 10
    table = decide_mod.format_table(report, "baseline", "variant")
    assert "completion_rate 1.000, failure_rate 0.000" in table
    assert "fallback_rate 0.200 (2/10 pages; missing on 0 completed runs)" in table
    assert "fallback_rate 0.200 (1/5 pages; missing on 0 completed runs)" in table
    assert "first_try_ok_rate unavailable (0/0 pages; missing on 2 completed runs)" in table


def test_mixed_conditions_inside_one_cell_do_not_silently_average():
    a = _result("a", {"x": [{"tables": 1}, {"tables": 2}]})
    b = _result("b", {"x": [{"tables": 3}]})
    a["runs"][1]["condition_identity"]["generation_config_sha256"] = "another-config"
    assert "mixed condition identity" in decide_mod.decide(a, b)["_comparison"]["condition_issues"]["x"]


def test_survivor_score_improvement_cannot_hide_completion_regression():
    a = _result("a", {"x": [{"judge_overall": 3}, {"judge_overall": 3}]})
    b = _result("b", {"x": [{"judge_overall": 4}, {}]})
    b["runs"][1]["completed"] = False
    report = decide_mod.decide(a, b)
    assert report["judge_overall"]["verdict"] == "better"  # Explicitly conditional on completed decks.
    assert report["_comparison"]["status"] == "completion regressed"
    assert report["_comparison"]["decision"] == "withheld"
    assert report["_comparison"]["accounting_b"]["failure_rate"] == 0.5
    assert "completion regressed" in decide_mod.format_table(report)


def test_all_failed_or_unknown_attempts_and_unattempted_requested_slots_are_incomplete_evidence():
    for outcome in (False, None):
        a, b = (_result(label, {"x": [{}]}) for label in ("a", "b"))
        for result in (a, b):
            result["runs"][0]["completed"] = outcome
        report = decide_mod.decide(a, b)
        assert report["_comparison"]["status"] == "incomplete evidence"
        assert report["_comparison"]["decision"] == "withheld"
        assert report["_comparison"]["cells"]["x"]["a"]["attempted_runs"] == 1
        assert report["_comparison"]["accounting_a"]["completed_runs"] == 0
    a = _result("a", {"x": [{"tables": 1}]})
    b = _result("b", {"x": [{"tables": 2}]})
    b["requested_runs"] = 2
    assert decide_mod.decide(a, b)["_comparison"]["status"] == "incomplete evidence"


def test_actual_runtime_identity_is_required_but_can_differ_between_variants():
    for field in ("code_revision", "generation_config_sha256", "policy_revision", "policy_sha256"):
        a = _result("a", {"x": [{"tables": 1}]})
        b = _result("b", {"x": [{"tables": 2}]})
        b["runs"][0]["evaluation_identity"].pop(field)
        assert decide_mod.decide(a, b)["tables"]["verdict"] == "not comparable"
    a = _result("a", {"x": [{"tables": 1}]})
    b = _result("b", {"x": [{"tables": 2}]})
    b["runs"][0]["evaluation_identity"].update(code_revision="new-runtime", policy_revision=6,
                                               policy_sha256="new-policy")
    assert decide_mod.decide(a, b)["_comparison"]["status"] == "comparable"
    b["runs"][0]["evaluation_identity"]["generation_config_sha256"] = "unrelated-config"
    assert decide_mod.decide(a, b)["tables"]["comparison_issues"]["x"] == [
        "runtime configuration identity disagrees with condition"]


def test_an_authorized_retry_that_completes_supersedes_its_failure_for_the_decision():
    a = _result("a", {"x": [{"judge_overall": 3}, {"judge_overall": 3}]})
    b = _result("b", {"x": [{"judge_overall": 4}, {}, {"judge_overall": 4}]})
    b["runs"][1].update(completed=False, attempt_id="failed-attempt")
    b["runs"][2].update(retry_of="failed-attempt", retry_authorization="explicit --retry-failed")
    b["requested_runs"] = 2
    report = decide_mod.decide(a, b)
    assert report["_comparison"]["status"] == "comparable"
    # The failure itself stays visible in the attempt accounting.
    assert report["_comparison"]["accounting_b"]["failed_runs"] == 1
    assert report["_comparison"]["accounting_b"]["retry_attempts"] == 1


def test_a_retry_that_fails_again_does_not_recover_the_slot():
    a = _result("a", {"x": [{"judge_overall": 3}, {"judge_overall": 3}]})
    b = _result("b", {"x": [{"judge_overall": 4}, {}, {}]})
    b["runs"][1].update(completed=False, attempt_id="failed-attempt")
    b["runs"][2].update(completed=False, attempt_id="retry-attempt", retry_of="failed-attempt")
    assert decide_mod.decide(a, b)["_comparison"]["status"] == "completion regressed"
