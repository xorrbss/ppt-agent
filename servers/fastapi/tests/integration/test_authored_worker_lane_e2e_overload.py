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


def _overload_scenario(tmp_path, monkeypatch):
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
    profile, recorder, _sleeps, provider, outcome = _overload_scenario(
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
    # The cooldown the wave scheduler must honour before the next wave.
    assert recorder.scheduler_skips[:1] == pytest.approx(
        [batch_policy_mod.OVERLOAD_COOLDOWN_BASE_SECONDS], abs=0.5
    )
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


@pytest.mark.xfail(
    strict=False,
    reason="cooldown wait lands with the wave scheduler",
)
def test_overload_cooldown_wait_is_performed_by_the_scheduler(tmp_path, monkeypatch):
    _profile, recorder, sleeps, _provider, outcome = _overload_scenario(
        tmp_path, monkeypatch
    )
    assert outcome.status["status"] == "completed", outcome.status
    # Intended behaviour: the wave loop awaits ``cooldown_remaining`` (bounded
    # by the deadline) instead of dispatching the next wave immediately.  Once
    # that sleep exists the recorder sees it and no test-side skip is needed.
    assert sleeps, "no cooldown-sized asyncio.sleep was awaited by the scheduler"
    assert sleeps[0] >= batch_policy_mod.OVERLOAD_COOLDOWN_BASE_SECONDS - 0.5
    assert recorder.scheduler_skips == []


