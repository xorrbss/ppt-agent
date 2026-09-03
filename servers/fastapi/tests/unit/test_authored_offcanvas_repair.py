"""Clamping a measured overhang costs no provider call and changes nothing else.

The recurring authoring mistake is arithmetic: a wrapper inset from the edge is
still given the full 1280px canvas width, so it and its children run past the
right edge by exactly the inset. Slide 30 of operation 02aaadec did it with a
body wrapper at x=64, carrying ten elements out of the frame with it, after the
bounded repair round had already tried and failed.
"""

from __future__ import annotations

from services.authored_offcanvas_repair import (
    overhanging_qids,
    repair_authored_offcanvas_width,
)


def _element(qid: str, *, x: float, width: float, y: float = 100.0, height: float = 40.0):
    return {"qid": qid, "bbox": {"x": x, "y": y, "width": width, "height": height}}


def _metrics(*elements):
    return {"viewport": {"width": 1280, "height": 720}, "elements": list(elements)}


HTML = (
    "<html><head><style>.body-wrap{width:1280px}</style></head><body>"
    '<div class="body-wrap" data-qid="wrap"><p data-qid="copy">본문</p></div>'
    "</body></html>"
)


def test_only_the_elements_measured_outside_the_canvas_are_named() -> None:
    metrics = _metrics(
        _element("wrap", x=64, width=1280),
        _element("copy", x=88, width=1000),
        # Full-bleed by design: it starts at the edge, so its width is right.
        _element("s.hero", x=0, width=1280),
        # Sub-pixel layout noise is not an overhang.
        _element("s.rule", x=64, width=1216.2),
    )
    assert overhanging_qids(metrics) == ("wrap",)


def test_the_clamp_is_appended_as_css_and_touches_nothing_else() -> None:
    metrics = _metrics(_element("wrap", x=64, width=1280))
    repaired = repair_authored_offcanvas_width(HTML, metrics)
    assert '[data-qid="wrap"]' in repaired
    assert "width: auto !important" in repaired
    assert "max-width: 100% !important" in repaired
    assert "box-sizing: border-box !important" in repaired
    # The authored rule is left as written; the clamp simply comes after it.
    assert ".body-wrap{width:1280px}" in repaired
    assert repaired.index(".body-wrap{width:1280px}") < repaired.index('[data-qid="wrap"]')
    assert "본문" in repaired
    assert repaired.count("</style>") == 1


def test_a_slide_that_fits_is_returned_unchanged() -> None:
    metrics = _metrics(_element("wrap", x=64, width=1152))
    assert repair_authored_offcanvas_width(HTML, metrics) == HTML
    assert repair_authored_offcanvas_width(HTML, {}) == HTML
    assert repair_authored_offcanvas_width(HTML, None) == HTML


def test_a_document_without_a_style_block_still_receives_the_clamp() -> None:
    bare = '<html><head></head><body><div data-qid="wrap">본문</div></body></html>'
    repaired = repair_authored_offcanvas_width(
        bare, _metrics(_element("wrap", x=64, width=1280))
    )
    assert "<style>" in repaired and '[data-qid="wrap"]' in repaired
    assert repaired.index("<style>") < repaired.index("</head>")


def test_repairing_twice_is_stable() -> None:
    metrics = _metrics(_element("wrap", x=64, width=1280))
    once = repair_authored_offcanvas_width(HTML, metrics)
    twice = repair_authored_offcanvas_width(once, metrics)
    # The second pass appends its own rule; both are identical and idempotent
    # in effect, and the measured metrics are what decide either way.
    assert twice.count('[data-qid="wrap"]') == 2
    assert "본문" in twice
