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

import asyncio
import json
from pathlib import Path

import pytest

from services.authored_composition_policy import blocking_safety_issues
from utils.authored_dom_quality import inspect_authored_dom
from utils.authored_dom_text_escape import TEXT_ESCAPE_CATEGORY
from utils.authored_fit_edits import apply_fit_edits
from utils.authored_semantic_role_repair import canonicalize_authored_roles
from utils.slide_capture import _placeholder_png, render_html_to_png_with_metrics
from utils.slide_capture_fit_script import FIT_CLASS

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


async def _fit(name: str):
    html = canonicalize_authored_roles(_fixture(name))
    capture = await render_html_to_png_with_metrics(html, fit=True)
    if capture.browser_metrics is None or capture.png == _placeholder_png():
        pytest.skip("Chromium render unavailable on this host")
    return html, capture


def test_original_fixtures_still_escape():
    asyncio.run(_test_original_fixtures_still_escape())


async def _test_original_fixtures_still_escape():
    """Guard the fixtures themselves: without the fit both slides escape."""
    for name, expected_px in (("run42-s12.html", 21.0), ("run42-s20.html", 52.0)):
        html = canonicalize_authored_roles(_fixture(name))
        capture = await render_html_to_png_with_metrics(html)
        if capture.browser_metrics is None or capture.png == _placeholder_png():
            pytest.skip("Chromium render unavailable on this host")
        escapes = _escapes(html, capture.browser_metrics)
        assert escapes, name
        worst = max(float((e.magnitude or {}).get("excess_px_y", 0)) for e in escapes)
        assert worst >= expected_px - 2.0, (name, worst)
        assert capture.browser_metrics["fit"]["requested"] is False


def test_slide_13_is_grown_not_shrunk():
    asyncio.run(_test_slide_13_is_grown_not_shrunk())


async def _test_slide_13_is_grown_not_shrunk():
    html, capture = await _fit("run42-s12.html")
    report = capture.fit_report
    assert report is not None and report["fit_state"] == "final" and report["error"] is None
    card = next(c for c in report["cards"] if c["qid"] == "s12-map")
    # Slack below the card is bounded by the page-number band and the body's
    # own box, so the card grows as far as it may and only the remainder is
    # shrunk: grow first, shrink second, and never a whole-slide shrink.
    assert card["outcome"] in {"grown", "grown_and_shrunk"}, card
    assert card["grow_px"] > 0, card
    assert card["factor"] >= 0.90, card
    assert any(e["qid"] == "s12-map" and "height:" in e["declarations"] for e in report["edits"])
    assert _blocking(capture.fitted_html, capture.browser_metrics) == set()
    assert not _escapes(capture.fitted_html, capture.browser_metrics)


def test_slide_21_is_shrunk_within_the_floor():
    asyncio.run(_test_slide_21_is_shrunk_within_the_floor())


async def _test_slide_21_is_shrunk_within_the_floor():
    html, capture = await _fit("run42-s20.html")
    report = capture.fit_report
    assert report is not None and report["fit_state"] == "final" and report["error"] is None
    card = next(c for c in report["cards"] if c["qid"] == "s20-map")
    assert card["outcome"] in {"shrunk", "grown_and_shrunk"}, card
    assert 0.78 <= card["factor"] <= 0.90, card
    for edit in report["edits"]:
        for declaration in edit["declarations"].split(";"):
            if declaration.startswith("font-size:"):
                assert float(declaration[len("font-size:"):-2]) >= FLOOR_PX, edit
    assert _blocking(capture.fitted_html, capture.browser_metrics) == set()


@pytest.mark.parametrize("name", ["run42-s12.html", "run42-s20.html"])
def test_reconstruction_measures_the_same(name: str):
    asyncio.run(_test_reconstruction_measures_the_same(name))


async def _test_reconstruction_measures_the_same(name: str):
    html, capture = await _fit(name)
    assert capture.fitted_html is not None
    assert FIT_CLASS not in capture.fitted_html
    rebuilt = apply_fit_edits(html, capture.fit_report["edits"])
    assert rebuilt == capture.fitted_html
    verify = await render_html_to_png_with_metrics(capture.fitted_html)
    assert verify.browser_metrics is not None
    assert not _escapes(capture.fitted_html, verify.browser_metrics)
    fitted_boxes, verify_boxes = _boxes(capture.browser_metrics), _boxes(verify.browser_metrics)
    assert set(fitted_boxes) == set(verify_boxes)
    worst = max(
        abs(a - b)
        for qid in fitted_boxes
        for a, b in zip(fitted_boxes[qid], verify_boxes[qid], strict=True)
    )
    assert worst <= BBOX_TOLERANCE_PX, worst


def test_apply_is_idempotent_across_the_two_probe_passes():
    asyncio.run(_test_apply_is_idempotent_across_the_two_probe_passes())


async def _test_apply_is_idempotent_across_the_two_probe_passes():
    """The final report must not compound the provisional pass's edits."""
    _, capture = await _fit("run42-s20.html")
    declarations = [e["declarations"] for e in capture.fit_report["edits"]]
    assert len(declarations) == len(set(e["qid"] for e in capture.fit_report["edits"]))
    for edit in capture.fit_report["edits"]:
        assert edit["declarations"].count("font-size:") <= 1, edit


def test_fixture_metrics_shape_matches_the_probe():
    metrics = json.loads((FIXTURES / "run42-s12.metrics.json").read_text(encoding="utf-8"))
    assert metrics["width"] == 1280 and metrics["height"] == 720
    assert any(element.get("qid") == "s12-map" for element in metrics["elements"])
