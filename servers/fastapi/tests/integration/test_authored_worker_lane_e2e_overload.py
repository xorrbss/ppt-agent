"""ARCH-06 worker-lane e2e: overload halves concurrency, cooldown, recovery.

Scenario 2 of the brief; harness in ``tests/support/authored_worker_lane_harness.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
)
from services import authored_batch_policy as batch_policy_mod
from tests.support.authored_worker_lane_harness import (
    PolicyClock,
    StubProvider,
    WaveRecorder,
    _default_contract,
    _install_sleep_recorder,
    _run_worker_lane,
    _slide_document,
    _token_profile,
)

# --- 2: overload halves concurrency, cooldown, recovery -------------------------


def _cooldown_sleeps(sleeps: list[float]) -> list[float]:
    """Only the sleeps that are cooldown-sized.

    ``_install_sleep_recorder`` intercepts every second-scale sleep issued from
    an ``authored`` module, and the generation progress poller issues its own,
    so a positional assertion would pin the wrong one.
    """

    return [
        value
        for value in sleeps
        if abs(value - batch_policy_mod.OVERLOAD_COOLDOWN_BASE_SECONDS) <= 0.5
    ]


def _overload_scenario(tmp_path, monkeypatch, *, wave_barrier: bool = True):
    # Two schedulers author the initial pass (``services/authored_wave_scheduler``):
    # the historic barrier plans discrete waves, and the shipping default admits
    # the next call the moment one finishes. Only the barrier HAS waves, so a
    # test about wave shape has to ask for it.
    monkeypatch.setenv(
        "AUTHORED_WAVE_BARRIER_DISABLED", "false" if wave_barrier else "true"
    )
    # The style anchor authors the deck's first content slide on its own before
    # the initial pass, which would absorb the overload this scenario aims at
    # the first steady wave. It is a separate feature with its own tests.
    monkeypatch.setenv("AUTHORED_STYLE_ANCHOR_ENABLED", "false")
    profile = _token_profile(monkeypatch)
    contract = _default_contract()
    slide_count = 8
    clock = PolicyClock()
    recorder = WaveRecorder(clock)
    recorder.install(monkeypatch)
    sleeps = _install_sleep_recorder(monkeypatch, clock)

    def raise_for(indices: tuple[int, ...], _call_number: int) -> BaseException | None:
        # The first call of the first steady wave (slide index 1, initial
        # attempt) hits an overloaded provider.
        if indices == (1,) and provider.calls_by_index.get(1) == 1:
            return RuntimeError("503 Service Unavailable")
        return None

    provider = StubProvider(
        html_for=lambda index, _ordinal: _slide_document(contract, index),
        raise_for=raise_for,
    )
    outcome = asyncio.run(
        _run_worker_lane(
            tmp_path,
            monkeypatch,
            slide_count=slide_count,
            provider=provider,
            contract=contract,
        )
    )
    return profile, recorder, sleeps, provider, outcome


def test_overload_halves_concurrency_and_recovers_after_three_successes(
    tmp_path, monkeypatch
):
    profile, recorder, sleeps, provider, outcome = _overload_scenario(
        tmp_path, monkeypatch
    )
    assert outcome.status["status"] == "completed", outcome.status

    # Wave 1: initial call.  Wave 2: first steady wave, slide 1 overloads.
    # Waves 3-5: cap halved to 1.  Wave 6: cap restored to 2 after three
    # consecutive post-cooldown successes.
    assert recorder.waves == [
        ((0,),),
        ((1,), (2,)),
        ((3,),),
        ((4,),),
        ((5,),),
        ((6,), (7,)),
    ]
    states = recorder.states_after_wave
    after_overload = states[1]
    assert batch_policy_mod.is_authored_provider_overload(
        RuntimeError("503 Service Unavailable")
    )
    assert after_overload.concurrency_cap == 1
    assert after_overload.degraded is True
    assert after_overload.consecutive_overloads == 1
    assert after_overload.cooldown_until is not None
    # The cooldown the wave scheduler honours before the next wave. F3 landed
    # the wait inside ``AuthoredWaveSchedule.wait_out_cooldown``, so the sleep
    # recorder sees it and the recorder's own stand-in skip stays unused. The
    # recorder catches every second-scale sleep from an ``authored`` module
    # (the progress poller sleeps too), so match on the value, not on position.
    assert _cooldown_sleeps(sleeps) == pytest.approx(
        [batch_policy_mod.OVERLOAD_COOLDOWN_BASE_SECONDS], abs=0.5
    )
    assert recorder.scheduler_skips == []
    assert states[2].consecutive_successes == 1
    assert states[3].consecutive_successes == 2
    after_recovery = states[4]
    assert after_recovery.concurrency_cap == profile.steady_wave_concurrency
    assert after_recovery.degraded is False
    assert after_recovery.cooldown_until is None
    assert after_recovery.consecutive_overloads == 0

    # The overloaded slide took exactly its recovery call; nothing else repeated.
    assert provider.calls_by_index == {1: 2, **{i: 1 for i in range(8) if i != 1}}
    assert outcome.job.state == "published"
    assert outcome.presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
    assert len(outcome.slides) == 8


def test_overload_cooldown_wait_is_performed_by_the_scheduler(tmp_path, monkeypatch):
    _profile, recorder, sleeps, _provider, outcome = _overload_scenario(
        tmp_path, monkeypatch
    )
    assert outcome.status["status"] == "completed", outcome.status
    # F3: the wave loop awaits ``cooldown_remaining`` (bounded by the deadline)
    # instead of dispatching the next wave immediately, so the recorder sees the
    # sleep and needs no test-side skip.
    cooldowns = _cooldown_sleeps(sleeps)
    assert cooldowns, "no cooldown-sized asyncio.sleep was awaited by the scheduler"
    assert cooldowns[0] >= batch_policy_mod.OVERLOAD_COOLDOWN_BASE_SECONDS - 0.5
    assert recorder.scheduler_skips == []




def test_overload_does_not_lose_a_slide_without_the_wave_barrier(
    tmp_path, monkeypatch
):
    """The shipping default: continuous admission, so there are no waves.

    The policy is still advanced from every outcome (the cap is re-read on each
    admission), but nothing plans a wave, so the shape assertions above have
    nothing to observe here. What must hold in both schedulers is the outcome:
    the overloaded slide takes exactly one recovery call and the deck publishes
    complete.
    """

    _profile, recorder, _sleeps, provider, outcome = _overload_scenario(
        tmp_path, monkeypatch, wave_barrier=False
    )

    assert outcome.status["status"] == "completed", outcome.status
    assert recorder.waves == []
    assert provider.calls_by_index == {1: 2, **{i: 1 for i in range(8) if i != 1}}
    assert outcome.job.state == "published"
    assert outcome.presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
    assert len(outcome.slides) == 8
