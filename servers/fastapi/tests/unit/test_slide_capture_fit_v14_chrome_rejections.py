"""Real-Chrome pins for local-fit v1.4 candidate rejections, the 9pt floor and
the 10% shrink cap, and semantic-gated label morphology.

Companion to ``test_slide_capture_fit_v14_chrome.py``; both load the fixtures in
``tests/fixtures/local_fit_v14`` through the same render cache.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from services.authored_local_fit_evidence import validate_changed_capture
from services.authored_local_fit_policy import font_size_allowed, minimum_allowed_pt
from utils.renderer_worker_policy import RendererPolicyError
from utils.slide_capture import find_chrome

_SUPPORT = Path(__file__).resolve().parents[1] / "fixtures" / "local_fit_v14" / "support.py"
_spec = importlib.util.spec_from_file_location("local_fit_v14_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(support)

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")

V14 = support.V14


def _assert_exhausted(report: dict, *, root: str, remaining_issue: str) -> dict:
    assert report["status"] == "unresolved"
    assert "candidates_exhausted" in report["unresolved_reasons"]
    assert report["edits"] == [] and report["operations"] == [] and report["changed"] is False
    assert report["cards"] == []
    termination = report["search_termination"]
    assert len(termination) == 1
    assert termination[0]["code"] == "search.candidates_exhausted"
    assert termination[0]["root_qid"] == root
    assert termination[0]["remaining_issue"] == remaining_issue
    assert termination[0]["unevaluated_candidates"] == 0
    assert termination[0]["rejection_counts"] == support.rejection_codes(report)
    # An unresolved slide is restored byte-for-byte: every run keeps its size.
    for run in report["runs"]:
        assert run["final_pt"] == pytest.approx(run["original_pt"])
    return termination[0]


# --- rejection paths -------------------------------------------------------


def test_absolute_rows_beyond_the_growth_cap_reject_parent_space_unfit() -> None:
    _, _, capture = support.fit("absolute_rows_detect")
    report = support.report_of(capture)

    termination = _assert_exhausted(report, root="rail", remaining_issue="defect.content_occluded")
    # Every measured space candidate is rejected here; byte-identical plans are
    # deduplicated before measurement, so the exact count is a search detail.
    assert termination["rejection_counts"]["reject.parent_space_unfit"] >= 1
    rejected = support.candidates_with(report, "reject.parent_space_unfit")
    assert all(candidate["outcome"] == "removed:parent_growth_cap" for candidate in rejected)
    assert all(candidate["rejection"]["stage"] == "parent_plan" for candidate in rejected)
    assert all(candidate["rejection"]["rule"] == "parent_growth_cap" for candidate in rejected)
    assert all(candidate["uses_parent_space"] is True for candidate in rejected)
    # 26px are needed, 86px are free below, but the 120px rail caps growth at 12px.
    assert rejected[0]["space"] == [pytest.approx({
        "qid": "bar-b", "required_growth_px": 26, "available_growth_px": 86, "growth_cap_px": 12,
    })]
    overlap = support.candidates_with(report, "reject.overlap")
    assert [candidate["geometry_rejection"] for candidate in overlap] == [pytest.approx({
        "qid": "bar-a", "other_qid": "bar-b", "kind": "absolute_rows",
        "before_depth_px": 24, "after_depth_px": 24,
    })]


def test_no_room_under_the_protected_footer_rejects_space_and_text_escape() -> None:
    _, _, capture = support.fit("text_escape_blocked")
    report = support.report_of(capture)

    termination = _assert_exhausted(report, root="root", remaining_issue="defect.text_clipped")
    counts = termination["rejection_counts"]
    assert counts["reject.text_escape"] >= 20
    assert counts["reject.parent_space_unfit"] >= 10
    space = support.candidates_with(report, "reject.parent_space_unfit")
    assert all(candidate["outcome"] == "removed:parent_space_unavailable" for candidate in space)
    measurement = space[0]["space"][0]
    assert measurement["qid"] == "card"
    assert measurement["available_growth_px"] == pytest.approx(4)
    assert measurement["required_growth_px"] > measurement["available_growth_px"]
    escape = support.candidates_with(report, "reject.text_escape")
    assert all(candidate["outcome"] == "rejected:text_escape" for candidate in escape)
    assert all(candidate["rejection"]["stage"] == "hard_gate" for candidate in escape)
    assert any(candidate["font_factor"] == 0.9 for candidate in escape)


def test_child_card_crossing_its_parent_bottom_rejects_flow_box_escape() -> None:
    _, _, capture = support.fit("flow_box_blocked", V14)
    report = support.report_of(capture)

    termination = _assert_exhausted(report, root="root", remaining_issue="defect.text_clipped")
    assert termination["rejection_counts"]["reject.flow_box_escape"] >= 1
    flow = support.candidates_with(report, "reject.flow_box_escape")
    assert all(candidate["outcome"] == "rejected:flow_box_escape" for candidate in flow)
    assert all(candidate["uses_parent_space"] is False for candidate in flow)
    space = support.candidates_with(report, "reject.parent_space_unfit")
    assert space and all(candidate["outcome"] == "removed:parent_space_unavailable" for candidate in space)
    assert space[0]["space"] == [pytest.approx({
        "qid": "outer", "kind": "child_boundary", "required_growth_px": 22,
        "available_growth_px": 20, "growth_cap_px": 18, "safe_bottom_px": 237,
    })]


def test_displacing_a_protected_sibling_rejects_scope_exceeded() -> None:
    _, _, capture = support.fit("scope_exceeded")
    report = support.report_of(capture)

    assert report["geometry_seeds"] == []
    termination = _assert_exhausted(report, root="root", remaining_issue="defect.text_clipped")
    assert termination["rejection_counts"]["reject.scope_exceeded"] >= 10
    scope = support.candidates_with(report, "reject.scope_exceeded")
    assert all(candidate["outcome"] == "rejected:parent_preservation" for candidate in scope)
    assert all(candidate["rejection"]["stage"] == "hard_gate" for candidate in scope)
    assert scope[0]["geometry_rejection"] == pytest.approx({
        "qid": "locked", "property": "top", "delta": 28, "cap": 0.5, "limit": 0.5,
        "morph_allowed": False, "rule": "protected_box",
    })
    assert termination["rejection_counts"]["reject.text_escape"] >= 1


@pytest.mark.xfail(
    strict=True, raises=RendererPolicyError,
    reason="DEFECT: data-local-fit-protected is read by _protected() but is not in the "
    "renderer GLOBAL_ATTRIBUTES allowlist, so a slide carrying it never renders.",
)
def test_data_local_fit_protected_sibling_rejects_scope_exceeded() -> None:
    _, _, capture = support.fit("scope_exceeded_unadmitted")
    report = support.report_of(capture)

    assert support.rejection_codes(report)["reject.scope_exceeded"] >= 1


# --- font floor and shrink cap --------------------------------------------


def test_text_shrink_stays_within_the_ten_percent_cap_and_nine_point_floor() -> None:
    html, policy, capture = support.fit("text_scaled_floor", V14)
    report = support.report_of(capture)

    assert report["unresolved_reasons"] == []
    assert report["status"] == "text_scaled"
    assert [operation["kind"] for operation in report["operations"]] == ["text_scaled"]
    assert report["operations"][0]["font_factor"] == 0.95
    assert report["edits"] == [{"qid": "card-text", "declarations": "font-size:15.2px;line-height:20.9px"}]
    for run in report["runs"]:
        assert run["original_pt"] == pytest.approx(12)
        assert run["final_pt"] == pytest.approx(11.4)
        assert run["minimum_allowed_pt"] == pytest.approx(10.8)
        assert run["final_pt"] >= 9.0
        assert run["final_pt"] >= run["original_pt"] * 0.9
        assert run["minimum_allowed_pt"] == pytest.approx(minimum_allowed_pt(run["original_pt"], policy_revision=V14))
        assert font_size_allowed(run["original_pt"], run["final_pt"], policy_revision=V14)
    # Under v1.4 the extended ladder is policy-removed before it is ever measured.
    extended = [candidate for candidate in report["candidates"] if candidate["font_factor"] < 0.9]
    assert extended
    assert all(candidate["outcome"] == "removed:relative_shrink_limit" for candidate in extended)
    assert all(candidate["rejection"]["code"] == "reject.relative_shrink_limit" for candidate in extended)
    assert all(
        min(candidate.get("actual_font_factors") or [1]) >= 0.9 - 1e-9
        for candidate in report["candidates"]
        if candidate["outcome"] in {"passed", "committed"}
    )
    support.assert_byte_preserving(html, capture.fitted_html, report["edits"])
    validate_changed_capture(capture, original_html=html, policy=policy)


@pytest.mark.parametrize("revision", [V14, support.V15])
def test_nine_point_text_is_never_shrunk_below_the_floor(revision: str) -> None:
    _, _, capture = support.fit("floor_12px", revision)
    report = support.report_of(capture)

    _assert_exhausted(report, root="root", remaining_issue="defect.text_clipped")
    assert report["runs"]
    for run in report["runs"]:
        assert run["original_pt"] == pytest.approx(9.0)
        assert run["final_pt"] == pytest.approx(9.0)
        assert run["minimum_allowed_pt"] == pytest.approx(9.0)
    # No font candidate can produce a smaller size: the plan clamps to the floor
    # and collapses into the unchanged-spacing candidate.
    for candidate in report["candidates"]:
        for factor in candidate.get("actual_font_factors") or []:
            assert factor >= 1 - 1e-9
    assert all(
        "font-size" not in declaration["declarations"]
        for candidate in report["candidates"]
        for declaration in candidate.get("declarations") or []
    )


# --- morph ---------------------------------------------------------------


def test_uniform_label_row_morphs_to_donate_space_to_the_colliding_bars() -> None:
    html, policy, capture = support.fit("morph_ok")
    report = support.report_of(capture)

    assert report["unresolved_reasons"] == []
    assert report["status"] == "spacing_adjusted"
    assert report["geometry_seeds"] == [{
        "qid": "bar-a", "other_qid": "bar-b", "kind": "absolute_rows",
        "measurement": "painted_sibling_intersection", "intersection_px": 26, "intentional_overlap": False,
    }]
    [operation] = report["operations"]
    assert operation["kind"] == "parent_reflow" and operation["qid"] == "root"
    morph = operation["morph"]
    assert morph["outcome"] == "passed"
    assert morph["semantic_guard"]["passed"] is True
    assert morph["semantic_guard"]["reason"] == "uniform_label_decoration_only"
    assert morph["semantic_guard"]["minimum_contrast"] >= 4.5
    assert morph["donor_qid"] == "donor" and morph["receiver_qid"] == "receiver"
    assert morph["morph_nodes"] == ["tag-1", "tag-2", "tag-3", "tag-4", "tag-5"]
    assert morph["needed_px"] == pytest.approx(28)
    assert morph["reclaimed_px"] == pytest.approx(morph["needed_px"])
    assert morph["current_available_px"] == pytest.approx(2)
    assert morph["row_before_height_px"] == pytest.approx(120)
    assert morph["row_after_height_px"] == pytest.approx(92)
    assert morph["receiver_growth_px"] == pytest.approx(24)
    assert morph["boundary"]["final_visual_bottom_px"] <= morph["boundary"]["allowed_bottom_px"]

    edits = {edit["qid"]: edit["declarations"] for edit in report["edits"]}
    assert edits["bar-b"] == "top:32px;bottom:auto"
    assert edits["receiver"] == "height:114px"
    assert edits["lanes"] == "height:106px;flex-shrink:0"
    assert edits["lane"] == "height:58px;flex-shrink:0"
    assert edits["tags"].startswith("display:flex;flex-wrap:nowrap;column-gap:2px;row-gap:0;align-items:baseline")
    for index in range(1, 6):
        assert "background-color:transparent" in edits[f"tag-{index}"]
        assert "white-space:nowrap" in edits[f"tag-{index}"]
    assert all("font-size" not in declarations for declarations in edits.values())
    for run in report["runs"]:
        assert run["final_pt"] == pytest.approx(run["original_pt"])
    support.assert_byte_preserving(html, capture.fitted_html, report["edits"])
    validate_changed_capture(capture, original_html=html, policy=policy)

    verified = support.render(capture.fitted_html, {**policy, "baseline": report["baseline"]}, fit=False)
    boxes = support.bboxes(verified)
    assert boxes["bar-b"]["y"] >= support.bottom(boxes["bar-a"]) + 2 - 0.5
    tops = {round(boxes[f"tag-{index}"]["y"], 1) for index in range(1, 6)}
    assert len(tops) == 1, "the label row must collapse onto one line"
    assert boxes["donor"]["height"] == pytest.approx(92, abs=0.5)
    assert support.bottom(boxes["donor"]) <= support.bottom(boxes["root"]) - 1 + 0.5


def test_a_label_with_explicit_semantics_blocks_the_morph() -> None:
    _, _, capture = support.fit("morph_status")
    report = support.report_of(capture)

    termination = _assert_exhausted(report, root="root", remaining_issue="defect.content_occluded")
    # Every measured space candidate is rejected here; byte-identical plans are
    # deduplicated before measurement, so the exact count is a search detail.
    assert termination["rejection_counts"]["reject.morph_semantic_guard"] >= 1
    blocked = support.candidates_with(report, "reject.morph_semantic_guard")
    assert all(candidate["outcome"] == "removed:morph_semantic_guard" for candidate in blocked)
    assert all(candidate["rejection"]["stage"] == "parent_plan" for candidate in blocked)
    diagnostic = blocked[0]["morph"]
    assert diagnostic["outcome"] == "donor_nested_or_semantic_content"
    assert diagnostic["semantic_guard"] is False
    assert diagnostic["donor_qid"] is None
    assert diagnostic["receiver_qid"] == "receiver"
    assert diagnostic["morph_nodes"] == []
    assert diagnostic["needed_px"] == pytest.approx(28)
    assert blocked[0]["space"] == [pytest.approx({
        "qid": "bar-b", "required_growth_px": 28, "available_growth_px": 2, "growth_cap_px": 40,
    })]


@pytest.mark.xfail(
    strict=True, raises=RendererPolicyError,
    reason="DEFECT: _morphExplicitSemantics()/_morphLabelGroup() guard on data-status "
    "and data-semantic-color, which utils/renderer_markup_policy.py never admits; "
    "only role= and aria-* guards can fire on rendered HTML.",
)
def test_a_label_carrying_data_status_blocks_the_morph() -> None:
    _, _, capture = support.fit("morph_status_unadmitted")
    report = support.report_of(capture)

    assert support.rejection_codes(report)["reject.morph_semantic_guard"] >= 1


def test_a_label_that_would_drop_below_4_5_contrast_blocks_the_morph() -> None:
    _, _, capture = support.fit("morph_contrast")
    report = support.report_of(capture)

    termination = _assert_exhausted(report, root="root", remaining_issue="defect.content_occluded")
    # Every measured space candidate is rejected here; byte-identical plans are
    # deduplicated before measurement, so the exact count is a search detail.
    assert termination["rejection_counts"]["reject.semantic_contrast"] >= 1
    blocked = support.candidates_with(report, "reject.semantic_contrast")
    assert all(candidate["outcome"] == "removed:semantic_contrast" for candidate in blocked)
    guard = blocked[0]["morph"]["semantic_guard"]
    assert guard["passed"] is False
    assert guard["reason"] == "semantic_contrast"
    assert guard["detail"] == "below_4_5"
    assert guard["qid"] == "tag-1"
    assert guard["before"] >= 4.5
    assert guard["after"] < 4.5
    assert blocked[0]["morph"]["donor_qid"] == "donor"
    assert blocked[0]["morph"]["morph_nodes"] == ["tag-1", "tag-2", "tag-3", "tag-4", "tag-5"]
