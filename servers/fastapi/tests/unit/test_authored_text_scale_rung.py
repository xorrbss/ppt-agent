"""H3: the deterministic text-scale rung in front of remake #1.

See ``docs/authored-quality-speed-programme-design-20260905.md`` section 6, H3.
The rung must rescue a slide whose only blocking defects are fit, must never
run for a slide whose defects a smaller font cannot fix, and must leave a slide
it fails to rescue byte-identical for the remake behind it.
"""

from __future__ import annotations

import asyncio

import pytest

from services import authored_repair_round_order as order_mod

_FIT_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
    "<style>.body{font-size:40px;line-height:48px}</style>"
    "</head><body><p class='body'>too tall</p></body></html>"
)


def _run(coro):
    return asyncio.run(coro)


def _overflow_issue(ratio: float = 1.25) -> dict[str, object]:
    return {
        "category": "overflow",
        "detail": "text overflows its box",
        "severity": "high",
        "magnitude": {"element_kind": "text", "ratio_x": 1.0, "ratio_y": ratio},
    }


def _overlap_issue() -> dict[str, object]:
    return {"category": "overlap", "detail": "two boxes overlap", "severity": "high"}


# ---------------------------------------------------------------------------
# text_scale_factor
# ---------------------------------------------------------------------------


def test_the_factor_is_the_inverse_of_the_worst_overflow_ratio():
    assert order_mod.text_scale_factor([_overflow_issue(1.25)]) == pytest.approx(0.8)


def test_the_factor_is_clamped_to_the_rungs_range():
    # A tiny overflow would ask for a scale so close to 1 it is not worth a
    # render; a catastrophic one would ask for a page nobody can read.
    assert order_mod.text_scale_factor([_overflow_issue(1.01)]) == pytest.approx(
        order_mod.TEXT_SCALE_MAX_FACTOR
    )
    assert order_mod.text_scale_factor([_overflow_issue(4.0)]) == pytest.approx(
        order_mod.TEXT_SCALE_MIN_FACTOR
    )


def test_an_overlap_or_off_canvas_defect_skips_the_rung_entirely():
    assert order_mod.text_scale_factor([_overflow_issue(), _overlap_issue()]) is None
    assert (
        order_mod.text_scale_factor(
            [{"category": "off_canvas", "magnitude": {"overshoot_px_right": 40}}]
        )
        is None
    )


def test_a_clipping_only_slide_has_no_ratio_to_bound_the_scale_with():
    assert order_mod.text_scale_factor([{"category": "clipping", "detail": "cut"}]) is None


def test_no_issues_means_no_rung():
    assert order_mod.text_scale_factor([]) is None


def test_gate_issues_by_slide_keeps_the_magnitude_the_defect_projection_drops():
    issues = order_mod.gate_issues_by_slide(
        [
            {"slide_index": 2, "issues": [_overflow_issue(1.5)]},
            {"slide_index": "x", "issues": [_overflow_issue()]},
            {"slide_index": 2, "issues": ["not a mapping"]},
        ]
    )

    assert sorted(issues) == [2]
    assert issues[2][0]["magnitude"]["ratio_y"] == 1.5


# ---------------------------------------------------------------------------
# The rung inside the remake-first round
# ---------------------------------------------------------------------------


class _Profile:
    profile_id = "ordinary"


def _round_kwargs(htmls, pngs, issues):
    return {
        "flagged": [0],
        "gate_failures": [{"slide_index": 0, "issues": issues}],
        "htmls": htmls,
        "pngs": pngs,
        "contents": ["content"],
        "roles": ["CONTENT"],
        "brand": object(),
        "design_system": "ds",
        "style": None,
        "capacity_plan": None,
        "profile": _Profile(),
        "reference_mode": "style_reference",
        "scene_envelope": "envelope-0",
        "metrics_by_slide": {},
    }


def _install(monkeypatch, *, admits: bool, remakes: bool = False):
    calls: dict[str, list] = {"admit": [], "remake": []}

    async def fake_admit(*, candidate_html, capacity, profile, reference_mode):
        calls["admit"].append(candidate_html)
        return (candidate_html, b"png", {"ok": True}) if admits else None

    async def fake_apply(*, index, admitted, htmls, pngs, metrics_by_slide, scene_envelope, generation_job=None):
        html, png, metrics = admitted
        htmls[index] = html
        pngs[index] = png
        metrics_by_slide[index] = metrics
        return "envelope-1"

    async def fake_remake_once(**kwargs):
        calls["remake"].append(kwargs["index"])
        return ((_FIT_HTML, b"remade", {}), "remade") if remakes else (None, "remake_gate_failed")

    monkeypatch.setattr(order_mod, "_admit_remade_slide", fake_admit)
    monkeypatch.setattr(order_mod, "apply_admitted_remake", fake_apply)
    monkeypatch.setattr(order_mod, "remake_slide_once", fake_remake_once)
    return calls


def test_a_rescued_slide_is_accepted_and_never_reaches_the_remake(monkeypatch):
    calls = _install(monkeypatch, admits=True)
    htmls, pngs = [_FIT_HTML], [b"png"]

    outcome = _run(
        order_mod.remake_first_flagged_slides(
            **_round_kwargs(htmls, pngs, [_overflow_issue()])
        )
    )

    assert outcome.text_scaled_slide_indices == (0,)
    assert outcome.remade_slide_indices == ()
    assert outcome.remaining_flagged == []
    # No provider call was made, so no remake budget was spent.
    assert calls["remake"] == []
    assert outcome.remake_counts == {}
    assert outcome.scene_envelope == "envelope-1"
    # The published HTML is the scaled one.
    assert "font-size:32px" in htmls[0]


def test_a_rung_that_does_not_admit_leaves_the_slide_untouched_for_the_remake(monkeypatch):
    calls = _install(monkeypatch, admits=False, remakes=False)
    htmls, pngs = [_FIT_HTML], [b"png"]

    outcome = _run(
        order_mod.remake_first_flagged_slides(
            **_round_kwargs(htmls, pngs, [_overflow_issue()])
        )
    )

    assert len(calls["admit"]) == 1
    assert calls["remake"] == [0]
    assert outcome.text_scaled_slide_indices == ()
    assert outcome.remaining_flagged == [0]
    assert htmls == [_FIT_HTML] and pngs == [b"png"]
    assert outcome.scene_envelope == "envelope-0"


def test_the_knob_off_restores_the_remake_first_order_exactly(monkeypatch):
    monkeypatch.setenv("AUTHORED_TEXT_SCALE_RUNG_ENABLED", "false")
    calls = _install(monkeypatch, admits=True, remakes=True)
    htmls, pngs = [_FIT_HTML], [b"png"]

    outcome = _run(
        order_mod.remake_first_flagged_slides(
            **_round_kwargs(htmls, pngs, [_overflow_issue()])
        )
    )

    assert calls["admit"] == []
    assert calls["remake"] == [0]
    assert outcome.text_scaled_slide_indices == ()
    assert outcome.remade_slide_indices == (0,)


def test_an_overlap_slide_goes_straight_to_the_remake(monkeypatch):
    calls = _install(monkeypatch, admits=True, remakes=True)
    htmls, pngs = [_FIT_HTML], [b"png"]

    outcome = _run(
        order_mod.remake_first_flagged_slides(
            **_round_kwargs(htmls, pngs, [_overlap_issue()])
        )
    )

    assert calls["admit"] == []
    assert calls["remake"] == [0]
    assert outcome.remade_slide_indices == (0,)
