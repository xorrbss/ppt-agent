"""M1: the composition gate records how far a slide misses, not just that it did.

``docs/authored-fit-magnitude-measurement-design-20260904.md`` section 3. The
verdicts these tests exercise are unchanged by construction -- magnitude is
attached after an issue has already been raised -- so the assertions here are
about the recorded numbers, the element kinds, and the two persistence paths
that carry them (the authored quality-gate receipt's issue dicts, and the E4
attempt-timing receipt's per-slide ``fitSummary``).
"""

from __future__ import annotations

import uuid

from services.authored_generation_timing_receipt import (
    create_generation_attempt_timing_receipt_v2,
    load_generation_attempt_timing_receipts,
)
from utils.authored_dom_fit_magnitude import (
    build_element_kind_index,
    element_kind,
)
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom

VIEWPORT = {"width": 1280, "height": 720}


def _metrics(*elements: dict) -> dict:
    return {"viewport": dict(VIEWPORT), "fonts_ready": True, "elements": list(elements)}


def _issues(report, category: str):
    return [issue for issue in report.issues if issue.category == category]


# --------------------------------------------------------------------------
# 3.1/3.2 -- magnitude and element kind on the three geometry categories
# --------------------------------------------------------------------------


def test_text_overflow_records_a_ratio_of_1_05_and_a_text_kind() -> None:
    html = '<p data-qid="lead">Revenue grew</p>'
    report = inspect_authored_dom(
        html,
        _metrics(
            {
                "qid": "lead",
                "tag": "p",
                "bbox": {"x": 40, "y": 40, "width": 400, "height": 60},
                "clientWidth": 400,
                "clientHeight": 60,
                "scrollWidth": 420,
                "scrollHeight": 60,
                "rendered_text": "Revenue grew",
                "has_owned_text": True,
                "computed_style": {"overflow": "hidden", "position": "static"},
            }
        ),
    )

    overflow = _issues(report, "overflow")
    assert len(overflow) == 1
    assert overflow[0].magnitude == {
        "ratio_x": 1.05,
        "ratio_y": 1.0,
        "excess_px_x": 20.0,
        "excess_px_y": 0.0,
        "element_kind": "text",
    }
    assert overflow[0].to_dict()["magnitude"]["ratio_x"] == 1.05


def test_container_overflow_records_a_ratio_of_1_4_and_a_container_kind() -> None:
    # A block-level child makes the wrapper a container, not a text leaf: a
    # uniform font scale-down is not the repair for this miss (defect 2).
    html = '<div data-qid="grid"><p data-qid="row">Row</p></div>'
    report = inspect_authored_dom(
        html,
        _metrics(
            {
                "qid": "grid",
                "tag": "div",
                "bbox": {"x": 40, "y": 40, "width": 600, "height": 200},
                "clientWidth": 600,
                "clientHeight": 200,
                "scrollWidth": 600,
                "scrollHeight": 280,
                "has_owned_text": False,
                "has_owned_paint": True,
                "unintentional": True,
                "computed_style": {"overflow": "hidden", "position": "static"},
            },
            {
                "qid": "row",
                "tag": "p",
                "parent_qid": "grid",
                "bbox": {"x": 40, "y": 40, "width": 600, "height": 40},
                "clientWidth": 600,
                "clientHeight": 40,
                "scrollWidth": 600,
                "scrollHeight": 40,
                "rendered_text": "Row",
                "has_owned_text": True,
                "computed_style": {"overflow": "hidden", "position": "static"},
            },
        ),
    )

    overflow = _issues(report, "overflow")
    assert [issue.qids for issue in overflow] == [("grid",)]
    assert overflow[0].magnitude == {
        "ratio_x": 1.0,
        "ratio_y": 1.4,
        "excess_px_x": 0.0,
        "excess_px_y": 80.0,
        "element_kind": "container",
    }


def test_off_canvas_records_a_30px_right_overshoot_and_zero_elsewhere() -> None:
    html = '<h1 data-qid="title">Title</h1>'
    report = inspect_authored_dom(
        html,
        _metrics(
            {
                "qid": "title",
                "tag": "h1",
                "bbox": {"x": 910, "y": 40, "width": 400, "height": 60},
                "clientWidth": 400,
                "clientHeight": 60,
                "scrollWidth": 400,
                "scrollHeight": 60,
                "rendered_text": "Title",
                "has_owned_text": True,
                "computed_style": {"overflow": "hidden", "position": "static"},
            }
        ),
    )

    off_canvas = _issues(report, "off_canvas")
    assert len(off_canvas) == 1
    assert off_canvas[0].magnitude == {
        "overshoot_px_left": 0.0,
        "overshoot_px_top": 0.0,
        "overshoot_px_right": 30.0,
        "overshoot_px_bottom": 0.0,
        "element_kind": "text",
    }
    assert report.fit_summary["worst_off_canvas_px"] == 30.0
    assert report.fit_summary["overflow_kinds"] == {"text": 1}


def test_clipping_records_the_hidden_share_against_the_clipping_ancestor() -> None:
    html = '<div data-qid="card"><p data-qid="body">Body</p></div>'
    report = inspect_authored_dom(
        html,
        _metrics(
            {
                "qid": "card",
                "tag": "div",
                "bbox": {"x": 100, "y": 100, "width": 200, "height": 100},
                "clientWidth": 200,
                "clientHeight": 100,
                "scrollWidth": 200,
                "scrollHeight": 100,
                "computed_style": {"overflow": "hidden", "position": "static"},
            },
            {
                "qid": "body",
                "tag": "p",
                "parent_qid": "card",
                # Half of the paragraph's width sits outside the clipping card.
                "bbox": {"x": 200, "y": 100, "width": 200, "height": 100},
                "clientWidth": 200,
                "clientHeight": 100,
                "scrollWidth": 200,
                "scrollHeight": 100,
                "clipped": True,
                "rendered_text": "Body",
                "has_owned_text": True,
                "computed_style": {"overflow": "visible", "position": "static"},
            },
        ),
    )

    clipping = _issues(report, "clipping")
    assert len(clipping) == 1
    assert clipping[0].magnitude == {"hidden_fraction": 0.5, "element_kind": "text"}


def test_figure_root_and_its_parts_are_not_reported_as_text() -> None:
    html = (
        '<section class="ig ig-flow" data-qid="fig">'
        '<div class="ig-label" data-qid="fig-label">Step</div>'
        '<img class="ig-art" data-qid="fig-art" src="a.png">'
        "</section>"
    )
    index = build_element_kind_index(ensure_authored_qids(html))

    assert element_kind("fig", None, index) == "figure"
    assert element_kind("fig-label", None, index) == "figure_part"
    # An image inside a figure is still a figure part: the design resolves
    # figure ancestry before the img tag.
    assert element_kind("fig-art", None, index) == "figure_part"


def test_a_standalone_image_is_reported_as_an_image_kind() -> None:
    html = '<div data-qid="wrap"><img data-qid="logo" src="logo.png"></div>'
    index = build_element_kind_index(ensure_authored_qids(html))

    assert element_kind("logo", {"tag": "img"}, index) == "image"


# --------------------------------------------------------------------------
# 3.1/3.3 -- a clean slide carries no magnitude and a neutral fit summary
# --------------------------------------------------------------------------


def test_a_clean_slide_has_no_magnitude_and_a_neutral_fit_summary() -> None:
    html = '<p data-qid="lead">Revenue grew</p>'
    report = inspect_authored_dom(
        html,
        _metrics(
            {
                "qid": "lead",
                "tag": "p",
                "bbox": {"x": 40, "y": 40, "width": 640, "height": 360},
                "clientWidth": 640,
                "clientHeight": 360,
                "scrollWidth": 640,
                "scrollHeight": 360,
                "rendered_text": "Revenue grew",
                "has_owned_text": True,
                "computed_style": {
                    "overflow": "hidden",
                    "position": "static",
                    "color": "#111111",
                    "backgroundColor": "#ffffff",
                },
            }
        ),
    )

    assert all(issue.magnitude is None for issue in report.issues)
    assert all("magnitude" not in issue.to_dict() for issue in report.issues)
    assert report.fit_summary["worst_overflow_ratio"] == 1.0
    assert report.fit_summary["worst_off_canvas_px"] == 0.0
    assert report.fit_summary["overflow_kinds"] == {}
    # 640x360 of owned content over a 1280x720 canvas is exactly a quarter.
    assert report.fit_summary["content_bbox_fill"] == 0.25
    assert report.to_dict()["fit_summary"] == dict(report.fit_summary)


def test_a_report_built_without_measurement_omits_fit_summary_entirely() -> None:
    from utils.authored_dom_quality import DomQualityIssue, DomQualityReport

    report = DomQualityReport("style_reference", (DomQualityIssue("font", "medium", "x"),), (), 1)

    assert report.fit_summary is None
    assert "fit_summary" not in report.to_dict()
    assert report.to_dict()["issues"] == [
        {
            "category": "font",
            "severity": "medium",
            "detail": "x",
            "qids": [],
            "policy": "style_reference",
        }
    ]


# --------------------------------------------------------------------------
# 3.4 -- persistence
# --------------------------------------------------------------------------


def test_the_attempt_timing_receipt_round_trips_a_failed_slide_fit_summary(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    html = '<p data-qid="lead">Revenue grew</p>'
    report = inspect_authored_dom(
        html,
        _metrics(
            {
                "qid": "lead",
                "tag": "p",
                "bbox": {"x": 40, "y": 40, "width": 400, "height": 60},
                "clientWidth": 400,
                "clientHeight": 60,
                "scrollWidth": 420,
                "scrollHeight": 60,
                "rendered_text": "Revenue grew",
                "has_owned_text": True,
                "computed_style": {"overflow": "hidden", "position": "static"},
            }
        ),
    )

    receipt = create_generation_attempt_timing_receipt_v2(
        job_id=job_id,
        attempt_id="private-fit-magnitude-attempt",
        attempt_number=1,
        revision=0,
        outcome="failed",
        stage_observations={
            "render": {"startMillis": 0, "endMillis": 10, "durationMillis": 10}
        },
        executed_stages=("chromiumReview",),
        queue_wait_ms=0,
        wall_ms=10,
        provider_usage={
            "totals": {
                "logicalCalls": 0,
                "physicalCalls": 0,
                "retryCalls": 0,
                "reportedUsageCalls": 0,
                "unavailableUsageCalls": 0,
                "pricedCalls": 0,
                "latencyMs": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cachedTokens": 0,
                "actualCostMicrounits": 0,
                "estimatedCostMicrounits": 0,
            },
            "stages": {},
        },
        retry_reason="terminal_failure",
        worker="workspace-generation-worker",
        terminal_error="authored_composition_safety_gate_failed",
        # Exactly the shape the gate stashes on the request.
        fit_summaries={3: dict(report.fit_summary)},
    )

    entry = receipt.to_dict()["fitSummary"][0]
    assert entry["slideIndex"] == 3
    assert entry["worstOverflowRatio"] == 1.05
    assert entry["overflowKinds"] == {"text": 1}
    # The receipt verifies its own digest on load, so a round trip proves the
    # new key is inside the sealed payload rather than appended after sealing.
    reloaded = load_generation_attempt_timing_receipts(job_id)[-1]
    assert reloaded.to_dict() == receipt.to_dict()


def test_a_succeeded_attempt_receipt_still_omits_the_fit_summary_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    receipt = create_generation_attempt_timing_receipt_v2(
        job_id=job_id,
        attempt_id="private-clean-attempt",
        attempt_number=1,
        revision=0,
        outcome="succeeded",
        stage_observations={
            "outline": {"startMillis": 0, "endMillis": 5, "durationMillis": 5},
            "render": {"startMillis": 5, "endMillis": 15, "durationMillis": 10},
            "publication": {"startMillis": 15, "endMillis": 20, "durationMillis": 5},
        },
        executed_stages=("outline", "authoring", "chromiumReview", "publication"),
        queue_wait_ms=0,
        wall_ms=20,
        provider_usage={
            "totals": {
                "logicalCalls": 0,
                "physicalCalls": 0,
                "retryCalls": 0,
                "reportedUsageCalls": 0,
                "unavailableUsageCalls": 0,
                "pricedCalls": 0,
                "latencyMs": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cachedTokens": 0,
                "actualCostMicrounits": 0,
                "estimatedCostMicrounits": 0,
            },
            "stages": {},
        },
        retry_reason=None,
        worker="workspace-generation-worker",
        terminal_error=None,
    )

    assert "fitSummary" not in receipt.to_dict()
    assert load_generation_attempt_timing_receipts(job_id)[-1].to_dict() == (
        receipt.to_dict()
    )
