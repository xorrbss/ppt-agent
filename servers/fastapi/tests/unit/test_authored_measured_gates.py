"""The measured gates reach the model where it can act, and stay furniture-safe.

Review of 2026-09-08 on the text-authority branch found three defects before
any bench run: the 15px clamp raised the 12px page number past the 14px
detection threshold (the fitter's content line and the footer gate both
moved), the "merge boxes" instruction only reached the local patch round
(which cannot restructure), and gate issues were listed before real DOM
overflow so the remake brief's two evidence slots quoted counts, not qids.
"""

from __future__ import annotations

from services.authored_measured_gates import (
    FRAGMENTATION_REMAKE_HINT,
    measured_gate_issues,
    remake_density_hint,
)
from services.authored_repair_round_order import gate_defects_by_slide
from utils.authored_dom_footer_zone import _looks_like_page_number
from utils.authored_fragmentation_gate import inspect_fragmentation, page_number_max_font_px
from utils.slide_capture_fit import fit_probe_prelude
from utils.slide_capture_fit_script import fit_constants

GATE = "AUTHORED_FRAGMENTATION_GATE_ENABLED"


def _clear(monkeypatch) -> None:
    for key in (GATE, "AUTHORED_TYPE_FLOOR_PX", "AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS"):
        monkeypatch.delenv(key, raising=False)


def _page_number(font_px: float) -> dict:
    return {
        "qid": "s3-page",
        "bbox": {"x": 0, "y": 690, "width": 1280, "height": 16},
        "rendered_text": "03",
        "has_owned_text": True,
        "font_size": font_px,
    }


def test_page_number_raised_to_the_floor_is_still_furniture(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "true")
    assert page_number_max_font_px() == 15.0
    item = _page_number(15.0)
    assert _looks_like_page_number(item, viewport_width=1280, viewport_height=720)
    metrics = {"viewport": {"width": 1280, "height": 720}, "elements": [item]}
    report = inspect_fragmentation(metrics, max_text_owners=50, floor_px=15)
    assert report.text_owners == 0
    assert report.violations == ()


def test_page_number_threshold_stays_at_14px_when_the_gate_is_off(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "false")
    assert page_number_max_font_px() == 14.0
    assert not _looks_like_page_number(_page_number(15.0), viewport_width=1280, viewport_height=720)
    assert fit_constants()["page_number_max_font_px"] == 14.0


def test_fitter_probe_carries_the_raised_threshold(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "true")
    prelude = fit_probe_prelude()
    assert '"page_number_max_font_px": 15.0' in prelude
    assert '"font_floor_px": 15.0' in prelude


def test_gate_issues_carry_the_merge_instruction_into_the_remake_brief(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "true")
    elements = [
        {"qid": f"q{i}", "bbox": {"x": 0, "y": 0, "width": 10, "height": 10},
         "rendered_text": f"label {i}", "has_owned_text": True, "font_size": 18.0}
        for i in range(60)
    ]
    metrics = {"viewport": {"width": 1280, "height": 720}, "elements": elements}
    issues = measured_gate_issues(browser_metrics=metrics, fit_report=None, role="body")
    assert [issue["message"] for issue in issues] == ["text_owners:60>50"]
    assert issues[0]["detail"].startswith("BODY has 60 text-owning elements (limit 50). Merge")
    defects = gate_defects_by_slide([{"slide_index": 3, "issues": issues}])[3]
    assert defects[0].detail == issues[0]["detail"]


def test_fragmentation_only_remakes_get_the_merge_hint_not_fewer_bullets():
    class Defect:
        def __init__(self, category):
            self.category = category

    default = "lower density"
    assert remake_density_hint([Defect("fragmentation")], default=default) == FRAGMENTATION_REMAKE_HINT
    assert remake_density_hint([Defect("fragmentation"), Defect("overflow")], default=default) == default
    assert remake_density_hint([], default=default) == default


def test_gate_issues_come_after_dom_defects(monkeypatch):
    """The remake brief quotes ``defects[:2]``; overflow qids must outrank counts."""

    import services.authored_presentation_pipeline as pipeline
    import services.authored_measured_gates as gates

    class Dom:
        category = "overflow"

        def to_dict(self):
            return {"category": "overflow", "severity": "high", "message": "overflow", "qids": ["q1"]}

    class Report:
        telemetry_status = "available"

    class Profile:
        strict_dom_gate = True
        profile_id = "ordinary"

    monkeypatch.setattr(pipeline, "inspect_authored_dom", lambda *a, **k: Report())
    monkeypatch.setattr(pipeline, "blocking_safety_issues", lambda report: [Dom()])
    monkeypatch.setattr(gates, "fragmentation_violations", lambda *a, **k: ("text_owners:60>50",))
    monkeypatch.setattr(pipeline, "measured_gate_issues", gates.measured_gate_issues)
    _report, failure = pipeline._evaluate_slide_quality(
        index=0, html="<html></html>", browser_metrics={"elements": []}, capacity_plan=None,
        composition_profile=Profile(), reference_mode="ordinary", fit_report=None,
    )
    assert [issue["category"] for issue in failure["issues"]] == ["overflow", "fragmentation"]
