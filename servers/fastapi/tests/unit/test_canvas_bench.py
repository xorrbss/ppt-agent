import json
import random
import zipfile
from pathlib import Path

import httpx
import pytest

from scripts import (
    canvas_bench,
    canvas_bench_assess as assess,
    canvas_bench_epoch as epochs,
    canvas_bench_matrix as matrix,
    canvas_bench_render,
    canvas_bench_rules as rules,
    canvas_bench_stats as stats,
)

ARTIFACTS = Path(__file__).resolve().parents[4] / "artifacts" / "canvas-bench-20260929"
MANUSCRIPTS = ["01.txt", "02.txt", "03.txt", "04.txt"]


def _receipt(canvas="wide-16x9", flagged=1, manuscript="01.txt", day="2026-09-30", epoch=None, pages=6,
             seconds=300.0, retries=1, **overrides):
    receipt = {
        "schemaVersion": rules.RECEIPT_SCHEMA, "createdAt": f"{day}T10:00:00+00:00",
        "manuscript": manuscript, "canvas": canvas, "style": "default", "strategy": None, "requestedSlides": pages,
        "presentationId": f"{canvas}-{manuscript}-{flagged}-{day}-{random.random()}",
        "slideCount": pages, "sourceFormats": ["svg"], "generation": {"ok": True},
        "timing": {"source": "wall", "totalSeconds": seconds, "perSlideSeconds": round(seconds / pages, 2)},
        "retries": {"total": retries}, "localFitUnresolved": None,
        "gate": {"blockers": [], "flags": {"flaggedPages": flagged, "passedPages": pages - flagged,
                                           "flaggedSlides": []}, "flagCount": flagged},
        "export": {"ok": True, "sizeMatches": True},
    }
    if epoch:
        receipt["metricEpoch"] = epoch
    receipt.update(overrides)
    return receipt


def _pool(canvas, count, flagged=1, manuscripts=3, day="2026-09-30", **kw):
    return [_receipt(canvas, flagged=flagged, manuscript=MANUSCRIPTS[i % manuscripts], day=day, **kw)
            for i in range(count)]


def _tier(result, name):
    return result["tiers"][name]


# ---- statistics ----------------------------------------------------------------------------------


def test_wilson_known_values():
    p, lower, upper = stats.wilson(8, 30, 1.96)
    assert (round(p, 3), round(lower, 3), round(upper, 3)) == (0.267, 0.142, 0.444)
    zero = stats.wilson(0, 6, 1.645)
    assert zero[0] == 0.0 and zero[1] == 0.0 and 0.0 < zero[2] < 0.35
    with pytest.raises(ValueError):
        stats.wilson(0, 0)


def test_newcombe_known_values_from_the_paper():
    # Newcombe 1998, example 1: 56/70 vs 48/80 -> method 10 interval (0.0524, 0.3339).
    diff, lower, upper = stats.newcombe_diff_bounds(56, 70, 48, 80, 1.96)
    assert round(diff, 3) == 0.2
    assert abs(lower - 0.0524) < 5e-4 and abs(upper - 0.3339) < 5e-4


def test_design_effect_scales_with_pages_per_deck():
    assert stats.design_effect(1) == 1.0
    assert stats.design_effect(6) == pytest.approx(1.75)
    adjusted = stats.newcombe_diff_bounds(11 / 1.75, 24 / 1.75, 8 / 1.75, 30 / 1.75)
    plain = stats.newcombe_diff_bounds(11, 24, 8, 30)
    assert adjusted[0] == pytest.approx(plain[0])
    assert adjusted[2] > plain[2] and adjusted[1] < plain[1]


def test_mean_diff_bounds_and_minimum_size():
    diff, lower, upper = stats.mean_diff_bounds([1.0, 3.0], [2.0, 6.0])
    assert diff == 2.0 and lower < diff < upper
    with pytest.raises(ValueError):
        stats.mean_diff_bounds([1.0], [1.0, 2.0])


# ---- verdicts ------------------------------------------------------------------------------------


def test_equal_canvases_with_enough_decks_pass_candidate_tier_only():
    result = rules.compare(_pool("wide-16x9", 18, flagged=1), _pool("square-1x1", 12, flagged=1))
    assert _tier(result, "candidate")["verdict"] == "pass"
    assert _tier(result, "verified")["verdict"] == "hold"
    assert result["earnedStage"] == "candidate"
    assert result["metricEpoch"] == epochs.LEGACY_EPOCH


def test_verified_needs_24_decks_4_manuscripts_and_two_days():
    def two_days(canvas):
        return (_pool(canvas, 12, manuscripts=4, day="2026-09-30")
                + _pool(canvas, 12, manuscripts=4, day="2026-10-01"))

    base, cand = two_days("wide-16x9"), two_days("square-1x1")
    assert rules.compare(base, cand)["earnedStage"] == "verified"
    one_day = _pool("square-1x1", 24, manuscripts=4, day="2026-09-30")
    result = rules.compare(base, one_day)
    assert _tier(result, "verified")["verdict"] == "hold"
    assert "calendar days 1/2" in " ".join(_tier(result, "verified")["reasons"])


def test_clearly_worse_canvas_fails():
    result = rules.compare(_pool("wide-16x9", 18, flagged=1), _pool("square-1x1", 12, flagged=4))
    primary = _tier(result, "candidate")["checks"][0]
    assert primary["difference"] > 0.2 and primary["lower"] > 0
    assert _tier(result, "candidate")["verdict"] == "fail"
    assert result["earnedStage"] == "disabled"


def test_primary_boundary_is_upper_bound_at_most_margin():
    def arm(flagged, pages, measured):
        return {"flagged": flagged, "pages": pages, "measured": measured, "rate": flagged / pages}

    base, cand = arm(8, 30, 5), arm(6, 30, 5)
    upper = assess._primary(base, cand, 1.0)["upper"]
    assert assess._primary(base, cand, upper + 1e-4)["status"] == "pass"
    assert assess._primary(base, cand, upper - 1e-4)["status"] == "hold"
    assert assess._primary(base, arm(30, 30, 5), 0.20)["status"] == "fail"


def test_insufficient_sample_holds_even_when_the_point_estimate_is_bad():
    result = rules.compare(_pool("wide-16x9", 5, flagged=0), _pool("square-1x1", 4, flagged=6))
    item = _tier(result, "candidate")
    assert item["verdict"] == "hold"
    assert any(r.startswith("insufficient_sample") for r in item["reasons"])


def test_manuscript_share_above_forty_percent_holds():
    cand = _pool("square-1x1", 10, manuscripts=3) + [_receipt("square-1x1", manuscript="01.txt") for _ in range(4)]
    result = rules.compare(_pool("wide-16x9", 18), cand)
    assert any("one manuscript" in r for r in _tier(result, "candidate")["reasons"])
    assert _tier(result, "candidate")["verdict"] == "hold"


def test_secondary_gates_seconds_and_retries():
    result = rules.compare(_pool("wide-16x9", 18, seconds=300.0), _pool("square-1x1", 12, seconds=420.0))
    seconds = next(c for c in _tier(result, "candidate")["checks"] if c["name"] == "seconds_per_slide")
    assert seconds["status"] == "fail" and seconds["margin"] == pytest.approx(7.5)
    assert _tier(result, "candidate")["verdict"] == "fail"
    retried = rules.compare(_pool("wide-16x9", 18, retries=1), _pool("square-1x1", 12, retries=12))
    retries = next(c for c in _tier(retried, "candidate")["checks"] if c["name"] == "retries_per_slide")
    assert retries["status"] == "fail"


def test_secondary_needs_twelve_decks_per_side():
    cand = _pool("square-1x1", 11) + [_receipt("square-1x1", retries=None)]
    retries = next(c for c in _tier(rules.compare(_pool("wide-16x9", 18), cand), "candidate")["checks"]
                   if c["name"] == "retries_per_slide")
    assert retries["status"] == "hold" and retries["reason"] == "insufficient_sample"


@pytest.mark.parametrize("override", [
    {"export": {"ok": True, "sizeMatches": False}},
    {"export": {"ok": False, "sizeMatches": None}},
    {"generation": {"ok": False}},
])
def test_hard_gates_fail_regardless_of_sample(override):
    cand = _pool("square-1x1", 2) + [_receipt("square-1x1", **override)]
    result = rules.compare(_pool("wide-16x9", 5), cand)
    assert _tier(result, "candidate")["verdict"] == "fail"
    assert any(r.startswith("hard_gate:") for r in _tier(result, "candidate")["reasons"])


# ---- pooling, epochs, applicability --------------------------------------------------------------


def test_epochs_never_pool():
    old = _pool("wide-16x9", 18)
    new_base = _pool("wide-16x9", 6, epoch=epochs.INK_EPOCH)
    cand = _pool("square-1x1", 12, epoch=epochs.INK_EPOCH)
    result = rules.compare(old + new_base, cand)
    assert result["metricEpoch"] == epochs.INK_EPOCH
    assert len(result["baselinePresentations"]) == 6
    assert result["excluded"]["baseline"]["other"] == {f"epoch:{epochs.LEGACY_EPOCH}": 18}
    assert _tier(result, "candidate")["verdict"] == "hold"
    legacy = rules.compare(old + new_base, cand, epoch=epochs.LEGACY_EPOCH)
    assert legacy["candidatePresentations"] == []  # nothing of the new epoch leaks into the old pool
    assert rules.compare(old, _pool("square-1x1", 12))["metricEpoch"] == epochs.LEGACY_EPOCH


def test_pool_key_excludes_other_slide_counts_and_styles():
    base = _pool("wide-16x9", 18) + [_receipt("wide-16x9", pages=8), _receipt("wide-16x9", style="other")]
    result = rules.compare(base, _pool("square-1x1", 12))
    assert result["excluded"]["baseline"]["other"] == {"slide_count": 1, "style_or_strategy": 1}


def test_compare_is_order_independent():
    base, cand = _pool("wide-16x9", 18) + _pool("wide-16x9", 3, flagged=3), _pool("square-1x1", 12, flagged=2)
    first = rules.compare(base, cand)
    shuffled_base, shuffled_cand = base[:], cand[:]
    random.Random(1).shuffle(shuffled_base)
    random.Random(2).shuffle(shuffled_cand)
    second = rules.compare(shuffled_base, shuffled_cand)
    for name in assess.TIERS:
        assert first["tiers"][name]["checks"] == second["tiers"][name]["checks"]
        assert first["tiers"][name]["verdict"] == second["tiers"][name]["verdict"]


def test_svg_local_fit_is_not_applicable_but_authored_missing_is_unmeasured():
    svg = assess.deck_metrics(_receipt())
    assert svg["localFitApplicable"] is False and svg["localFitUnresolved"] is None
    authored = dict(strategy="authored", sourceFormats=["authored"])
    assert assess.deck_metrics(_receipt(**authored))["localFitApplicable"] is True
    result = rules.compare(_pool("wide-16x9", 18, **authored), _pool("square-1x1", 12, **authored))
    assert result["informational"]["candidate"]["localFitUnresolved"] == {
        "applicableDecks": 12, "unmeasured": 12, "total": 0}
    # a not-applicable metric never blocks the primary verdict
    assert _tier(result, "candidate")["verdict"] == "pass"
    assert rules.compare(_pool("wide-16x9", 18), _pool("square-1x1", 12))["informational"]["candidate"][
        "localFitUnresolved"]["applicableDecks"] == 0


def test_unmeasured_decks_are_excluded_and_over_ten_percent_holds():
    missing = {"gate": {"blockers": [], "flags": []}}
    one = rules.compare(_pool("wide-16x9", 18), _pool("square-1x1", 12) + [_receipt("square-1x1", **missing)])
    sample = _tier(one, "candidate")["sample"]["candidate"]
    assert sample["measured"] == 12 and sample["unmeasured"] == 1  # 1/13 <= 10%: still decides
    assert _tier(one, "candidate")["verdict"] == "pass"
    many = _pool("square-1x1", 12) + [_receipt("square-1x1", **missing) for _ in range(2)]
    held = rules.compare(_pool("wide-16x9", 18), many)
    assert _tier(held, "candidate")["verdict"] == "hold"
    assert any(r.startswith("too_many_unmeasured") for r in _tier(held, "candidate")["reasons"])


def test_infra_lost_is_excluded_but_product_failure_stays():
    lost = _receipt("square-1x1", generation={"ok": False}, invalid={"reason": "infra_lost", "note": "reboot"})
    result = rules.compare(_pool("wide-16x9", 18), _pool("square-1x1", 12) + [lost])
    assert _tier(result, "candidate")["verdict"] == "pass"
    assert result["excluded"]["candidate"]["invalid"][0]["note"] == "reboot"
    failed = _receipt("square-1x1", generation={"ok": False})
    assert _tier(rules.compare(_pool("wide-16x9", 18), _pool("square-1x1", 12) + [failed]),
                 "candidate")["verdict"] == "fail"
    with pytest.raises(ValueError, match="infra_lost"):
        rules.compare(_pool("wide-16x9", 18), [_receipt("square-1x1", invalid={"reason": "bad_generation"})])


def test_compare_refuses_mixed_canvases_and_empty_input():
    with pytest.raises(ValueError):
        rules.compare(_pool("wide-16x9", 3) + _pool("a4-landscape", 3), _pool("square-1x1", 3))
    with pytest.raises(ValueError):
        rules.compare(_pool("wide-16x9", 3), [_receipt("square-1x1", invalid={"reason": "infra_lost"})])


def test_markdown_states_verdicts_and_sample():
    markdown = rules.compare_markdown(rules.compare(_pool("wide-16x9", 18), _pool("square-1x1", 12)))
    assert "**candidate: PASS**" in markdown and "**verified: HOLD**" in markdown
    assert "page_flag_rate" in markdown and "insufficient_sample" in markdown


@pytest.mark.skipif(not ARTIFACTS.is_dir(), reason="sq-uw artifacts not present")
def test_sq_uw_data_holds_for_insufficient_sample():
    def load(*names, canvas):
        files = [f for name in names for f in (ARTIFACTS / name).rglob("*.receipt.json")]
        return [r for r in (json.loads(f.read_text(encoding="utf-8")) for f in files) if r["canvas"] == canvas]

    base = load("wide-16x9", "sq-uw", canvas="wide-16x9")
    for canvas, decks, difference in (("square-1x1", 4, 0.19), ("ultrawide-21x9", 3, -0.04)):
        result = rules.compare(base, load("sq-uw", canvas=canvas))
        item = _tier(result, "candidate")
        assert item["verdict"] == "hold" and result["earnedStage"] == "disabled"
        assert item["sample"]["candidate"]["measured"] == decks
        assert item["checks"][0]["difference"] == pytest.approx(difference, abs=0.005)
        assert any(r.startswith("insufficient_sample") for r in item["reasons"])


# ---- promotion -----------------------------------------------------------------------------------


def _compare(earned, canvas="square-1x1", style="default"):
    return {"schemaVersion": rules.COMPARE_SCHEMA, "canvas": canvas, "style": style, "earnedStage": earned}


@pytest.mark.parametrize("earned", ["disabled", "candidate", "verified"])
def test_promotion_stage_reads_the_pooled_evaluation(earned):
    assert rules.promotion_stage([_compare(earned)], "square-1x1", "default") == earned
    assert rules.promotion_stage([_compare("verified"), _compare(earned)], "square-1x1", "default") == earned


def test_promotion_rejects_foreign_missing_or_invalid_compares():
    with pytest.raises(ValueError):
        rules.promotion_stage([_compare("candidate"), _compare("candidate", style="other")], "square-1x1", "default")
    with pytest.raises(ValueError):
        rules.promotion_stage([_compare("candidate", canvas="ultrawide-21x9")], "square-1x1", "default")
    with pytest.raises(ValueError):
        rules.promotion_stage([{"canvas": "square-1x1", "style": "default"}], "square-1x1", "default")


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


def test_promote_command_raises_stage_and_never_lowers_it(tmp_path, monkeypatch):
    stages_path = tmp_path / "canvas_stages.json"
    stages_path.write_text(json.dumps({"1:1": {}, "21:9": {}}), encoding="utf-8")
    monkeypatch.setattr(rules, "CANVAS_STAGES_PATH", stages_path)

    def write(name, earned):
        path = tmp_path / name
        path.write_text(json.dumps(_compare(earned)), encoding="utf-8")
        return str(path)

    def stage():
        return json.loads(stages_path.read_text(encoding="utf-8"))["1:1"]

    argv = ["promote", "--canvas", "square-1x1", "--style", "default", "--receipts"]
    assert canvas_bench.main(argv + [write("a.json", "candidate")]) == 0 and stage() == {"default": "candidate"}
    assert canvas_bench.main(argv + [write("b.json", "verified")]) == 0 and stage() == {"default": "verified"}
    for earned in ("disabled", "candidate"):  # hold/fail/lower results never demote
        assert canvas_bench.main(argv + [write("c.json", earned)]) == 0
        assert stage() == {"default": "verified"}
    assert canvas_bench.main(["promote", "--canvas", "a4-landscape", "--style", "default",
                              "--receipts", write("d.json", "verified")]) == 2
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schemaVersion": "canvas-bench-compare/v1", "canvas": "square-1x1",
                               "style": "default", "passed": True}), encoding="utf-8")
    assert canvas_bench.main(argv + [str(old)]) == 2


def test_compare_command_pools_directories_and_maps_exit_codes(tmp_path):
    for canvas, count in (("wide-16x9", 5), ("square-1x1", 4)):
        for i, receipt in enumerate(_pool(canvas, count)):
            (tmp_path / "runs" / canvas).mkdir(parents=True, exist_ok=True)
            (tmp_path / "runs" / canvas / f"{i}.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    argv = ["compare", "--baseline", str(tmp_path / "runs"), "--baseline-canvas", "wide-16x9",
            "--candidate", str(tmp_path / "runs"), "--candidate-canvas", "square-1x1", "--out", str(tmp_path / "out")]
    assert canvas_bench.main(argv) == 3  # hold: insufficient sample
    written = json.loads((tmp_path / "out" / "compare-square-1x1-vs-wide-16x9.json").read_text(encoding="utf-8"))
    assert written["schemaVersion"] == rules.COMPARE_SCHEMA and written["earnedStage"] == "disabled"
    same = argv[:-4] + ["--candidate-canvas", "wide-16x9", "--out", str(tmp_path / "o")]
    assert canvas_bench.main(same) in (0, 1, 3)  # the same canvas on both sides is legal input


def test_invalidate_command_stamps_infra_lost(tmp_path):
    path = tmp_path / "x.receipt.json"
    path.write_text(json.dumps(_receipt("square-1x1")), encoding="utf-8")
    assert canvas_bench.main(["invalidate", str(path), "--note", "machine restarted"]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["invalid"] == {"reason": "infra_lost",
                                                                        "note": "machine restarted"}


# ---- epochs --------------------------------------------------------------------------------------


def test_receipts_without_an_epoch_are_legacy():
    assert epochs.receipt_epoch({}) == epochs.LEGACY_EPOCH
    assert epochs.receipt_epoch({"metricEpoch": epochs.INK_EPOCH}) == epochs.INK_EPOCH


def test_overlap_basis_is_found_in_nested_results_and_maps_to_epochs():
    payload = {"result": {"quality": [{"measurements": {"overlap_basis": "glyph_ink"}}]}}
    assert epochs.find_overlap_basis(payload) == "glyph_ink"
    assert epochs.find_overlap_basis({"a": [1, {"b": None}]}) is None
    assert epochs.observed_epoch("glyph_ink") == epochs.INK_EPOCH
    assert epochs.observed_epoch("line_box") == epochs.LEGACY_EPOCH
    assert epochs.observed_epoch(None) is None


def test_run_epoch_precedence_and_refusal(monkeypatch):
    assert epochs.resolve_run_epoch("custom", None, None) == "custom"
    monkeypatch.setattr(epochs, "is_ancestor", lambda ancestor, commit, repo=None: commit == "after")
    assert epochs.resolve_run_epoch(None, "after", "fix") == epochs.INK_EPOCH
    assert epochs.resolve_run_epoch(None, "before", "fix") == epochs.LEGACY_EPOCH
    with pytest.raises(ValueError, match="metric epoch"):
        epochs.resolve_run_epoch(None, "after", None)
    monkeypatch.setattr(epochs, "is_ancestor", lambda *a, **k: None)
    with pytest.raises(ValueError, match="metric epoch"):
        epochs.resolve_run_epoch(None, "after", "fix")


# ---- matrix and resumable runner -----------------------------------------------------------------


def _manuscript_paths(count=3):
    return [Path(f"{i + 1:02d}-m.txt") for i in range(count)]


def test_parse_canvas_decks():
    assert matrix.parse_canvas_decks(["wide-16x9=18", "square-1x1=12"]) == {"wide-16x9": 18, "square-1x1": 12}
    for bad in (["wide"], ["wide=0"], ["wide=x"], ["a=1", "a=2"], []):
        with pytest.raises(ValueError):
            matrix.parse_canvas_decks(bad)


def test_plan_is_balanced_deterministic_and_interleaved():
    decks = {"wide-16x9": 18, "square-1x1": 12}
    plan = matrix.plan_matrix(decks, _manuscript_paths(), seed=7)
    assert plan == matrix.plan_matrix(decks, _manuscript_paths(), seed=7)
    assert len(plan) == 30 and len({(d.canvas, d.stem) for d in plan}) == 30
    for canvas, per_manuscript in (("wide-16x9", 6), ("square-1x1", 4)):
        counts: dict[str, int] = {}
        for deck in (d for d in plan if d.canvas == canvas):
            counts[deck.manuscript.name] = counts.get(deck.manuscript.name, 0) + 1
        assert set(counts.values()) == {per_manuscript}
    assert {d.canvas for d in plan[:2]} == {"wide-16x9", "square-1x1"}  # arms alternate within the first block
    orders = {tuple(d.canvas for d in matrix.plan_matrix({"a": 6, "b": 6}, _manuscript_paths(), seed=s))
              for s in range(8)}
    assert len(orders) > 1


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fake_receipt(deck, ok=True):
    return _receipt(deck.canvas, manuscript=deck.manuscript.name, generation={"ok": ok},
                    export={"ok": ok, "sizeMatches": True if ok else None})


def test_runner_skips_done_decks_and_resumes(tmp_path):
    plan = matrix.plan_matrix({"wide-16x9": 3, "square-1x1": 3}, _manuscript_paths(), seed=1)
    ran: list[str] = []

    def run_deck(deck):
        ran.append(deck.stem + deck.canvas)
        return _fake_receipt(deck)

    def stop_after_two(deck):
        if len(ran) == 2:
            raise httpx.ConnectError("backend down")
        return run_deck(deck)

    base = {"style": "default"}
    assert matrix.run_matrix(plan, tmp_path, stop_after_two, base, _write, log=lambda _: None) == matrix.EXIT_INFRA_LOST
    assert len(ran) == 2
    lost = json.loads(matrix.receipt_path(tmp_path, plan[2]).read_text(encoding="utf-8"))
    assert lost["invalid"]["reason"] == "infra_lost" and lost["generation"]["ok"] is False
    assert len(matrix.pending(plan, tmp_path)) == 4  # the lost deck is retried, the two done ones are not

    ran.clear()
    assert matrix.run_matrix(plan, tmp_path, run_deck, base, _write, log=lambda _: None) == 0
    assert len(ran) == 4 and plan[0].stem + plan[0].canvas not in ran
    rerun = json.loads(matrix.receipt_path(tmp_path, plan[2]).read_text(encoding="utf-8"))
    assert "invalid" not in rerun and rerun["previousInvalid"][0]["reason"] == "infra_lost"
    ran.clear()
    assert matrix.run_matrix(plan, tmp_path, run_deck, base, _write, log=lambda _: None) == 0 and ran == []


def test_product_failure_is_a_finished_deck_not_infra_lost(tmp_path):
    plan = matrix.plan_matrix({"wide-16x9": 1}, _manuscript_paths(1), seed=1)
    assert matrix.run_matrix(plan, tmp_path, lambda d: _fake_receipt(d, ok=False), {}, _write,
                             log=lambda _: None) == 0
    assert matrix.pending(plan, tmp_path) == []
    assert "invalid" not in json.loads(matrix.receipt_path(tmp_path, plan[0]).read_text(encoding="utf-8"))


def test_corrupt_receipt_is_rerun(tmp_path):
    plan = matrix.plan_matrix({"wide-16x9": 1}, _manuscript_paths(1), seed=1)
    path = matrix.receipt_path(tmp_path, plan[0])
    path.parent.mkdir(parents=True)
    path.write_text("{ truncated", encoding="utf-8")
    assert matrix.pending(plan, tmp_path) == plan


def test_matrix_command_dry_run_lists_done_and_todo(tmp_path, capsys):
    manuscripts = tmp_path / "ms"
    manuscripts.mkdir()
    for name in ("01-a.txt", "02-b.txt"):
        (manuscripts / name).write_text("x", encoding="utf-8")
    argv = ["matrix", "--base", "http://x", "--style", "default", "--manuscripts", str(manuscripts),
            "--canvas-decks", "wide-16x9=2", "--canvas-decks", "square-1x1=2", "--metric-epoch", "e1",
            "--out", str(tmp_path / "out"), "--dry-run"]
    assert canvas_bench.main(argv) == 0
    assert capsys.readouterr().out.count("todo") == 4
    plan = matrix.plan_matrix({"wide-16x9": 2, "square-1x1": 2}, sorted(manuscripts.glob("*.txt")), 20260929)
    _write(matrix.receipt_path(tmp_path / "out", plan[0]), _fake_receipt(plan[0]))
    canvas_bench.main(argv)
    assert capsys.readouterr().out.count("done  ") == 1
    without_epoch = [a for a in argv if a not in ("--metric-epoch", "e1")]
    with pytest.raises(SystemExit, match="metric epoch"):
        canvas_bench.main(without_epoch)
