"""Real-Chrome pins for the local-fit v1.4 collision machinery: detection,
vertical-space resolution, byte-preserving reconstruction and determinism.

Every test renders a hand-written 1280x720 fixture from
``tests/fixtures/local_fit_v14`` through ``render_html_to_png_with_metrics``
with the production policy, so the report fields asserted here are the ones the
evidence layer reads. Rejection paths, the font floor and label morphology live
in ``test_slide_capture_fit_v14_chrome_rejections.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from services.authored_local_fit_evidence import validate_changed_capture
from utils.slide_capture import find_chrome

_SUPPORT = Path(__file__).resolve().parents[1] / "fixtures" / "local_fit_v14" / "support.py"
_spec = importlib.util.spec_from_file_location("local_fit_v14_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(support)

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")


# --- detection -------------------------------------------------------------


def test_overlapping_absolute_sibling_bars_seed_absolute_rows() -> None:
    _, _, capture = support.fit("absolute_rows_detect")
    report = support.report_of(capture)

    assert report["geometry_seeds"] == [{
        "qid": "bar-a", "other_qid": "bar-b", "kind": "absolute_rows",
        "measurement": "painted_sibling_intersection",
        "intersection_px": 24, "intentional_overlap": False,
    }]
    assert report["geometry_seeds"][0]["intersection_px"] > 0
    # The nearest eligible ancestor (the 120px rail) becomes the scope root.
    assert [scope["root_qid"] for scope in report["scopes"]] == ["rail"]
    assert report["scopes"][0]["seed_qids"] == ["bar-a", "bar-b"]
    assert report["scopes"][0]["allowed_axes"] == ["y"]
    assert report["scopes"][0]["preserved_properties"] == ["left", "width"]


def test_card_painted_under_a_bottom_anchored_band_seeds_bottom_band() -> None:
    _, _, capture = support.fit("bottom_band_resolve")
    report = support.report_of(capture)

    assert report["geometry_seeds"] == [{
        "qid": "card", "other_qid": "band", "kind": "bottom_band",
        "measurement": "painted_band_intersection",
        "intersection_px": 18, "intentional_overlap": False,
    }]


def test_protected_ancestors_produce_no_seed() -> None:
    """Collisions under ``data-allow-overlap``, ``<footer>`` or a header part are left alone."""

    _, _, capture = support.fit("protected_no_seed")
    report = support.report_of(capture)

    assert report["geometry_seeds"] == []
    assert report["parent_exclusions"] == []
    assert report["status"] == "unchanged"
    assert report["changed"] is False
    assert report["unresolved_reasons"] == []
    assert report["candidates"] == []
    assert report["edits"] == []


# --- resolution ------------------------------------------------------------


def _assert_resolved_by_parent_reflow(name: str, *, root: str) -> tuple[str, dict, object, dict]:
    html, policy, capture = support.fit(name)
    report = support.report_of(capture)

    assert report["unresolved_reasons"] == []
    assert report["error"] is None and report["partial"] is False
    assert report["status"] in {"spacing_adjusted", "combined"}
    assert report["changed"] is True
    assert [operation["kind"] for operation in report["operations"]] == ["parent_reflow"]
    assert report["operations"][0]["qid"] == root
    assert report["operations"][0]["font_factor"] == 1
    assert report["cards"] == [pytest.approx({
        "qid": root, "escape_px": report["geometry_seeds"][0]["intersection_px"],
        "outcome": "parent_reflow", "font_factor": 1, "spacing_factor": 1,
    })]
    assert report["search_termination"] == []
    assert all(
        "font-size" not in edit["declarations"] and "line-height" not in edit["declarations"]
        for edit in report["edits"]
    )
    for run in report["runs"]:
        assert run["final_pt"] == pytest.approx(run["original_pt"])
    assert all(seed["intersection_px"] > 0 for seed in report["geometry_seeds"])

    assert capture.fitted_html is not None
    support.assert_byte_preserving(html, capture.fitted_html, report["edits"])
    # The evidence layer accepts exactly this capture shape.
    baseline = validate_changed_capture(capture, original_html=html, policy=policy)
    assert baseline["complete"] is True
    return html, policy, capture, report


def test_absolute_rows_are_separated_by_moving_the_second_bar_down() -> None:
    html, policy, capture, report = _assert_resolved_by_parent_reflow(
        "absolute_rows_resolve", root="root")

    assert report["edits"] == [{"qid": "bar-b", "declarations": "top:56px;bottom:auto"}]
    # bar-a spans y=130..154 (24px deep); the bar moves to 154 + 2px spacing.
    verified = support.render(capture.fitted_html, {**policy, "baseline": report["baseline"]}, fit=False)
    boxes = support.bboxes(verified)
    assert boxes["bar-b"]["y"] >= support.bottom(boxes["bar-a"]) + 2 - 0.5
    # left/width are preserved properties: 80 (root) + 1 (border) + 220 (left).
    assert boxes["bar-b"]["x"] == pytest.approx(301, abs=0.5)
    assert boxes["bar-b"]["width"] == pytest.approx(320, abs=0.5)
    # after_depth: the fitter's own detector finds no collision in the result.
    refit = support.render(capture.fitted_html, support.policy_for(capture.fitted_html), fit=True)
    assert support.report_of(refit)["geometry_seeds"] == []
    assert support.report_of(refit)["status"] == "unchanged"


def test_bottom_band_is_moved_below_the_card_it_covered() -> None:
    html, policy, capture, report = _assert_resolved_by_parent_reflow(
        "bottom_band_resolve", root="root")

    assert report["edits"] == [{"qid": "band", "declarations": "top:218px;bottom:auto"}]
    space = next(
        candidate["space"] for candidate in report["candidates"]
        if candidate.get("outcome") == "committed"
    )
    assert space == [pytest.approx({
        "qid": "band", "required_growth_px": 20, "available_growth_px": 44, "growth_cap_px": 30,
    })]
    verified = support.render(capture.fitted_html, {**policy, "baseline": report["baseline"]}, fit=False)
    boxes = support.bboxes(verified)
    assert boxes["band"]["y"] >= support.bottom(boxes["card"]) + 2 - 0.5
    assert support.bottom(boxes["band"]) <= support.bottom(boxes["root"]) - 1 + 0.5


def test_grid_cards_grow_by_reclaiming_slack_from_the_following_sibling() -> None:
    """Design §5.3 pattern 2: fixed grid cards with a bottom-anchored box."""

    html, policy, capture, report = _assert_resolved_by_parent_reflow(
        "grid_slack_contentbox", root="root")

    assert [seed["kind"] for seed in report["geometry_seeds"]] == ["bottom_band"] * 3
    assert [(seed["qid"], seed["other_qid"]) for seed in report["geometry_seeds"]] == [
        ("body-1", "desc-1"), ("body-2", "desc-2"), ("body-3", "desc-3")]
    edits = {edit["qid"]: edit["declarations"] for edit in report["edits"]}
    assert set(edits) == {"grid", "card-1", "card-2", "card-3", "summary"}
    assert edits["grid"] == edits["card-1"] == edits["card-2"] == edits["card-3"] == "height:169px;flex-shrink:0"
    assert edits["summary"] == "height:111px;flex-shrink:0"
    space = next(
        candidate["space"] for candidate in report["candidates"]
        if candidate.get("outcome") == "committed"
    )
    assert space[0]["shared_row_qids"] == ["card-1", "card-2", "card-3"]
    assert space[0]["required_growth_px"] == pytest.approx(9)
    assert space[0]["available_growth_px"] >= space[0]["required_growth_px"]

    verified = support.render(capture.fitted_html, {**policy, "baseline": report["baseline"]}, fit=False)
    boxes = support.bboxes(verified)
    for index in (1, 2, 3):
        assert boxes[f"desc-{index}"]["y"] >= support.bottom(boxes[f"body-{index}"]) - 0.5
    assert support.bottom(boxes["summary"]) == pytest.approx(389, abs=0.5)
    assert boxes["summary"]["height"] == pytest.approx(111, abs=0.5)


def test_grid_slack_is_reclaimed_when_the_grid_itself_is_root_eligible() -> None:
    # _slackRoot steps the scope up from a root-eligible grid to the grid's parent,
    # so the 73px of slack in the following sibling is visible to materializeGrowth.
    _, _, capture = support.fit("grid_slack_borderbox")
    report = support.report_of(capture)

    assert report["unresolved_reasons"] == []
    assert [operation["kind"] for operation in report["operations"]] == ["parent_reflow"]
    assert all("font-size" not in edit["declarations"] for edit in report["edits"])


# --- determinism -----------------------------------------------------------


@pytest.mark.parametrize("name", ["morph_ok", "grid_slack_contentbox"])
def test_the_same_fixture_rendered_twice_yields_the_same_report(name: str) -> None:
    html, policy, first = support.fit(name)
    second = support.render(html, policy, fit=True)

    assert support.strip_timing(support.report_of(first)) == support.strip_timing(support.report_of(second))
    assert first.fitted_html == second.fitted_html
    assert first.html_digest == second.html_digest
