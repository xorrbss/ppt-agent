"""In-render card fit, end to end against real Chromium.

The two fixtures are the slides bench run 42 published flagged
``text_escapes_card`` (design ``docs/authored-in-render-fit-design-20260906.md``,
section 9).  Slide 13 has 21 px of copy below its card and bottom slack, so
the fitter must grow the card and leave the type alone; slide 21 has 52 px
and no slack (the page number sits below it), so the fitter must shrink that
card's leaves to a factor near 0.83 with every leaf at or above the 12 px
floor.  Both must reconstruct: rendering the fitted HTML without the fit
class must measure the same boxes and raise no escape.
"""

from __future__ import annotations

import json
from pathlib import Path


from services.authored_composition_policy import blocking_safety_issues
from utils.authored_dom_quality import inspect_authored_dom
from utils.authored_dom_text_escape import TEXT_ESCAPE_CATEGORY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "authored_fit"
FLOOR_PX = 12.0
BBOX_TOLERANCE_PX = 0.5

def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _escapes(html: str, metrics) -> list:
    report = inspect_authored_dom(
        html, metrics, reference_mode=None, strict_structural_off_canvas=False
    )
    assert report.telemetry_status == "available"
    return [issue for issue in report.issues if issue.category == TEXT_ESCAPE_CATEGORY]


def _blocking(html: str, metrics) -> set[str]:
    report = inspect_authored_dom(
        html, metrics, reference_mode=None, strict_structural_off_canvas=False
    )
    return {issue.category for issue in blocking_safety_issues(report)}


def _boxes(metrics) -> dict[str, tuple[float, float, float, float]]:
    return {
        element["qid"]: (
            element["bbox"]["x"], element["bbox"]["y"],
            element["bbox"]["width"], element["bbox"]["height"],
        )
        for element in metrics["elements"]
        if element.get("qid")
    }


def test_fixture_metrics_shape_matches_the_probe():
    metrics = json.loads((FIXTURES / "run42-s12.metrics.json").read_text(encoding="utf-8"))
    assert metrics["width"] == 1280 and metrics["height"] == 720
    assert any(element.get("qid") == "s12-map" for element in metrics["elements"])
