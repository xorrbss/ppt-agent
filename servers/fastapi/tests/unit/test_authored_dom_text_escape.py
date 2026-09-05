"""Text that prints outside its card must block publication.

The two fixtures are the browser payloads bench run 29 (A-med, 2026-09-05)
actually sealed, trimmed to the keys the gate reads and verified to produce the
same report as the untrimmed originals:

* ``run29_slide17_text_escape`` -- the published defect.  Two paragraphs in the
  right column print below their cards; one of them lands on the next card.
* ``run29_slide05_container_clip`` -- the control.  It carries the same
  ``clipped: true`` container mark slide 17 has (``s06.layer.system``) and is
  visually correct, so "a container reports a clip" cannot be the signal.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.authored_composition_policy import blocking_safety_issues
from services.authored_repair_round_order import (
    TEXT_SCALE_MAX_FACTOR,
    TEXT_SCALE_MIN_FACTOR,
    text_scale_factor,
)
from utils.authored_dom_quality import (
    EXACT_RECONSTRUCTION,
    inspect_authored_dom,
    normalize_browser_metrics,
)
from utils.authored_dom_text_escape import (
    BLOCKING_SAFETY_CATEGORIES,
    TEXT_ESCAPE_CATEGORY,
    find_text_escapes,
    text_escape_detail,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "authored_dom_text_escape"


def _metrics(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.metrics.json").read_text(encoding="utf-8"))


def _escapes(name: str):
    return find_text_escapes(normalize_browser_metrics(_metrics(name))["elements"])


def _card(
    qid: str,
    *,
    y: float,
    height: float,
    x: float = 0.0,
    width: float = 200.0,
    parent: str = "",
) -> dict:
    return {
        "qid": qid,
        "tag": "section",
        "parent_qid": parent,
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "computed_style": {"backgroundColor": "rgb(255, 255, 255)"},
        "text_fragments": [],
    }


def _text(
    qid: str,
    *,
    parent: str,
    lines: list[tuple[float, float, float, float]],
    font_size: float = 18.0,
) -> dict:
    """A text owner whose line rects are given as ``(x, y, width, height)``."""

    fragments = [
        {"x": x, "y": y, "width": width, "height": height}
        for x, y, width, height in lines
    ]
    top = min(fragment["y"] for fragment in fragments)
    bottom = max(fragment["y"] + fragment["height"] for fragment in fragments)
    left = min(fragment["x"] for fragment in fragments)
    right = max(fragment["x"] + fragment["width"] for fragment in fragments)
    return {
        "qid": qid,
        "tag": "p",
        "parent_qid": parent,
        "bbox": {"x": left, "y": top, "width": right - left, "height": bottom - top},
        "computed_style": {"backgroundColor": "rgba(0, 0, 0, 0)"},
        "has_owned_text": True,
        "font_size": font_size,
        "text_fragments": fragments,
    }


# ---------------------------------------------------------------------------
# The measured defect
# ---------------------------------------------------------------------------


def test_run29_slide17_reports_both_escaping_paragraphs():
    escapes = {escape.qid: escape for escape in _escapes("run29_slide17_text_escape")}

    assert set(escapes) == {"s18-accept-evidence", "s18-stable-scope"}
    evidence = escapes["s18-accept-evidence"]
    assert evidence.card_qid == "s18-accept-card"
    assert evidence.escape_px_bottom == pytest.approx(25.0, abs=0.5)
    # The browser's own paint probe names the next card as the occluder, so the
    # detail can say which card the copy disappeared under.
    assert evidence.collides_with == ("s18-stable-card",)
    scope = escapes["s18-stable-scope"]
    assert scope.card_qid == "s18-stable-card"
    assert scope.escape_px_bottom == pytest.approx(13.0, abs=0.5)


def test_run29_slide17_blocks_publication():
    report = inspect_authored_dom("<div></div>", _metrics("run29_slide17_text_escape"))
    blocking = blocking_safety_issues(report)

    assert {issue.category for issue in blocking} == {TEXT_ESCAPE_CATEGORY}
    assert {issue.qids[0] for issue in blocking} == {
        "s18-accept-evidence",
        "s18-stable-scope",
    }
    # High only where the escaped copy actually lands under another card.
    by_qid = {issue.qids[0]: issue for issue in blocking}
    assert by_qid["s18-accept-evidence"].severity == "high"
    assert by_qid["s18-stable-scope"].severity == "medium"


def test_run29_slide17_detail_names_the_owner_and_its_card():
    detail = text_escape_detail(
        next(
            escape
            for escape in _escapes("run29_slide17_text_escape")
            if escape.qid == "s18-accept-evidence"
        )
    )

    assert "s18-accept-evidence" in detail
    assert "s18-accept-card" in detail
    assert "s18-stable-card" in detail


# ---------------------------------------------------------------------------
# The control: a container clip is not the signal
# ---------------------------------------------------------------------------


def test_run29_slide05_container_clip_is_not_a_text_escape():
    metrics = _metrics("run29_slide05_container_clip")
    clipped = {
        item["qid"] for item in metrics["elements"] if item.get("clipped") is True
    }

    # The control really does carry the container clip mark slide 17 has.
    assert clipped == {"s06.layer.system"}
    assert _escapes("run29_slide05_container_clip") == ()

    report = inspect_authored_dom("<div></div>", metrics)
    assert blocking_safety_issues(report) == ()


# ---------------------------------------------------------------------------
# The rung: this defect is one shrinking type can fix
# ---------------------------------------------------------------------------


def test_escape_feeds_fit_summary_and_bounds_the_text_scale_rung():
    report = inspect_authored_dom("<div></div>", _metrics("run29_slide17_text_escape"))

    assert report.fit_summary["worst_overflow_ratio"] > 1.0
    assert report.fit_summary["overflow_kinds"] == {"text": 2}

    factor = text_scale_factor(
        [issue.to_dict() for issue in blocking_safety_issues(report)]
    )
    assert factor is not None
    assert TEXT_SCALE_MIN_FACTOR <= factor <= TEXT_SCALE_MAX_FACTOR
    assert factor == pytest.approx(1.0 / report.fit_summary["worst_overflow_ratio"])


def test_clean_slide_leaves_the_rung_alone():
    report = inspect_authored_dom("<div></div>", _metrics("run29_slide05_container_clip"))

    assert report.fit_summary["worst_overflow_ratio"] == 1.0
    assert text_scale_factor([issue.to_dict() for issue in report.issues]) is None


def test_new_category_is_a_blocking_safety_category():
    assert TEXT_ESCAPE_CATEGORY in BLOCKING_SAFETY_CATEGORIES
    assert {"overflow", "clipping", "off_canvas", "overlap"} <= BLOCKING_SAFETY_CATEGORIES


# ---------------------------------------------------------------------------
# Detector behaviour on synthetic geometry
# ---------------------------------------------------------------------------


def test_escape_within_tolerance_is_not_reported():
    elements = [
        _card("card", y=100.0, height=100.0),
        # Line box bottom 202, em box bottom 199: inside the card at 200.
        _text("copy", parent="card", lines=[(0.0, 176.0, 150.0, 26.0)]),
    ]

    assert find_text_escapes(elements) == ()


def test_line_leading_alone_is_not_an_escape():
    elements = [
        _card("card", y=100.0, height=100.0),
        # Line box bottom 204 (4 px past the card) but em box bottom 200.
        _text("copy", parent="card", lines=[(0.0, 178.0, 150.0, 26.0)]),
    ]

    assert find_text_escapes(elements) == ()


def test_glyphs_below_the_card_are_reported():
    elements = [
        _card("card", y=100.0, height=100.0),
        _text("copy", parent="card", lines=[(0.0, 195.0, 150.0, 26.0)]),
    ]

    escape = find_text_escapes(elements)[0]
    assert escape.qid == "copy"
    assert escape.card_qid == "card"
    assert escape.escape_px_bottom == pytest.approx(17.0)
    assert escape.escape_px_right == 0.0
    assert escape.ratio_y == pytest.approx(1.17)


def test_glyphs_past_the_right_edge_are_reported():
    elements = [
        _card("card", y=100.0, height=100.0, width=200.0),
        _text("copy", parent="card", lines=[(0.0, 120.0, 260.0, 26.0)]),
    ]

    escape = find_text_escapes(elements)[0]
    assert escape.escape_px_right == pytest.approx(60.0)
    assert "past the right edge of" in text_escape_detail(escape)


def test_text_with_no_card_ancestor_is_left_alone():
    elements = [
        {
            "qid": "wrapper",
            "tag": "div",
            "parent_qid": "",
            "bbox": {"x": 0.0, "y": 100.0, "width": 200.0, "height": 100.0},
            "computed_style": {"backgroundColor": "rgba(0, 0, 0, 0)"},
            "text_fragments": [],
        },
        _text("copy", parent="wrapper", lines=[(0.0, 195.0, 150.0, 26.0)]),
    ]

    assert find_text_escapes(elements) == ()


def test_a_single_divider_border_is_not_a_card():
    """A 1px top rule between paragraphs must not become the nearest card."""

    divider = _card("divider", y=100.0, height=40.0)
    divider["computed_style"] = {
        "backgroundColor": "rgba(0, 0, 0, 0)",
        "borderTopWidth": "1px",
    }
    elements = [
        _card("card", y=90.0, height=200.0),
        {**divider, "parent_qid": "card"},
        _text("copy", parent="divider", lines=[(0.0, 150.0, 150.0, 26.0)]),
    ]

    assert find_text_escapes(elements) == ()


def test_intentional_owner_is_left_to_vqa():
    text = _text("copy", parent="card", lines=[(0.0, 195.0, 150.0, 26.0)])
    text["intentional"] = True

    assert find_text_escapes([_card("card", y=100.0, height=100.0), text]) == ()


def test_exact_reconstruction_reports_only_marked_defects():
    metrics = _metrics("run29_slide17_text_escape")
    report = inspect_authored_dom(
        "<div></div>", metrics, reference_mode=EXACT_RECONSTRUCTION
    )

    assert not [
        issue for issue in report.issues if issue.category == TEXT_ESCAPE_CATEGORY
    ]


# ---------------------------------------------------------------------------
# The remake brief carries the measurement
# ---------------------------------------------------------------------------


def test_remake_brief_carries_the_measured_defects(monkeypatch):
    import asyncio

    from services import authored_terminal_slide_fallback as terminal_mod

    captured: dict[str, object] = {}

    async def fake_revise(*args, **kwargs):
        captured["critiques"] = kwargs.get("initial_critiques")
        return [["<html>remade</html>"], [b"png"], [0], [{"path": "full_reauthor"}]]

    monkeypatch.setattr(terminal_mod, "revise_authored_deck", fake_revise)
    defects = [
        SimpleNamespace(
            category=TEXT_ESCAPE_CATEGORY,
            detail=(
                "text of s18-accept-evidence prints 25px below the bottom of its "
                "card s18-accept-card, over s18-stable-card"
            ),
        )
    ]

    asyncio.run(
        terminal_mod._remake_candidate(
            index=0, html="<html></html>", png=b"png", content="topic", role="body",
            brand=None, design_system="", style=None, total_slides=1,
            defects=defects, reference_kwargs={},
        )
    )

    details = [issue.detail for issue in captured["critiques"][0].issues]
    # The hint alone is longer than one issue's 200-character budget, which is
    # why the measurement has to travel as its own issue rather than appended.
    assert len(terminal_mod.LOWER_DENSITY_HINT) > 200
    assert "s18-accept-evidence" in details[0]
    assert "s18-accept-card" in details[0]
    assert details[1].startswith("Rebuild this slide")
