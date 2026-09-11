"""F3: authoring admission width, cooldown waits, and exactly-once coverage."""

import asyncio
from dataclasses import dataclass, replace

import pytest

from services.authored_batch_policy import (
    AuthoredBatchPolicyState,
    new_authored_batch_policy_state,
)
from services.authored_provider_rate_profile import AuthoredRateProfile
from services.authored_wave_scheduler import (
    AuthoredWaveSchedule,
    run_initial_authoring,
)
from utils.provider_idempotency import ProviderQuotaPause


@dataclass
class _Result:
    htmls: dict


def _profile(*, initial: int = 1, steady: int = 4, per_call: int = 1):
    return AuthoredRateProfile(
        name="api",
        slides_per_call=per_call,
        initial_wave_concurrency=initial,
        steady_wave_concurrency=steady,
        recovery_successes=2,
        author_concurrency_default=steady,
        author_concurrency_max=steady,
    )


def _state(**kwargs) -> AuthoredBatchPolicyState:
    return AuthoredBatchPolicyState(profile=_profile(**kwargs))


def _run(coro):
    return asyncio.run(coro)


def test_admission_authors_every_slide_exactly_once():
    seen: list[int] = []

    async def run_batch(indexes):
        await asyncio.sleep(0)
        seen.extend(indexes)
        return _Result(htmls={index: f"<html>{index}</html>" for index in indexes})

    schedule = AuthoredWaveSchedule(_state())
    _run(
        run_initial_authoring(
            list(range(9)),
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, outcome: bool(getattr(outcome, "htmls", None)),
            barrier_disabled=True,
        )
    )

    assert sorted(seen) == list(range(9))
    assert len(seen) == len(set(seen))


def test_admission_width_follows_the_live_policy_concurrency():
    active = 0
    peak_by_admission: list[int] = []

    async def run_batch(indexes):
        nonlocal active
        active += 1
        peak_by_admission.append(active)
        try:
            await asyncio.sleep(0.01)
            return _Result(htmls={index: "html" for index in indexes})
        finally:
            active -= 1

    # The first wave is width 1 until a success unlocks the steady width of 4.
    schedule = AuthoredWaveSchedule(_state(initial=1, steady=4))
    _run(
        run_initial_authoring(
            list(range(8)),
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, _outcome: True,
            barrier_disabled=True,
        )
    )

    assert peak_by_admission[0] == 1
    assert max(peak_by_admission) == 4
    assert schedule.policy.first_success_observed is True


def test_an_overload_narrows_the_live_admission_width():
    active = 0
    peaks: list[int] = []
    calls = 0

    async def run_batch(indexes):
        nonlocal active, calls
        calls += 1
        this_call = calls
        active += 1
        peaks.append(active)
        try:
            await asyncio.sleep(0.01)
            if this_call == 2:
                # A 429 halves the cap for every later admission.
                raise RuntimeError("429 too many requests")
            return _Result(htmls={index: "html" for index in indexes})
        finally:
            active -= 1

    schedule = AuthoredWaveSchedule(
        _state(initial=4, steady=4),
        # Skip the cooldown itself; the width change is what is under test.
        sleep=_noop_sleep,
        clock=lambda: 10_000.0,
    )
    schedule.policy = replace(schedule.policy, first_success_observed=True)
    _run(
        run_initial_authoring(
            list(range(12)),
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, outcome: not isinstance(outcome, BaseException),
            barrier_disabled=True,
        )
    )

    assert schedule.policy.concurrency_cap == 2
    assert peaks[0:4] == [1, 2, 3, 4]
    # After the overload nothing ever runs four wide again.
    assert max(peaks[6:]) <= 2


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_admission_waits_out_an_open_cooldown_before_the_next_call():
    slept: list[float] = []
    now = [1000.0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    async def run_batch(indexes):
        return _Result(htmls={index: "html" for index in indexes})

    schedule = AuthoredWaveSchedule(
        _state(initial=1, steady=1), clock=lambda: now[0], sleep=sleep
    )
    schedule.policy = replace(schedule.policy, cooldown_until=now[0] + 30.0)

    _run(
        run_initial_authoring(
            [0, 1],
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, _outcome: True,
            barrier_disabled=True,
        )
    )

    assert slept and slept[0] == pytest.approx(30.0)
    assert schedule.cooldown_wait_seconds == pytest.approx(sum(slept))


def test_a_cooldown_wait_never_outruns_the_job_deadline():
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    schedule = AuthoredWaveSchedule(
        _state(),
        clock=lambda: 100.0,
        sleep=sleep,
        deadline=112.0,
    )
    schedule.policy = replace(schedule.policy, cooldown_until=400.0)

    waited = _run(schedule.wait_out_cooldown("admission-1"))

    assert waited == pytest.approx(12.0)
    assert slept == [pytest.approx(12.0)]


def test_the_barrier_path_still_runs_when_the_knob_is_off():
    waves: list[tuple[int, ...]] = []

    async def run_batch(indexes):
        waves.append(tuple(indexes))
        return _Result(htmls={index: "html" for index in indexes})

    schedule = AuthoredWaveSchedule(_state(initial=1, steady=2))
    _run(
        run_initial_authoring(
            [0, 1, 2, 3, 4],
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, _outcome: True,
            barrier_disabled=False,
        )
    )

    # Wave 1 is the single probe call; every later wave is two calls wide.
    assert waves[0] == (0,)
    assert sorted(index for wave in waves for index in wave) == [0, 1, 2, 3, 4]


def test_a_raised_batch_is_recorded_and_never_reauthored():
    seen: list[tuple[int, ...]] = []
    recorded: list[tuple[tuple[int, ...], bool]] = []

    async def run_batch(indexes):
        seen.append(tuple(indexes))
        raise RuntimeError("provider down")

    def record(indexes, outcome) -> bool:
        recorded.append((tuple(indexes), isinstance(outcome, BaseException)))
        return False

    schedule = AuthoredWaveSchedule(new_authored_batch_policy_state())
    _run(
        run_initial_authoring(
            [0, 1],
            schedule,
            run_batch=run_batch,
            record=record,
            barrier_disabled=True,
        )
    )

    assert sorted(index for batch in seen for index in batch) == [0, 1]
    assert all(is_exception for _batch, is_exception in recorded)


def test_quota_pause_escapes_as_a_deck_outcome_before_slide_recording():
    recorded: list[tuple[int, ...]] = []
    pause = ProviderQuotaPause(reset_at=2_000_000_000, observed_at=1_999_999_900)

    async def run_batch(_indexes):
        raise pause

    schedule = AuthoredWaveSchedule(_state())
    with pytest.raises(ProviderQuotaPause) as raised:
        _run(
            run_initial_authoring(
                [0],
                schedule,
                run_batch=run_batch,
                record=lambda indexes, _outcome: recorded.append(tuple(indexes)) or False,
                barrier_disabled=True,
            )
        )

    assert raised.value is pause
    assert recorded == []


def test_cancelling_admission_reaps_every_inflight_batch():
    started: set[int] = set()
    cleaned: set[int] = set()
    all_started = asyncio.Event()

    async def run_batch(indexes):
        index = indexes[0]
        started.add(index)
        if len(started) == 4:
            all_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.add(index)

    async def scenario():
        schedule = AuthoredWaveSchedule(_state(initial=4, steady=4))
        task = asyncio.create_task(
            run_initial_authoring(
                list(range(20)),
                schedule,
                run_batch=run_batch,
                record=lambda _indexes, _outcome: True,
                barrier_disabled=True,
            )
        )
        await all_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert started == cleaned == {0, 1, 2, 3}

    _run(scenario())
