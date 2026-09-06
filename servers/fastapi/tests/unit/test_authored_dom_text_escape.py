"""Text that prints outside its card must block publication.

The two fixtures are the browser payloads bench run 29 (A-med, 2026-09-05)
actually sealed, trimmed to the keys the gate reads and verified to produce the
same report as the untrimmed originals:

* ``run29_slide17_text_escape`` -- the published defect.  Two paragraphs in the
  right column print below their cards; one of them lands on the next card.
* ``run29_slide05_container_clip`` -- the control.  It carries the same
  ``clipped: true`` container mark slide 17 has (``s06.layer.system``) and is
  visually correct, so "a container reports a clip" cannot be the signal.

Two more fixtures cover ``utils.authored_dom_footer_zone`` -- a sibling
detector for a different published defect, bench run 33 (P0, ``c907f094a``,
2026-09-05), also trimmed to the keys the gate reads:

* ``run33_slide02_footer_zone`` -- the published defect.  A "검증" callout's
  text prints under the page number "03": ``text_escapes_card`` cannot see it
  because both text owners' ink stays *inside* their own card (``s2-takeaway``,
  which itself runs to y=727); nothing there compares any element against the
  deck's own footer safe line, only against the nearest painted ancestor.
* ``run33_slide03_footer_zone_clean`` -- the control.  A similar bottom panel
  on the following slide stops flush with the footer line and must not trip
  the new detector.

The two detectors have different STATUS, which these tests pin.
``text_escapes_card`` blocks publication.  ``text_intrudes_footer_zone`` is
detected and recorded but advisory: the offline replay of three stored decks
found its distribution indistinguishable between the deck the product owner
preferred and the ones they did not (medians 28/26/30 px), so it cannot flag a
slide, spend a remake or hold a deck back.  See
``utils/authored_dom_categories.py`` for the measurement.
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
from utils.authored_dom_footer_zone import (
    FOOTER_ZONE_CATEGORY,
    find_footer_zone_intrusions,
)
from utils.authored_dom_categories import (
    ADVISORY_ONLY_CATEGORIES,
    BLOCKING_SAFETY_CATEGORIES,
)
from utils.authored_dom_text_escape import (
    TEXT_ESCAPE_CATEGORY,
    find_text_escapes,
    reconcile_footer_zone_reports,
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


# ---------------------------------------------------------------------------
# The footer safe zone: run 33's published callout under the page number
# ---------------------------------------------------------------------------


def test_run33_slide02_reports_both_intruding_owners():
    """Still DETECTED, with the same measurement: only its status changed."""

    report = inspect_authored_dom("<div></div>", _metrics("run33_slide02_footer_zone"))
    footer = [
        issue for issue in report.issues if issue.category == FOOTER_ZONE_CATEGORY
    ]

    by_qid = {issue.qids[0]: issue for issue in footer}
    assert set(by_qid) == {"s2-close", "s2-word"}
    # Both the paragraph and the "검증" word print under the page number, so
    # both name it and both are high: the browser boxes actually overlap.
    for issue in by_qid.values():
        assert issue.qids == (issue.qids[0], "s2-page")
        assert issue.severity == "high"
        assert "s2-page" in issue.detail
    # The real 57px intrusion is on the record, in px.
    assert by_qid["s2-close"].magnitude["excess_px_y"] == pytest.approx(57.0)


def test_run33_slide02_does_not_block_publication():
    """The advisory demotion, at the seam every caller goes through.

    ``blocking_safety_issues`` is what builds a slide's gate failure, and a
    gate failure is what flags the slide, spends a remake and holds the deck.
    A footer-only slide produces none of it.
    """

    report = inspect_authored_dom("<div></div>", _metrics("run33_slide02_footer_zone"))

    assert blocking_safety_issues(report) == ()
    assert FOOTER_ZONE_CATEGORY not in BLOCKING_SAFETY_CATEGORIES
    assert FOOTER_ZONE_CATEGORY in ADVISORY_ONLY_CATEGORIES


def test_run33_slide03_is_not_a_footer_zone_intrusion():
    metrics = _metrics("run33_slide03_footer_zone_clean")

    report = inspect_authored_dom("<div></div>", metrics)
    assert blocking_safety_issues(report) == ()
    assert (
        find_footer_zone_intrusions(
            normalize_browser_metrics(metrics)["elements"],
            viewport_width=1280.0,
            viewport_height=720.0,
        )
        == ()
    )


def test_footer_zone_intrusion_feeds_fit_summary_and_keeps_its_rung_wiring():
    """The magnitude and the text-scale wiring survive the demotion intact.

    The rung reads the gate failures, and those carry only
    ``blocking_safety_issues``, so an advisory category cannot reach it today.
    The wiring is asserted here against the issues directly, so re-promoting
    the category stays the one-line deletion from ``ADVISORY_ONLY_CATEGORIES``.
    """

    report = inspect_authored_dom("<div></div>", _metrics("run33_slide02_footer_zone"))

    assert report.fit_summary["worst_overflow_ratio"] > 1.0
    assert report.fit_summary["overflow_kinds"] == {"text": 2}

    # What the rung would be handed if the category were promoted again.
    footer_issues = [
        issue.to_dict()
        for issue in report.issues
        if issue.category == FOOTER_ZONE_CATEGORY
    ]
    factor = text_scale_factor(footer_issues)
    assert factor is not None
    assert TEXT_SCALE_MIN_FACTOR <= factor <= TEXT_SCALE_MAX_FACTOR
    assert factor == pytest.approx(1.0 / report.fit_summary["worst_overflow_ratio"])

    # What it is actually handed today: nothing.
    assert text_scale_factor(
        [issue.to_dict() for issue in blocking_safety_issues(report)]
    ) is None


def test_footer_zone_category_is_advisory_not_blocking():
    assert ADVISORY_ONLY_CATEGORIES == {FOOTER_ZONE_CATEGORY}
    assert BLOCKING_SAFETY_CATEGORIES == {
        "overflow",
        "clipping",
        "off_canvas",
        "overlap",
        TEXT_ESCAPE_CATEGORY,
    }


def test_run29_fixtures_are_unaffected_by_the_footer_zone_detector():
    """The escape-only fixtures both carry a page-number element at y=690.

    ``s18-stable-scope``'s ink lands exactly 2px past the 648px line -- inside
    tolerance -- and ``s18-accept-evidence`` lands well above it, so this new
    detector must add nothing to either report.
    """

    for name in ("run29_slide17_text_escape", "run29_slide05_container_clip"):
        elements = normalize_browser_metrics(_metrics(name))["elements"]
        assert (
            find_footer_zone_intrusions(
                elements, viewport_width=1280.0, viewport_height=720.0
            )
            == ()
        )


# ---------------------------------------------------------------------------
# The detector: synthetic geometry
# ---------------------------------------------------------------------------


def _page_number(qid: str = "page", *, bottom_gap: float = 14.0) -> dict:
    height = 16.0
    y = 720.0 - bottom_gap - height
    return {
        "qid": qid,
        "tag": "div",
        "bbox": {"x": 0.0, "y": y, "width": 1280.0, "height": height},
        "font_size": 12.0,
        "rendered_text": "03",
        "text_fragments": [{"x": 600.0, "y": y - 1.0, "width": 20.0, "height": 17.0}],
    }


def _text_owner(
    qid: str, *, bottom: float, height: float = 26.0, x: float = 100.0, width: float = 200.0
) -> dict:
    """A text owner whose one fragment's own em box (height == font size) ends at ``bottom``."""

    y = bottom - height
    return {
        "qid": qid,
        "tag": "p",
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "font_size": height,
        "text_fragments": [{"x": x, "y": y, "width": width, "height": height}],
    }


def test_page_number_element_is_never_flagged_itself():
    elements = [_page_number()]

    assert (
        find_footer_zone_intrusions(elements, viewport_width=1280.0, viewport_height=720.0)
        == ()
    )


def test_text_past_the_numbered_line_is_reported():
    elements = [_page_number(), _text_owner("owner", bottom=651.0)]

    result = find_footer_zone_intrusions(
        elements, viewport_width=1280.0, viewport_height=720.0
    )

    assert len(result) == 1
    assert result[0].qid == "owner"
    assert result[0].page_number_qid == "page"
    assert result[0].content_bottom_px == pytest.approx(648.0)
    assert result[0].intrusion_px == pytest.approx(3.0)


def test_text_within_tolerance_of_the_numbered_line_is_not_reported():
    elements = [_page_number(), _text_owner("owner", bottom=649.5)]

    assert (
        find_footer_zone_intrusions(elements, viewport_width=1280.0, viewport_height=720.0)
        == ()
    )


def test_without_a_page_number_the_line_falls_back_to_688():
    within = [_text_owner("owner", bottom=690.0)]
    assert (
        find_footer_zone_intrusions(within, viewport_width=1280.0, viewport_height=720.0)
        == ()
    )

    past = [_text_owner("owner", bottom=696.0)]
    result = find_footer_zone_intrusions(
        past, viewport_width=1280.0, viewport_height=720.0
    )
    assert len(result) == 1
    assert result[0].content_bottom_px == pytest.approx(688.0)
    assert result[0].page_number_qid == ""


def test_intersects_page_number_true_when_boxes_overlap():
    elements = [_page_number(), _text_owner("owner", bottom=700.0)]

    result = find_footer_zone_intrusions(
        elements, viewport_width=1280.0, viewport_height=720.0
    )

    assert len(result) == 1
    assert result[0].intersects_page_number is True


def test_intersects_page_number_false_when_boxes_do_not_overlap():
    # Ink at y=660 clears the 648 line but its own box (650-660) sits above
    # the page number's box (690-706): no overlap even though both are late.
    elements = [_page_number(), _text_owner("owner", bottom=660.0, height=10.0)]

    result = find_footer_zone_intrusions(
        elements, viewport_width=1280.0, viewport_height=720.0
    )

    assert len(result) == 1
    assert result[0].intersects_page_number is False


def test_a_narrow_box_is_not_mistaken_for_the_page_number():
    """Only a full-width box near the bottom is treated as page furniture."""

    narrow = dict(_page_number())
    narrow["bbox"] = {"x": 500.0, "y": 690.0, "width": 100.0, "height": 16.0}

    result = find_footer_zone_intrusions(
        [narrow], viewport_width=1280.0, viewport_height=720.0
    )

    # Not recognized as the page number, so it is measured as ordinary text
    # against the unnumbered 688px line instead.
    assert len(result) == 1
    assert result[0].qid == "page"
    assert result[0].page_number_qid == ""
    assert result[0].content_bottom_px == pytest.approx(688.0)


# ---------------------------------------------------------------------------
# One element, one report
# ---------------------------------------------------------------------------


def _reconciled(elements: list[dict]):
    return reconcile_footer_zone_reports(
        find_text_escapes(elements),
        find_footer_zone_intrusions(
            elements, viewport_width=1280.0, viewport_height=720.0
        ),
    )


def test_a_legal_card_wins_over_the_footer_line_for_the_same_owner():
    """Bench run 36 double-reported 6 owners across 3 of its 20 slides.

    ``s10-integration`` escaped its card by 25px and crossed the 648px line by
    exactly the same 25px, because the card's own bottom edge sits on the line.
    Putting the copy back inside the card is the whole fix, so the card escape
    is the report that survives.
    """

    card = _card("card", y=400.0, height=240.0)  # bottom 640: above the line
    text = _text("copy", parent="card", lines=[(10.0, 674.0, 100.0, 18.0)], font_size=18.0)

    escapes, intrusions = _reconciled([_page_number(), card, text])

    assert [escape.qid for escape in escapes] == ["copy"]
    assert escapes[0].card_bottom_px == pytest.approx(640.0)
    assert intrusions == ()


def test_a_card_below_the_footer_line_keeps_the_footer_report():
    """When the card itself is illegal, fitting the copy into it is not a fix."""

    card = _card("card", y=560.0, height=140.0)  # bottom 700: below the line
    text = _text("copy", parent="card", lines=[(10.0, 692.0, 100.0, 18.0)], font_size=18.0)

    escapes, intrusions = _reconciled([_page_number(), card, text])

    assert escapes == ()
    assert [intrusion.qid for intrusion in intrusions] == ["copy"]
    assert intrusions[0].intrusion_px == pytest.approx(62.0)


def test_reconciliation_leaves_unrelated_reports_alone():
    """Two different owners keep two different reports."""

    card = _card("card", y=400.0, height=240.0)
    escaping = _text("copy", parent="card", lines=[(10.0, 674.0, 100.0, 18.0)])
    low = _text_owner("other", bottom=700.0, x=600.0)

    escapes, intrusions = _reconciled([_page_number(), card, escaping, low])

    assert [escape.qid for escape in escapes] == ["copy"]
    assert [intrusion.qid for intrusion in intrusions] == ["other"]


def test_run33_slide02_is_untouched_by_the_reconciliation():
    """Its two owners' ink stays inside their cards, so nothing is suppressed."""

    elements = normalize_browser_metrics(_metrics("run33_slide02_footer_zone"))["elements"]
    escapes, intrusions = _reconciled(elements)

    assert escapes == ()
    assert sorted(intrusion.qid for intrusion in intrusions) == ["s2-close", "s2-word"]
