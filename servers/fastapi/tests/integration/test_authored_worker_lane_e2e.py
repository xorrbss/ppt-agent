"""ARCH-06 worker-lane e2e: happy path, admission on every slide, deadline.

Scenarios 1, 4 and 5 of the brief.  The overload and terminal-fallback
scenarios live in the sibling ``_overload`` / ``_terminal`` modules; all
share ``tests/support/authored_worker_lane_harness.py``.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
)
from services import public_generation_deadline_policy as deadline_policy
from services.authored_provider_rate_profile import TOKEN_PROFILE_NAME
from tests.support.authored_worker_lane_harness import (
    _NORMALIZED_DECLARATION,
    PolicyClock,
    StubProvider,
    WaveRecorder,
    _default_contract,
    _expected_wave_shapes,
    _install_sleep_recorder,
    _run_worker_lane,
    _slide_document,
    _token_profile,
    _utc,
)
from utils.renderer_isolation import admit_authored_html

# --- 1 + 5: happy path, wave shapes, admission on every published slide ---------


def test_happy_path_authors_in_token_profile_waves_and_publishes(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", TOKEN_PROFILE_NAME)
    profile = _token_profile(monkeypatch)
    contract = _default_contract()
    slide_count = 6
    clock = PolicyClock()
    recorder = WaveRecorder(clock)
    recorder.install(monkeypatch)
    _install_sleep_recorder(monkeypatch, clock)

    def html_for(index: int, _ordinal: int) -> str:
        # Slide 3 carries a whitelisted harmless finding the normaliser strips.
        extra = _NORMALIZED_DECLARATION if index == 2 else ""
        return _slide_document(contract, index, body_style_extra=extra)

    provider = StubProvider(html_for=html_for)
    outcome = asyncio.run(
        _run_worker_lane(
            tmp_path,
            monkeypatch,
            slide_count=slide_count,
            provider=provider,
            contract=contract,
        )
    )

    assert outcome.status["status"] == "completed", outcome.status
    assert outcome.status["failure"] is None
    assert outcome.status["progress"]["completed_slides"] == slide_count

    # Wave shapes: 1 call in the initial wave, then 2 per wave, 1 slide/call.
    expected_waves = _expected_wave_shapes(slide_count, profile)
    assert recorder.waves == expected_waves
    assert len(recorder.waves) == 1 + math.ceil((slide_count - 1) / 2)
    assert len(recorder.waves) == deadline_policy.public_generation_wave_count(
        slide_count, profile=profile
    )
    assert provider.calls_by_index == {index: 1 for index in range(slide_count)}
    assert provider.max_in_flight <= profile.steady_wave_concurrency
    assert recorder.scheduler_skips == []
    final_state = recorder.final_state()
    assert final_state is not None
    assert final_state.first_success_observed is True
    assert final_state.degraded is False

    # Every slide admitted and the deck published.
    assert outcome.job.state == "published"
    assert outcome.job.completed_slides == slide_count
    assert outcome.presentation is not None
    assert outcome.presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
    assert [slide.index for slide in outcome.slides] == list(range(slide_count))

    # Quality-gate receipt on the presentation: passed, nothing flagged.
    gate = outcome.presentation.theme["authoredQualityGate"]
    assert gate["status"] == "passed"
    assert gate.get("flaggedSlides", []) == []

    # 4. The deadline pinned at admission is the policy formula for this deck
    # under the token profile (one slide per call), never recomputed later.
    assert outcome.admission is not None and outcome.operation is not None
    assert outcome.admission.slide_count == slide_count
    pinned_seconds = (
        _utc(outcome.admission.deadline_at) - _utc(outcome.operation.created_at)
    ).total_seconds()
    assert pinned_seconds == pytest.approx(
        deadline_policy.public_generation_deadline_seconds(slide_count, profile=profile),
        abs=15,
    )
    assert _utc(outcome.operation.deadline_at) == _utc(outcome.admission.deadline_at)

    # Attempt-timing receipt (E4): provider rate profile and a clean publish.
    assert outcome.receipts, "no attempt-timing receipt was written"
    receipt = outcome.receipts[-1].to_dict()
    assert receipt["outcome"] == "succeeded"
    assert receipt["providerRateProfile"] == TOKEN_PROFILE_NAME
    assert "flaggedSlides" not in receipt

    # 5. Markup admission ran on the persisted HTML of every published slide.
    for slide in outcome.slides:
        html = slide.html_content
        assert isinstance(html, str) and html.strip(), slide.index
        assert slide.ui is None
        assert "<script" not in html.lower()
        admit_authored_html(html)  # re-admission must not raise
        assert "break-inside" not in html.lower(), slide.index
        if slide.index != 0:  # COVER is exempt from the header contract
            assert 'data-header-part="band"' in html, slide.index



# --- 4: deadline for the shipped 1-slide token shape -------------------------------


def test_generation_deadline_scales_the_wave_budget_by_slides_per_call(monkeypatch):
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", TOKEN_PROFILE_NAME)
    profile = _token_profile(monkeypatch)
    slide_count = 6

    waves = deadline_policy.public_generation_wave_count(slide_count, profile=profile)
    assert waves == 1 + math.ceil((slide_count - 1) / profile.steady_wave_concurrency)

    wave_seconds = deadline_policy.public_generation_wave_seconds(profile=profile)
    assert wave_seconds == (
        deadline_policy.PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS
        + deadline_policy.PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS
        * profile.slides_per_call
    )
    # One slide per call is budgeted below the documented three-slide wave.
    assert wave_seconds < deadline_policy.PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS

    # Pinned literal rather than a re-derivation of the production formula:
    # 600 base + 1410 preamble + 140 * 4 waves + 1120 remediation + 928 review.
    # The full term-by-term contract lives in
    # tests/unit/test_public_generation_deadline_policy.py.
    expected = 4618
    assert deadline_policy.public_generation_deadline_seconds(
        slide_count, profile=profile
    ) == expected
    # The pinned deadline is a pure function of the resolved profile.
    assert deadline_policy.public_generation_deadline_seconds(slide_count) == expected
