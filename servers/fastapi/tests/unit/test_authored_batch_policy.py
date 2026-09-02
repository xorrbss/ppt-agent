import pytest
from types import SimpleNamespace

from services.authored_batch_policy import (
    AuthoredBatchPolicyState,
    advance_authored_batch_policy,
    is_authored_provider_overload,
    new_authored_batch_policy_state,
    plan_authored_batch_wave,
    plan_authored_recovery_batches,
)
from utils.llm_calls import author_batch as author_batch_mod
from utils.llm_calls.author_batch import AuthoredBatchSlideRequest, author_slides_html
from utils.llm_calls.author_slide import Brand


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


def _wave_shapes(remaining: int) -> list[tuple[int, ...]]:
    state = AuthoredBatchPolicyState(first_success_observed=True)
    wave = plan_authored_batch_wave(range(remaining), state)
    return [batch.slide_indices for batch in wave.batches]


def test_first_wave_is_serial_then_first_success_unlocks_two_calls() -> None:
    initial = new_authored_batch_policy_state()
    first_wave = plan_authored_batch_wave(range(6), initial)

    assert initial.concurrency == 1
    assert [batch.slide_indices for batch in first_wave.batches] == [(0, 1, 2)]

    unlocked = advance_authored_batch_policy(initial, succeeded=True)
    second_wave = plan_authored_batch_wave(range(3, 9), unlocked)

    assert unlocked.concurrency == 2
    assert [batch.slide_indices for batch in second_wave.batches] == [(3, 4, 5), (6, 7, 8)]


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


@pytest.mark.parametrize(
    "error",
    [500, 503, "HTTP 500", "request timed out", "provider_circuit_open", "public_provider_bridge_exception", TimeoutError()],
)
def test_one_overload_failure_permanently_downgrades_a_job(error: object) -> None:
    unlocked = AuthoredBatchPolicyState(first_success_observed=True)
    serial = advance_authored_batch_policy(unlocked, succeeded=False, error=error)

    assert is_authored_provider_overload(error) is True
    assert serial.permanently_serial is True
    assert serial.concurrency == 1
    assert [batch.slide_indices for batch in plan_authored_batch_wave(range(6), serial).batches] == [(0, 1, 2)]
    assert advance_authored_batch_policy(serial, succeeded=True).concurrency == 1


def test_non_overload_failure_does_not_change_unlocked_policy() -> None:
    state = AuthoredBatchPolicyState(first_success_observed=True)

    assert advance_authored_batch_policy(state, succeeded=False, error="HTTP 429") == state


def test_recovery_batches_contain_only_failed_indices_and_cap_at_three() -> None:
    batches = plan_authored_recovery_batches(
        [1, 4, 7, 9], successful_slide_indices=[0, 2, 3, 5, 6, 8]
    )

    assert [batch.slide_indices for batch in batches] == [(1, 4, 7), (9,)]
    assert all(batch.recovery for batch in batches)
    assert all(len(batch.slide_indices) <= 3 for batch in batches)


def test_recovery_rejects_an_already_successful_slide() -> None:
    with pytest.raises(ValueError, match="must not include successful"):
        plan_authored_recovery_batches([1, 2], successful_slide_indices=[2])


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
    assert calls[0]["max_tokens"] == 24_000
    request_parts = calls[0]["messages"][1].content
    assert sum(
        1
        for part in request_parts
        if getattr(part, "text", "").startswith("\n\nBEGIN REQUEST FOR SLIDE INDEX")
    ) == 3


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
