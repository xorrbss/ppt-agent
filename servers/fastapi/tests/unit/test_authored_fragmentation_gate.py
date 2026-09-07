"""The fragmentation gate: text-owner count, type floor, and the road its
violations take into the bounded repair round.

The two synthetic slides mirror the 2026-09-08 measurement: a Sol-like slide
splits its copy across ~90 owners at 14px, an Astra-like slide uses 46 owners
at 18px.  Both carry the same amount of text; only the shape differs.
"""

from __future__ import annotations

from types import SimpleNamespace

from services import authored_presentation_pipeline as pipeline
from services.authored_composition_policy import (
    CompositionProfile,
    capacity_repair_instructions,
)
from services.authored_presentation_generation import _capacity_gate_initial_critiques
from utils.authored_deck_instruments import _fit_aggregates
from utils.authored_fragmentation_gate import (
    FRAGMENTATION_CATEGORY,
    fit_floor_unfixable_cards,
    fit_font_floor_px,
    fragmentation_repair_instruction,
    fragmentation_violations,
    inspect_fragmentation,
)
from utils.get_env_programme import (
    get_authored_fragmentation_max_text_owners,
    get_authored_type_floor_px,
    is_authored_fragmentation_gate_enabled,
)
from utils.slide_capture_fit import fit_probe_prelude
from utils.slide_capture_fit_script import fit_constants, fit_script_fragment

GATE_KNOB = "AUTHORED_FRAGMENTATION_GATE_ENABLED"
OWNERS_KNOB = "AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS"
FLOOR_KNOB = "AUTHORED_TYPE_FLOOR_PX"


def _element(qid: str, text: str, font: float, *, role: str = "body", **extra) -> dict:
    item = {
        "qid": qid,
        "tag": "div",
        "semantic_role": role,
        "has_owned_text": bool(text),
        "rendered_text": text,
        "font_size": font,
        "parent_qid": "root",
        "bbox": {"x": 100, "y": 100, "width": 200, "height": 40},
    }
    item.update(extra)
    return item


def _slide(owner_count: int, font: float) -> dict:
    """``owner_count`` text owners of ``font`` px plus one title at 32px."""

    elements = [_element("title", "Quarterly review", 32, role="title")]
    elements.extend(
        _element(f"s1-o{index}", f"claim {index}", font) for index in range(owner_count)
    )
    return {"viewport": {"width": 1280, "height": 720}, "fonts_ready": True, "elements": elements}


def _sol_like() -> dict:
    return _slide(89, 14.0)


def _astra_like() -> dict:
    return _slide(45, 18.0)


def _gate_on(monkeypatch, *, owners: str | None = None, floor: str | None = None) -> None:
    monkeypatch.setenv(GATE_KNOB, "true")
    for key, value in ((OWNERS_KNOB, owners), (FLOOR_KNOB, floor)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _gate_off(monkeypatch) -> None:
    for key in (GATE_KNOB, OWNERS_KNOB, FLOOR_KNOB):
        monkeypatch.delenv(key, raising=False)


# --- the counter -------------------------------------------------------------


def test_sol_like_slide_violates_both_limits_and_astra_like_passes():
    sol = inspect_fragmentation(_sol_like(), max_text_owners=50, floor_px=16)
    assert sol.text_owners == 90
    assert sol.max_text_owners == 50
    assert len(sol.sub_floor_owners) == 89
    assert all(font == 14.0 for _qid, font in sol.sub_floor_owners)
    assert sol.min_font_px == 14.0
    assert sol.violations == ("text_owners:90>50", "type_floor:89<16")

    astra = inspect_fragmentation(_astra_like(), max_text_owners=50, floor_px=16)
    assert astra.text_owners == 46
    assert astra.sub_floor_owners == ()
    assert astra.min_font_px == 18.0
    assert astra.violations == ()


def test_sub_floor_owners_name_the_qid_and_the_measured_px():
    metrics = _slide(3, 18.0)
    metrics["elements"].append(_element("s1-kicker", "kicker", 13.5, role="supporting"))
    report = inspect_fragmentation(metrics, max_text_owners=50, floor_px=16)
    assert report.sub_floor_owners == (("s1-kicker", 13.5),)
    assert report.violations == ("type_floor:1<16",)
    payload = report.to_dict()
    assert payload["sub_floor_owners"] == [{"qid": "s1-kicker", "font_px": 13.5}]


def test_furniture_decoration_and_textless_elements_are_not_owners():
    metrics = _slide(2, 18.0)
    metrics["elements"].extend(
        [
            # A page number: full-width box pinned to the bottom, digits only, small type.
            _element(
                "s1-page", "3", 12.0, role="body",
                bbox={"x": 0, "y": 690, "width": 1280, "height": 24},
            ),
            _element("s1-footer", "Confidential", 11.0, role="footer"),
            _element("s1-rule", "", 10.0, role="decoration"),
            _element("s1-wrap", "wrapper", 11.0, role="body", has_owned_text=False),
            _element("s1-blank", "   ", 11.0, role="body"),
        ]
    )
    report = inspect_fragmentation(metrics, max_text_owners=50, floor_px=16)
    assert report.text_owners == 3
    assert report.sub_floor_owners == ()
    assert report.violations == ()


def test_owner_without_a_font_size_counts_but_is_not_floored():
    metrics = _slide(1, 18.0)
    metrics["elements"].append(_element("s1-x", "measured late", None))
    report = inspect_fragmentation(metrics, max_text_owners=1, floor_px=16)
    assert report.text_owners == 3
    assert report.sub_floor_owners == ()
    assert report.violations == ("text_owners:3>1",)


def test_missing_metrics_report_nothing():
    report = inspect_fragmentation(None, max_text_owners=50, floor_px=16)
    assert report.text_owners == 0
    assert report.min_font_px is None
    assert report.violations == ()


# --- the fitter's floor outcome -----------------------------------------------


def test_unfixable_floor_cards_become_the_fit_floor_violation():
    fit_report = {
        "requested": True,
        "fit_state": "final",
        "cards": [
            {"qid": "c1", "outcome": "unfixable:floor"},
            {"qid": "c2", "outcome": "grown_and_shrunk", "factor": 0.9},
            {"qid": "c3", "outcome": "unfixable:floor"},
            {"qid": "c4", "outcome": "unfixable:budget"},
            "not-a-card",
        ],
    }
    assert fit_floor_unfixable_cards(fit_report) == 2
    assert fit_floor_unfixable_cards(None) == 0
    assert fit_floor_unfixable_cards({"cards": "nope"}) == 0
    report = inspect_fragmentation(
        _astra_like(), max_text_owners=50, floor_px=16, fit_report=fit_report
    )
    assert report.fit_floor_cards == 2
    assert report.violations == ("fit_floor:2>0",)


def test_deck_instruments_still_count_floor_unfixable_cards():
    aggregates = _fit_aggregates(
        [
            {"cards": [{"outcome": "unfixable:floor"}, {"outcome": "shrunk", "factor": 0.8}]},
            None,
            {"cards": [{"outcome": "unfixable:floor"}]},
        ]
    )
    assert aggregates["fit_cards_unfixable"] == 2
    assert aggregates["fit_cards_shrunk"] == 1


# --- knobs ---------------------------------------------------------------------


def test_knob_defaults(monkeypatch):
    _gate_off(monkeypatch)
    assert is_authored_fragmentation_gate_enabled() is False
    assert get_authored_fragmentation_max_text_owners() == 50
    assert get_authored_type_floor_px() == 15


def test_knob_reads_and_clamps(monkeypatch):
    monkeypatch.setenv(GATE_KNOB, "true")
    assert is_authored_fragmentation_gate_enabled() is True
    monkeypatch.setenv(GATE_KNOB, "false")
    assert is_authored_fragmentation_gate_enabled() is False

    monkeypatch.setenv(OWNERS_KNOB, "80")
    assert get_authored_fragmentation_max_text_owners() == 80
    monkeypatch.setenv(OWNERS_KNOB, "3")
    assert get_authored_fragmentation_max_text_owners() == 10
    monkeypatch.setenv(OWNERS_KNOB, "999")
    assert get_authored_fragmentation_max_text_owners() == 200

    monkeypatch.setenv(FLOOR_KNOB, "18")
    assert get_authored_type_floor_px() == 18
    monkeypatch.setenv(FLOOR_KNOB, "4")
    assert get_authored_type_floor_px() == 12
    monkeypatch.setenv(FLOOR_KNOB, "40")
    assert get_authored_type_floor_px() == 24


def test_violations_are_silent_while_the_knob_is_off(monkeypatch):
    _gate_off(monkeypatch)
    assert fragmentation_violations(_sol_like()) == ()
    _gate_on(monkeypatch)
    assert fragmentation_violations(_sol_like()) == ("text_owners:90>50", "type_floor:89<15")
    assert fragmentation_violations(_astra_like()) == ()
    _gate_on(monkeypatch, owners="40", floor="20")
    assert fragmentation_violations(_astra_like()) == ("text_owners:46>40", "type_floor:45<20")


# --- the repair road ------------------------------------------------------------


def test_instructions_say_merge_boxes_and_forbid_the_two_escapes():
    owners = fragmentation_repair_instruction("body", "text_owners:90>50")
    floor = fragmentation_repair_instruction("body", "type_floor:13<16")
    fit = fragmentation_repair_instruction("body", "fit_floor:2>0")
    for detail in (owners, floor, fit):
        assert detail is not None
        assert len(detail) <= 200
        assert "CONTENT" in detail
        assert "erg" in detail  # merge / merging
    assert "90 text-owning elements (limit 50)" in owners
    assert "do not shrink type" in owners
    assert "at least 16px" in floor
    assert "not by cutting CONTENT or shrinking other type" in floor
    assert "2 card(s)" in fit
    assert "do not shrink type" in fit
    assert fragmentation_repair_instruction("body", "qids:45>40") is None
    assert fragmentation_repair_instruction("body", "") is None


def test_capacity_repair_instructions_route_the_gate_and_keep_the_envelope_text():
    instructions = capacity_repair_instructions(
        "body", ["text_owners:90>50", "type_floor:13<16", "fit_floor:1>0", "qids:45>40"]
    )
    assert len(instructions) == 4
    assert instructions[0].startswith("BODY has 90 text-owning elements")
    assert instructions[1].startswith("BODY has 13 text element(s) below 16px")
    assert instructions[2].startswith("BODY: 1 card(s) still overflow")
    # The envelope's own sentence is untouched.
    assert instructions[3] == (
        "BODY has 5 excess content target(s). Consolidate low-value body targets; "
        "never relabel title/body/image/supporting roles to bypass the limit."
    )


def test_fragmentation_failures_start_the_same_bounded_repair_round():
    critiques = _capacity_gate_initial_critiques(
        [
            {
                "slide_index": 0,
                "issues": [
                    {"category": FRAGMENTATION_CATEGORY, "message": "text_owners:90>50"},
                    {"category": "capacity", "message": "qids:45>40"},
                ],
            }
        ],
        [0],
        ["body"],
    )
    assert len(critiques) == 1 and critiques[0] is not None
    details = [issue.detail for issue in critiques[0].issues]
    assert any("Merge labels, kickers and small boxes" in detail for detail in details)
    assert any("excess content target" in detail for detail in details)
    assert all(issue.type == "overflow" and issue.severity == "high" for issue in critiques[0].issues)


def _strict_profile() -> CompositionProfile:
    return CompositionProfile(
        profile_id="ordinary",
        text_mode="authored",
        allow_summarization=True,
        strict_dom_gate=True,
        max_repair_cycles=1,
    )


def test_post_render_gate_reports_fragmentation_next_to_the_envelope(monkeypatch):
    """``_evaluate_slide_quality`` carries the gate's issues with no envelope
    at all (``capacity_plan=None``), and nothing while the knob is off."""

    monkeypatch.setattr(
        pipeline, "inspect_authored_dom",
        lambda *args, **kwargs: SimpleNamespace(telemetry_status="available"),
    )
    monkeypatch.setattr(pipeline, "blocking_safety_issues", lambda report: [])
    fit_report = {"cards": [{"qid": "c1", "outcome": "unfixable:floor"}]}
    kwargs = dict(
        index=0, html="<html><body></body></html>", browser_metrics=_sol_like(),
        capacity_plan=None, composition_profile=_strict_profile(),
        reference_mode="style-reference", fit_report=fit_report,
    )

    _gate_off(monkeypatch)
    _report, failure = pipeline._evaluate_slide_quality(**kwargs)
    assert failure is None

    _gate_on(monkeypatch)
    _report, failure = pipeline._evaluate_slide_quality(**kwargs)
    assert failure is not None
    assert failure["slide_index"] == 0
    assert failure["issues"] == [
        {"category": "fragmentation", "severity": "error", "message": "text_owners:90>50"},
        {"category": "fragmentation", "severity": "error", "message": "type_floor:89<15"},
        {"category": "fragmentation", "severity": "error", "message": "fit_floor:1>0"},
    ]


# --- the fitter's floor ----------------------------------------------------------


def test_fitter_floor_follows_the_gate(monkeypatch):
    _gate_off(monkeypatch)
    assert fit_font_floor_px() == 12.0
    assert fit_constants()["font_floor_px"] == 12.0
    assert '"font_floor_px": 12.0' in fit_script_fragment()
    assert '"font_floor_px": 12.0' in fit_probe_prelude()

    _gate_on(monkeypatch)
    assert fit_font_floor_px() == 15.0
    assert fit_constants(font_floor_px=fit_font_floor_px())["font_floor_px"] == 15.0
    assert '"font_floor_px": 15.0' in fit_probe_prelude()
    # An explicit floor wins over the knob, and the default argument stays 12.
    assert '"font_floor_px": 12.0' in fit_probe_prelude(font_floor_px=12)
    assert fit_constants()["font_floor_px"] == 12.0

    _gate_on(monkeypatch, floor="20")
    assert fit_font_floor_px() == 20.0
    assert '"font_floor_px": 20.0' in fit_probe_prelude()


def test_the_probe_carries_the_resolved_floor(monkeypatch):
    """``utils.slide_capture`` threads the resolved floor into the probe it
    injects, so a renderer worker fits to the API's floor, not its own."""

    from utils import slide_capture

    seen: list[float | None] = []
    original = slide_capture.fit_probe_prelude

    def spy(*, font_floor_px=None):
        seen.append(font_floor_px)
        return original(font_floor_px=font_floor_px)

    monkeypatch.setattr(slide_capture, "fit_probe_prelude", spy)
    _gate_on(monkeypatch)
    enriched, _key = slide_capture._instrument_authored_html(
        "<html><body><p>hello</p></body></html>", issue_capability=False
    )
    assert seen == [15.0]
    assert '"font_floor_px": 15.0' in enriched
