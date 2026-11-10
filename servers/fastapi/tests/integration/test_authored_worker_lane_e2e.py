"""ARCH-06 worker-lane e2e: happy path, admission on every slide, deadline.

Scenarios 1, 4 and 5 of the brief.  The overload and terminal-fallback
scenarios live in the sibling ``_overload`` / ``_terminal`` modules; all
share ``tests/support/authored_worker_lane_harness.py``.
"""

from __future__ import annotations

import asyncio
import math


from services import public_generation_deadline_policy as deadline_policy
from services.authored_provider_rate_profile import TOKEN_PROFILE_NAME
from tests.support.authored_worker_lane_harness import (
    assert_lane_refused,
    _NORMALIZED_DECLARATION,
    PolicyClock,
    StubProvider,
    WaveRecorder,
    _default_contract,
    _install_sleep_recorder,
    _run_worker_lane,
    _slide_document,
    _token_profile,
)

# --- 1 + 5: happy path, wave shapes, admission on every published slide ---------


def test_happy_path_lane_is_refused_as_authored_strategy_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_WAVE_BARRIER_DISABLED", "false")
    monkeypatch.setenv("AUTHORED_STYLE_ANCHOR_ENABLED", "false")
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", TOKEN_PROFILE_NAME)
    _token_profile(monkeypatch)
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

    # Authored generation was removed on 2026-11-09: this scenario can no
    # longer start, so the lane now proves the refusal.
    assert_lane_refused(outcome, provider)


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
    # 600 base + 2010 preamble + 140 * 4 waves + 1120 remediation + 928 review.
    # The full term-by-term contract lives in
    # tests/unit/test_public_generation_deadline_policy.py.
    expected = 5218
    assert deadline_policy.public_generation_deadline_seconds(
        slide_count, profile=profile
    ) == expected
    # The pinned deadline is a pure function of the resolved profile.
    assert deadline_policy.public_generation_deadline_seconds(slide_count) == expected
