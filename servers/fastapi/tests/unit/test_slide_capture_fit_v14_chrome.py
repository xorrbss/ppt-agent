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
