import json
import zipfile

import pytest

from scripts import canvas_bench, canvas_bench_render, canvas_bench_rules as rules


def _receipt(**overrides):
    receipt = {
        "schemaVersion": rules.RECEIPT_SCHEMA,
        "manuscript": "01.txt",
        "canvas": "wide-16x9",
        "style": "default",
        "strategy": None,
        "presentationId": "p1",
        "slideCount": 6,
        "generation": {"ok": True},
        "timing": {"totalSeconds": 120.0},
        "retries": {"total": 1},
        "localFitUnresolved": 0,
        "gate": {"blockers": [], "flags": []},
        "export": {"ok": True, "sizeMatches": True},
    }
    receipt.update(overrides)
    return receipt


def _check(result, name):
    return next(check for check in result["checks"] if check["name"] == name)


def test_identical_runs_pass():
    result = rules.compare([_receipt()], [_receipt(canvas="a4-landscape")])
    assert result["passed"] is True
    assert result["canvas"] == "a4-landscape"
    assert result["baseline"]["perSlideSeconds"] == 20.0


def test_defect_count_above_baseline_fails():
    candidate = _receipt(canvas="a4-landscape", localFitUnresolved=1)
    result = rules.compare([_receipt()], [candidate])
    assert result["passed"] is False
    assert _check(result, "local_fit_unresolved")["status"] == "fail"


def test_gate_blockers_counted_per_deck():
    candidate = _receipt(canvas="a4-landscape", gate={"blockers": ["b1", "b2"], "flags": []})
    baseline = _receipt(gate={"blockers": ["b1"], "flags": []})
    result = rules.compare([baseline], [candidate])
    assert _check(result, "gate_blockers") == {
        "name": "gate_blockers", "baseline": 1, "candidate": 2, "limit": 1, "passed": False, "status": "fail",
    }


def test_seconds_within_fifteen_percent_pass_and_above_fail():
    at_limit = _receipt(canvas="a4-landscape", timing={"totalSeconds": 138.0})
    above = _receipt(canvas="a4-landscape", timing={"totalSeconds": 139.0})
    assert _check(rules.compare([_receipt()], [at_limit]), "per_slide_seconds")["passed"] is True
    assert _check(rules.compare([_receipt()], [above]), "per_slide_seconds")["passed"] is False


def test_retries_tolerance_and_zero_baseline():
    assert _check(rules.compare([_receipt()], [_receipt(retries={"total": 1})]), "retries")["passed"]
    zero = _receipt(retries={"total": 0})
    assert not _check(rules.compare([zero], [_receipt(retries={"total": 1})]), "retries")["passed"]


def test_export_failure_and_size_mismatch_fail():
    failed = _receipt(canvas="a4-landscape", export={"ok": False, "sizeMatches": None})
    result = rules.compare([_receipt()], [failed])
    assert _check(result, "export_failures")["status"] == "fail"
    assert _check(result, "pptx_size_matches_canvas")["status"] == "fail"
    wrong = _receipt(canvas="a4-landscape", export={"ok": True, "sizeMatches": False})
    assert _check(rules.compare([_receipt()], [wrong]), "pptx_size_matches_canvas")["passed"] is False


def test_generation_failure_counts_and_excludes_deck_from_seconds():
    failed = _receipt(presentationId=None, manuscript="02.txt", generation={"ok": False},
                      timing={"totalSeconds": None}, retries={"total": 0}, export={"ok": False})
    candidate = [_receipt(canvas="a4-landscape"), dict(failed, canvas="a4-landscape")]
    baseline = [_receipt(), dict(failed, generation={"ok": True}, timing={"totalSeconds": 120.0},
                                 export={"ok": True, "sizeMatches": True}, presentationId="p2")]
    result = rules.compare(baseline, candidate)
    assert result["candidate"]["generationFailures"] == 1
    assert result["candidate"]["perSlideSeconds"] == 20.0
    assert result["passed"] is False


def test_unmeasured_metric_fails_but_absent_on_both_sides_is_not_applicable():
    unmeasured = _receipt(canvas="a4-landscape", localFitUnresolved=None)
    assert _check(rules.compare([_receipt()], [unmeasured]), "local_fit_unresolved")["status"] == "unmeasured"
    both = rules.compare([_receipt(localFitUnresolved=None)], [unmeasured])
    assert _check(both, "local_fit_unresolved")["status"] == "not_applicable"
    assert both["passed"] is True


def test_matrix_mismatch_fails():
    assert not rules.compare([_receipt()], [_receipt(style="other")])["passed"]
    assert not rules.compare([_receipt()], [_receipt(strategy="svg")])["passed"]
    assert not rules.compare([_receipt()], [_receipt(manuscript="02.txt")])["passed"]


def test_markdown_table_lists_every_check():
    result = rules.compare([_receipt()], [_receipt(canvas="a4-landscape")])
    markdown = rules.compare_markdown(result)
    assert "**PASS**" in markdown
    assert all(check["name"] in markdown for check in result["checks"])


def _compare(passed, canvas="square-1x1", style="default"):
    return {"schemaVersion": rules.COMPARE_SCHEMA, "canvas": canvas, "style": style, "passed": passed}


@pytest.mark.parametrize(
    ("history", "earned"),
    [
        ([True], "candidate"),
        ([True, True], "verified"),
        ([False, True], "candidate"),
        ([True, False], "disabled"),
        ([True, False, True], "candidate"),
    ],
)
def test_promotion_stage_from_history(history, earned):
    assert rules.promotion_stage([_compare(p) for p in history], "square-1x1", "default") == earned


def test_promotion_rejects_foreign_or_missing_compares():
    with pytest.raises(ValueError):
        rules.promotion_stage([_compare(True), _compare(True, style="other")], "square-1x1", "default")
    with pytest.raises(ValueError):
        rules.promotion_stage([_compare(True, canvas="ultrawide-21x9")], "square-1x1", "default")


def test_apply_promotion_raises_never_downgrades():
    stages = {"1:1": {"default": "verified"}, "21:9": {}}
    with pytest.raises(ValueError, match="downgrade"):
        rules.apply_promotion(stages, "1:1", "default", "candidate")
    updated, outcome = rules.apply_promotion({"1:1": {}, "21:9": {}}, "1:1", "default", "candidate")
    assert updated == {"1:1": {"default": "candidate"}, "21:9": {}}
    assert outcome == "disabled->candidate"
    assert rules.apply_promotion(updated, "1:1", "default", "candidate")[1] == "unchanged"


def test_only_gated_canvases_can_be_promoted():
    registry = rules.load_registry()
    stages = {"1:1": {}, "21:9": {}}
    assert rules.gated_ratio("square-1x1", registry, stages) == "1:1"
    assert rules.gated_ratio("ultrawide-21x9", registry, stages) == "21:9"
    with pytest.raises(ValueError, match="not stage-gated"):
        rules.gated_ratio("a4-landscape", registry, stages)
    with pytest.raises(ValueError, match="unknown canvas"):
        rules.gated_ratio("letter", registry, stages)


def test_registry_drives_request_fields_and_emu():
    registry = rules.load_registry()
    assert rules.request_fields("a4-landscape", registry) == {"page_size": "A4", "orientation": "landscape"}
    assert rules.request_fields("wide-16x9", registry) == {"aspect_ratio": "16:9"}
    assert rules.canvas_emu("a4-landscape", registry) == (10692000, 7560000)


def test_pptx_slide_size_reads_presentation_xml(tmp_path):
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="10692000" cy="7560000" type="custom"/></p:presentation>',
        )
        archive.writestr("ppt/slides/slide1.xml", "<p:sld/>")
    assert canvas_bench_render.pptx_slide_size(pptx) == (10692000, 7560000)
    assert canvas_bench_render.pptx_slide_parts(pptx) == 1


def test_promote_command_writes_stage_and_refuses_ungated(tmp_path, monkeypatch):
    stages_path = tmp_path / "canvas_stages.json"
    stages_path.write_text(json.dumps({"1:1": {}, "21:9": {}}), encoding="utf-8")
    monkeypatch.setattr(rules, "CANVAS_STAGES_PATH", stages_path)
    first, second = tmp_path / "c1.json", tmp_path / "c2.json"
    first.write_text(json.dumps(_compare(True)), encoding="utf-8")
    second.write_text(json.dumps(_compare(True)), encoding="utf-8")

    argv = ["promote", "--canvas", "square-1x1", "--style", "default", "--receipts"]
    assert canvas_bench.main(argv + [str(first)]) == 0
    assert json.loads(stages_path.read_text(encoding="utf-8"))["1:1"] == {"default": "candidate"}
    assert canvas_bench.main(argv + [str(first), str(second)]) == 0
    assert json.loads(stages_path.read_text(encoding="utf-8"))["1:1"] == {"default": "verified"}
    assert canvas_bench.main(["promote", "--canvas", "a4-landscape", "--style", "default",
                              "--receipts", str(first)]) == 2
    assert json.loads(stages_path.read_text(encoding="utf-8"))["1:1"] == {"default": "verified"}
