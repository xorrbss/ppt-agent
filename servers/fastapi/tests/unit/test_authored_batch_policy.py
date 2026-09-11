import pytest
from types import SimpleNamespace

from services.authored_batch_policy import (
    OVERLOAD_COOLDOWN_BASE_SECONDS,
    OVERLOAD_COOLDOWN_MAX_SECONDS,
    AuthoredBatchPolicyState,
    advance_authored_batch_policy,
    is_authored_provider_overload,
    new_authored_batch_policy_state,
    plan_authored_batch_wave,
    plan_authored_recovery_batches,
)
from services.authored_provider_rate_profile import AuthoredRateProfile
from utils.llm_calls import author_batch as author_batch_mod
from utils.llm_calls.author_batch import AuthoredBatchSlideRequest, author_slides_html
from utils.llm_calls.author_slide import Brand


TOKEN = AuthoredRateProfile(
    name="token",
    slides_per_call=3,
    initial_wave_concurrency=1,
    steady_wave_concurrency=2,
    recovery_successes=3,
    author_concurrency_default=5,
    author_concurrency_max=8,
)
API = AuthoredRateProfile(
    name="api",
    slides_per_call=1,
    initial_wave_concurrency=5,
    steady_wave_concurrency=5,
    recovery_successes=3,
    author_concurrency_default=5,
    author_concurrency_max=16,
)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _brand() -> Brand:
    return Brand(topic="batch", language="Korean")


def _html(index: int) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        f"<h1 data-qid='slide-{index}.title'>slide {index}</h1>"
        "</body></html>"
    )


def _wrapped(index: int) -> str:
    return f'<authored-slide index="{index}">{_html(index)}</authored-slide>'


def _capture_batch_provider(monkeypatch, response: str):
    calls = []

    class FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=response)

    async def no_rate_limit():
        return None

    monkeypatch.setattr(author_batch_mod, "get_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(author_batch_mod, "get_llm_config", lambda: {})
    monkeypatch.setattr(author_batch_mod, "get_model", lambda: "fake-model")
    monkeypatch.setattr(author_batch_mod, "get_llm_provider", lambda: SimpleNamespace(value="fake"))
    monkeypatch.setattr(author_batch_mod, "get_generate_kwargs", lambda **kwargs: kwargs)
    monkeypatch.setattr(author_batch_mod, "_authoring_extra_body", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(author_batch_mod, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_batch_mod, "_wait_for_author_rate_slot", no_rate_limit)
    return calls


def _shapes(wave) -> list[tuple[int, ...]]:
    return [batch.slide_indices for batch in wave.batches]


def _wave_shapes(remaining: int) -> list[tuple[int, ...]]:
    state = AuthoredBatchPolicyState(profile=TOKEN, first_success_observed=True)
    return _shapes(plan_authored_batch_wave(range(remaining), state))


def _overloaded(state, *, now: float, error: object = 503) -> AuthoredBatchPolicyState:
    return advance_authored_batch_policy(state, succeeded=False, error=error, now=now)


def _succeeded(state, *, now: float) -> AuthoredBatchPolicyState:
    return advance_authored_batch_policy(state, succeeded=True, now=now)


def test_first_wave_is_serial_then_first_success_unlocks_two_calls() -> None:
    initial = new_authored_batch_policy_state(TOKEN)
    first_wave = plan_authored_batch_wave(range(6), initial)

    assert initial.concurrency == 1
    assert _shapes(first_wave) == [(0, 1, 2)]

    unlocked = advance_authored_batch_policy(initial, succeeded=True, now=0.0)
    second_wave = plan_authored_batch_wave(range(3, 9), unlocked)

    assert unlocked.concurrency == 2
    assert _shapes(second_wave) == [(3, 4, 5), (6, 7, 8)]


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (6, [(0, 1, 2), (3, 4, 5)]),
        (5, [(0, 1, 2), (3, 4)]),
        (4, [(0, 1, 2), (3,)]),
        (3, [(0, 1, 2)]),
        (2, [(0, 1)]),
        (1, [(0,)]),
    ],
)
def test_unlocked_wave_uses_exact_remainder_shapes(remaining: int, expected: list[tuple[int, ...]]) -> None:
    assert _wave_shapes(remaining) == expected


def test_api_profile_schedules_one_slide_per_call_at_author_concurrency() -> None:
    state = new_authored_batch_policy_state(API)
    pending = list(range(20))
    call_count = 0
    waves = 0
    while pending:
        wave = plan_authored_batch_wave(pending, state)
        waves += 1
        assert all(len(batch.slide_indices) == 1 for batch in wave.batches)
        assert len(wave.batches) == min(5, len(pending))
        for _batch in wave.batches:
            state = advance_authored_batch_policy(state, succeeded=True, now=0.0)
            call_count += 1
        pending = [index for index in pending if index not in wave.slide_indices]

    assert call_count == 20
    assert waves == 4


class _RateLimited(RuntimeError):
    status_code = 429


@pytest.mark.parametrize(
    "error",
    [
        500,
        502,
        503,
        504,
        429,
        "HTTP 500",
        "HTTP 502",
        "HTTP 504",
        "HTTP 429",
        "request timed out",
        "provider_circuit_open",
        "public_provider_overload",
        "public_provider_call_timeout",
        "rate_limit_exceeded",
        "Rate limit reached for gpt-5.2",
        "usage_limit_reached",
        "AI provider usage limit reached.",
        "RESOURCE_EXHAUSTED",
        "Too Many Requests",
        TimeoutError(),
        _RateLimited("throttled"),
        SimpleNamespace(response=SimpleNamespace(status_code=429)),
    ],
)
def test_overload_and_rate_limit_signals_are_recognized(error: object) -> None:
    assert is_authored_provider_overload(error) is True


@pytest.mark.parametrize(
    "error",
    [
        None,
        400,
        "HTTP 404",
        "public_provider_bridge_exception",
        "authored_batch_slide_missing",
        "schema mismatch",
    ],
)
def test_ordinary_failures_are_not_overload(error: object) -> None:
    assert is_authored_provider_overload(error) is False


def test_overload_halves_the_cap_and_opens_a_cooldown() -> None:
    unlocked = AuthoredBatchPolicyState(profile=TOKEN, first_success_observed=True)

    degraded = _overloaded(unlocked, now=100.0, error="HTTP 429")

    assert degraded.concurrency_cap == 1
    assert degraded.concurrency == 1
    assert degraded.degraded is True
    assert degraded.cooldown_until == 100.0 + OVERLOAD_COOLDOWN_BASE_SECONDS
    assert degraded.in_cooldown(120.0) is True
    assert degraded.cooldown_remaining(120.0) == 10.0
    assert degraded.in_cooldown(130.0) is False
    assert _shapes(plan_authored_batch_wave(range(6), degraded)) == [(0, 1, 2)]


def test_overload_wins_over_a_partial_success_but_still_records_the_success() -> None:
    fresh = new_authored_batch_policy_state(TOKEN)

    state = advance_authored_batch_policy(fresh, succeeded=True, error=503, now=0.0)

    assert state.first_success_observed is True
    assert state.concurrency == 1
    assert state.concurrency_cap == 1


def test_cap_never_drops_below_one() -> None:
    state = AuthoredBatchPolicyState(profile=API, first_success_observed=True)
    for step, expected in enumerate((2, 1, 1, 1)):
        state = _overloaded(state, now=float(step))
        assert state.concurrency_cap == expected
        assert state.concurrency == expected


def test_backoff_doubles_per_consecutive_overload_and_caps_at_five_minutes() -> None:
    state = AuthoredBatchPolicyState(profile=API, first_success_observed=True)
    cooldowns = []
    for _step in range(6):
        state = _overloaded(state, now=1000.0)
        cooldowns.append(state.cooldown_until - 1000.0)

    assert cooldowns == [30.0, 60.0, 120.0, 240.0, 300.0, 300.0]
    assert cooldowns[-1] == OVERLOAD_COOLDOWN_MAX_SECONDS
    assert state.consecutive_overloads == 6


def test_recovery_restores_one_step_after_n_successes_past_the_cooldown() -> None:
    degraded = _overloaded(
        AuthoredBatchPolicyState(profile=TOKEN, first_success_observed=True), now=0.0
    )
    assert degraded.concurrency == 1

    # Successes inside the cooldown do not count toward recovery.
    state = degraded
    for moment in (5.0, 10.0, 15.0):
        state = _succeeded(state, now=moment)
    assert state.consecutive_successes == 0
    assert state.concurrency == 1

    # After the cooldown, N - 1 successes are not enough...
    state = _succeeded(state, now=31.0)
    state = _succeeded(state, now=32.0)
    assert state.consecutive_successes == 2
    assert state.concurrency == 1

    # ...and the N-th restores the step, clearing the cooldown and the backoff.
    state = _succeeded(state, now=33.0)
    assert state.concurrency == 2
    assert state.degraded is False
    assert state.cooldown_until is None
    assert state.consecutive_successes == 0
    assert state.consecutive_overloads == 0
    assert _shapes(plan_authored_batch_wave(range(6), state)) == [(0, 1, 2), (3, 4, 5)]


def test_recovery_climbs_by_doubling_up_to_the_profile_cap() -> None:
    state = AuthoredBatchPolicyState(profile=API, first_success_observed=True)
    for step in range(3):
        state = _overloaded(state, now=float(step))
    assert state.concurrency_cap == 1
    assert state.consecutive_overloads == 3

    now = 1000.0
    caps = []
    for _restore in range(3):
        for _success in range(API.recovery_successes):
            now += 1.0
            state = _succeeded(state, now=now)
        caps.append(state.concurrency_cap)

    assert caps == [2, 4, 5]
    assert state.degraded is False
    assert state.consecutive_overloads == 0


def test_partial_recovery_keeps_the_backoff_history() -> None:
    state = AuthoredBatchPolicyState(profile=API, first_success_observed=True)
    state = _overloaded(state, now=0.0)
    state = _overloaded(state, now=1.0)
    assert state.concurrency_cap == 1

    now = 1000.0
    for _success in range(API.recovery_successes):
        now += 1.0
        state = _succeeded(state, now=now)
    assert state.concurrency_cap == 2
    assert state.consecutive_overloads == 2

    # A further overload while still degraded continues the exponential series.
    state = _overloaded(state, now=2000.0)
    assert state.cooldown_until == 2000.0 + 120.0


def test_a_non_overload_failure_breaks_the_success_run_only() -> None:
    degraded = _overloaded(
        AuthoredBatchPolicyState(profile=TOKEN, first_success_observed=True), now=0.0
    )
    state = _succeeded(degraded, now=40.0)
    state = _succeeded(state, now=41.0)
    assert state.consecutive_successes == 2

    broken = advance_authored_batch_policy(
        state, succeeded=False, error="authored_batch_slide_missing", now=42.0
    )

    assert broken.consecutive_successes == 0
    assert broken.concurrency_cap == state.concurrency_cap
    assert broken.cooldown_until == state.cooldown_until
    # An ordinary failure on a healthy job leaves it untouched.
    healthy = AuthoredBatchPolicyState(profile=TOKEN, first_success_observed=True)
    assert advance_authored_batch_policy(healthy, succeeded=False, error="schema mismatch") == healthy


def test_default_clock_is_used_when_no_moment_is_given() -> None:
    ticks = iter([500.0])
    state = advance_authored_batch_policy(
        AuthoredBatchPolicyState(profile=TOKEN, first_success_observed=True),
        succeeded=False,
        error=503,
        clock=lambda: next(ticks),
    )

    assert state.cooldown_until == 530.0


def test_recovery_batches_contain_only_failed_indices_and_cap_at_profile_size() -> None:
    batches = plan_authored_recovery_batches(
        [1, 4, 7, 9], successful_slide_indices=[0, 2, 3, 5, 6, 8], profile=TOKEN
    )

    assert [batch.slide_indices for batch in batches] == [(1, 4, 7), (9,)]
    assert all(batch.recovery for batch in batches)
    assert all(len(batch.slide_indices) <= 3 for batch in batches)

    single = plan_authored_recovery_batches([1, 4], profile=API)
    assert [batch.slide_indices for batch in single] == [(1,), (4,)]


def test_recovery_rejects_an_already_successful_slide() -> None:
    with pytest.raises(ValueError, match="must not include successful"):
        plan_authored_recovery_batches([1, 2], successful_slide_indices=[2], profile=TOKEN)


def test_batch_author_returns_three_slides_from_one_provider_call(monkeypatch) -> None:
    calls = _capture_batch_provider(
        monkeypatch,
        "\n".join(_wrapped(index) for index in (0, 1, 2)),
    )

    result = _run(
        author_slides_html(
            [
                AuthoredBatchSlideRequest(index=0, content="cover", role="COVER"),
                AuthoredBatchSlideRequest(index=1, content="section", role="SECTION"),
                AuthoredBatchSlideRequest(index=2, content="closing", role="CLOSING"),
            ],
            design_system="DESIGN",
            brand=_brand(),
            slide_count=3,
        )
    )

    assert len(calls) == 1
    assert sorted(result.htmls) == [0, 1, 2]
    assert result.errors == {}
    # G9/D18: the fast roles carry the full 16k budget each, and the batch cap
    # is AUTHOR_MAX_TOKENS x MAX_BATCH_SLIDES, so the two meet at 48k.
    assert calls[0]["max_tokens"] == 48_000
    request_parts = calls[0]["messages"][1].content
    assert sum(
        1
        for part in request_parts
        if getattr(part, "text", "").startswith("\n\nBEGIN REQUEST FOR SLIDE INDEX")
    ) == 3


def test_batch_author_records_void_end_tag_normalization(monkeypatch) -> None:
    candidate = _html(0).replace("</body>", "<img data-qid='hero'></img></body>")
    _capture_batch_provider(
        monkeypatch,
        f'<authored-slide index="0">{candidate}</authored-slide>',
    )
    receipts = {0: []}

    result = _run(
        author_slides_html(
            [AuthoredBatchSlideRequest(index=0, content="cover", role="COVER")],
            design_system="DESIGN",
            brand=_brand(),
            slide_count=1,
            normalization_receipts_by_index=receipts,
        )
    )

    assert "</img>" not in result.htmls[0].lower()
    assert receipts[0] == [
        {"rule": "html_void_end_tag_dropped", "detail": "img", "count": 1}
    ]


def test_batch_author_preserves_successes_when_one_slide_is_missing(monkeypatch) -> None:
    _capture_batch_provider(
        monkeypatch,
        "\n".join((_wrapped(0), _wrapped(2))),
    )

    result = _run(
        author_slides_html(
            [
                AuthoredBatchSlideRequest(index=0, content="zero", role="COVER"),
                AuthoredBatchSlideRequest(index=1, content="one", role="SECTION"),
                AuthoredBatchSlideRequest(index=2, content="two", role="CLOSING"),
            ],
            design_system="DESIGN",
            brand=_brand(),
            slide_count=3,
        )
    )

    assert sorted(result.htmls) == [0, 2]
    assert sorted(result.errors) == [1]
    assert str(result.errors[1]) == "authored_batch_slide_missing"
