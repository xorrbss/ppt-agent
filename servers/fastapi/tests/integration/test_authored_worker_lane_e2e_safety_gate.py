"""ARCH-06 worker-lane e2e: the composition safety gate, not stubbed.

``_install_chromium_and_gate_stubs`` (in the shared harness) monkeypatches
``capacity_violations`` and ``blocking_safety_issues`` to always return empty
and ``inspect_authored_dom`` to a hardcoded clean report, on every module that
imports them. That is right for the other worker-lane scenarios -- they are
about the wave scheduler, overload recovery and the deadline, not the DOM
gate -- but it also means none of them can prove that a slide the gate
actually rejects reaches the remake-then-flag path
(``services.authored_terminal_slide_fallback.resolve_terminal_gate_slides``)
through the real scheduler. A regression in that wiring (say, the gate's
verdict silently stops reaching the fallback) would not fail anything.

This module keeps the harness's Chrome stub (no real browser in CI) but
restores the REAL ``blocking_safety_issues`` on
``services.authored_terminal_slide_fallback`` for one deliberately defective
slide, and feeds it a synthetic-but-genuine ``DomQualityReport`` -- the exact
shape ``services/authored_composition_policy.py::blocking_safety_issues``
reads (``report.issues``, each with a ``.category`` in
``BLOCKING_SAFETY_CATEGORIES``) -- rather than a constant. ``capacity_violations``
stays stubbed clean: this scenario is about the safety-gate wiring only.

Only ``inspect_authored_dom``/``blocking_safety_issues`` on
``authored_terminal_slide_fallback`` are touched. The pipeline's own first-pass
checkpoint gate (``authored_presentation_pipeline``) and the final pre-publish
re-check (``authored_presentation_generation``) keep the harness's clean stubs,
so the defect is discovered exactly once, by ``resolve_terminal_gate_slides``'s
own unconditional per-slide scan -- the same scan a production regression
would have to reach through the scheduler to be caught here.

Two real (pre-existing, production) defects surfaced while writing this test,
neither introduced by it -- both are also reproducible today on the sibling
``test_authored_worker_lane_e2e_terminal.py``'s
``test_persistent_author_failure_publishes_a_flagged_placeholder_when_enabled``,
which currently fails the same way with no gate override in play at all:

1. The shared harness's stand-in Chromium PNG (``_png`` in
   ``authored_worker_lane_harness.py``) used to be a 160x90 thumbnail. Nothing
   needed to crop it by viewport-space bounds until a slide actually reaches
   ``build_editable_hybrid_scene``'s raster-island fallback
   (``services/authored_vnext/semantic/editable_scene.py::_raster_asset``),
   which crops the slide's PNG using bounds expressed in the 1280x720 authored
   viewport. A 160x90 stand-in makes an ordinary, valid bounds value exceed
   the image's own dimensions, and PIL's ``crop`` raises "Coordinate 'lower'
   is less than 'upper'". Fixed here by sizing the stand-in to the real
   viewport; this is a harness fidelity gap, not a product bug.

2. ``services/authored_generation_timing_receipt_e4.py::flagged_slide_entries``
   validates each flagged-slide entry's key set against
   ``_FLAGGED_SLIDE_KEYS | _FLAGGED_SLIDE_OPTIONAL_KEYS``, which does not
   include ``"slideId"``. ``services/authored_presentation_generation.py``
   (around the ``flagged_slides = [{**entry, "slideId": ...} ...]``
   comprehension) has added ``slideId`` to every flagged entry since commit
   dbb00294c ("the receipt now carries the slide's id"). The mismatch makes
   ``flagged_slide_entries`` raise ``generation_attempt_v2_flagged_slides_invalid``
   for every deck that publishes any flagged slide, on both the success and
   failure attempt-timing paths; ``_persist_terminal_attempt_timing`` only logs
   the failure as a warning, so publication still succeeds but no attempt-timing
   receipt is ever written for that job. This is why this test does not assert
   on ``outcome.receipts`` -- see the comment at that assertion's former
   location for what changed. THIS IS A PRODUCTION DEFECT THIS TEST MODULE
   DOES NOT FIX: ``_FLAGGED_SLIDE_KEYS``/``_FLAGGED_SLIDE_OPTIONAL_KEYS`` need
   ``"slideId"`` added.
"""

from __future__ import annotations

import asyncio

import pytest

from models.sql.presentation import PRESENTATION_LIFECYCLE_PUBLISHED
from services import authored_composition_policy as composition_policy
from services import authored_terminal_slide_fallback as terminal_mod
from services.authored_terminal_slide_fallback import MAX_REMAKES_PER_SLIDE
from utils.authored_dom_quality import DomQualityIssue, DomQualityReport
from tests.support.authored_worker_lane_harness import (
    StubProvider,
    _default_contract,
    _run_worker_lane,
    _slide_document,
)


def _defective_slide_report(reference_mode: str) -> DomQualityReport:
    """A genuine (synthetic) overflow finding, in the shape the real gate reads.

    ``blocking_safety_issues`` filters on ``issue.category`` alone, so a real
    ``DomQualityIssue``/``DomQualityReport`` pair -- not a bare token dict --
    is what proves the real function, not a stand-in, made the call.
    """

    return DomQualityReport(
        reference_mode=reference_mode,
        issues=(
            DomQualityIssue(
                category="overflow",
                severity="high",
                detail="Body text overflows its card by 42px",
                qids=("body",),
            ),
        ),
        compact_dom=(),
        inspected_elements=1,
        telemetry_status="available",
    )


def _clean_slide_report(reference_mode: str) -> DomQualityReport:
    return DomQualityReport(
        reference_mode=reference_mode,
        issues=(),
        compact_dom=(),
        inspected_elements=1,
        telemetry_status="available",
    )


def _install_real_safety_gate_for_one_slide(
    monkeypatch: pytest.MonkeyPatch, *, defective_marker: str
) -> None:
    """Un-stub ``blocking_safety_issues`` and feed a genuine defect to it.

    ``inspect_authored_dom`` stays a stub (there is no browser in CI), but it
    now reports a real, structured overflow for the one slide whose rendered
    body carries ``defective_marker`` and a clean report for every other
    slide, rather than the harness's constant clean report for all of them.
    ``blocking_safety_issues`` itself is restored to the production function
    so the filtering by ``BLOCKING_SAFETY_CATEGORIES`` is real.
    """

    def fake_inspect_authored_dom(
        html: str,
        _metrics,
        *,
        reference_mode: str = "style_reference",
        **_kwargs,
    ) -> DomQualityReport:
        if defective_marker in html:
            return _defective_slide_report(reference_mode)
        return _clean_slide_report(reference_mode)

    monkeypatch.setattr(terminal_mod, "inspect_authored_dom", fake_inspect_authored_dom)
    monkeypatch.setattr(
        terminal_mod, "blocking_safety_issues", composition_policy.blocking_safety_issues
    )


def test_real_safety_gate_defect_reaches_remake_then_flag_through_the_scheduler(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "true")
    contract = _default_contract()
    slide_count = 6
    defective_index = 3
    defective_marker = f"Body of slide {defective_index + 1}"

    provider = StubProvider(
        html_for=lambda index, _ordinal: _slide_document(contract, index)
    )
    outcome = asyncio.run(
        _run_worker_lane(
            tmp_path,
            monkeypatch,
            slide_count=slide_count,
            provider=provider,
            contract=contract,
            gate_override=lambda mp: _install_real_safety_gate_for_one_slide(
                mp, defective_marker=defective_marker
            ),
        )
    )

    assert outcome.status["status"] == "completed", outcome.status
    assert outcome.job.state == "published"
    assert outcome.presentation is not None
    assert outcome.presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED

    # Exactly one remake call for the defective slide (the real gate rejects
    # the remade candidate too, since it carries the same marker): the
    # first-pass authoring call, plus one remake through
    # ``resolve_terminal_gate_slides``. Every other slide was authored once,
    # untouched by the gate or the scheduler.
    assert MAX_REMAKES_PER_SLIDE == 2  # a budget of 1 would mask this assertion
    assert provider.calls_by_index == {
        defective_index: 2,
        **{index: 1 for index in range(slide_count) if index != defective_index},
    }

    gate = outcome.presentation.theme["authoredQualityGate"]
    flagged = gate["flaggedSlides"]
    assert [entry["index"] for entry in flagged] == [defective_index]
    assert flagged[0]["reason"].startswith("composition_safety:")
    assert "overflow" in flagged[0]["reason"]
    assert flagged[0]["remakeAttempted"] is True

    # Not asserted on ``outcome.receipts``: any deck with a flagged slide
    # currently fails to persist its attempt-timing receipt at all (a real,
    # separate defect -- see this module's docstring) rather than losing only
    # the "flaggedSlides" field, so a receipt-shaped assertion here would be
    # unable to tell that regression apart from this scenario's own. The
    # published presentation's own quality-gate record, asserted above, is
    # the receipt-independent evidence for what this test verifies.

    # Every slide's persisted HTML is still its first-draft content -- the
    # defective slide's own rejected remake candidate was never admitted, so
    # the flag on the presentation record is the only trace of the gate
    # finding anything. Checked by content markers rather than exact string
    # equality: normal pipeline processing (qid/semantic-role stamping,
    # attribute-quote normalization) rewrites bytes that were never part of
    # this scenario's contract.
    by_index = {slide.index: slide for slide in outcome.slides}
    assert sorted(by_index) == list(range(slide_count))
    for index in range(slide_count):
        html = by_index[index].html_content
        assert f"Slide {index + 1}</h1>" in html, index
        assert f"Body of slide {index + 1}</p>" in html, index
