"""Unit tests for services.content_brief_policy (QUAL-07, SPEED-10).

Covers the input-scaled timeout formula, the stage budget and retry floor, the
single reduced retry after a timeout, the shared physical-call budget, the
timed-out fallback status, and that non-timeout failures are propagated so the
call sites keep their own 429 / retryability handling."""

import asyncio
from types import SimpleNamespace

import pytest

from services import content_brief_policy as policy
from models.content_brief_model import ContentBrief
from utils.llm_calls import generate_content_brief as brief_module
from utils.structured_call_budget import StructuredProviderCallBudget


def _run(coroutine):
    return asyncio.new_event_loop().run_until_complete(coroutine)


# --- timeout formula ----------------------------------------------------------


def test_timeout_formula_scales_with_input_and_caps():
    formula = policy.ContentBriefTimeoutPolicy()

    assert formula.timeout_for(0) == 600.0
    assert formula.timeout_for(50_000) == 650.0
    assert formula.timeout_for(300_000) == 900.0
    assert formula.timeout_for(500_000) == 900.0
    assert formula.timeout_for(-10) == 600.0


def test_timeout_policy_reads_env_knobs_and_ignores_garbage(monkeypatch):
    monkeypatch.setenv(policy.BASE_TIMEOUT_ENV, "30")
    monkeypatch.setenv(policy.PER_KCHAR_TIMEOUT_ENV, "2")
    # A non-numeric value is rejected earlier, when the runtime settings
    # snapshot coerces the registry ``number``; a negative one reaches the
    # accessor and is treated as unset.
    monkeypatch.setenv(policy.MAX_TIMEOUT_ENV, "-1")

    formula = policy.content_brief_timeout_policy()

    assert formula.base_seconds == 30.0
    assert formula.per_kchar_seconds == 2.0
    assert formula.max_seconds == policy.DEFAULT_MAX_TIMEOUT_SECONDS
    assert formula.timeout_for(10_000) == 50.0
    # Unset, the total derives from the effective max and base, and the floor
    # from the base.
    assert formula.total_budget_seconds == 930.0
    assert formula.retry_floor == pytest.approx(24.0)

    monkeypatch.setenv(policy.TOTAL_BUDGET_ENV, "1000")
    monkeypatch.setenv(policy.RETRY_FLOOR_ENV, "100")
    formula = policy.content_brief_timeout_policy()
    assert formula.total_budget_seconds == 1000.0
    assert formula.retry_floor == 100.0


def test_timeout_policy_max_never_drops_below_base(monkeypatch):
    monkeypatch.setenv(policy.BASE_TIMEOUT_ENV, "90")
    monkeypatch.setenv(policy.MAX_TIMEOUT_ENV, "10")

    formula = policy.content_brief_timeout_policy()

    assert formula.max_seconds == 90.0
    assert formula.timeout_for(0) == 90.0
    assert formula.total_budget_seconds == 180.0


def test_defaults_let_the_retry_win_where_the_old_total_could_not():
    """gpt-6-astra medium measured 325 s and 455 s for a prompt-only brief.

    Under the former 900 s total a 600 s first attempt left the retry 300 s:
    a guaranteed timeout that burned 300 s of the deck's deadline.  The total
    is now the sum of both attempts (cap plus base) and a retry below the
    480 s floor is not made at all.
    """

    active = policy.content_brief_timeout_policy()

    assert active.timeout_for(0) >= 500.0
    assert active.total_budget_seconds == 1500.0
    assert active.total_budget_seconds == active.max_seconds + active.base_seconds
    assert active.retry_floor == pytest.approx(480.0)
    assert active.retry_floor > 455.0
    # A prompt-only brief that timed out at 600 s: the retry gets its full 600 s.
    assert active.retry_timeout_for(0, 600.0) == 600.0
    # A 500k-character document timed out at the 900 s cap: the reduced retry
    # (250k characters, needing 850 s) gets the 600 s that are left.
    assert active.timeout_for(500_000) == 900.0
    assert active.retry_timeout_for(250_000, 900.0) == 600.0
    # The old total: 300 s is below the floor, so the retry is skipped.
    old = policy.ContentBriefTimeoutPolicy(total_budget_seconds=900.0)
    assert old.retry_timeout_for(0, 600.0) == 0.0


def test_retry_deadline_is_clamped_to_the_remaining_stage_budget():
    active = policy.ContentBriefTimeoutPolicy(
        base_seconds=600.0,
        per_kchar_seconds=0.0,
        max_seconds=900.0,
        total_budget_seconds=1500.0,
    )

    assert active.retry_timeout_for(0, 0.0) == pytest.approx(600.0)
    assert active.retry_timeout_for(0, 600.0) == pytest.approx(600.0)
    assert active.retry_timeout_for(0, 1000.0) == pytest.approx(500.0)
    # 400 s left is below the 480 s floor: skip rather than spend it.
    assert active.retry_timeout_for(0, 1100.0) == 0.0
    assert active.retry_timeout_for(0, 1500.0) == 0.0
    # An explicit floor lowers the bar.
    lowered = policy.ContentBriefTimeoutPolicy(
        base_seconds=600.0, max_seconds=900.0, total_budget_seconds=1500.0,
        retry_floor_seconds=100.0,
    )
    assert lowered.retry_timeout_for(0, 1100.0) == pytest.approx(400.0)


def test_input_chars_counts_every_text_the_brief_reads():
    assert policy.content_brief_input_chars("abc", "defgh", None) == 8
    assert policy.content_brief_input_chars(None, None, "xy") == 2


def test_reduced_context_keeps_the_head_at_half_length():
    assert policy.reduced_brief_context("0123456789") == "01234"
    assert policy.reduced_brief_context("") == ""
    assert policy.reduced_brief_context(None) is None


# --- run_content_brief_with_policy ---------------------------------------------


def _brief(text: str):
    return SimpleNamespace(to_prompt_context=lambda: text)


def _fake_clock(*readings: float):
    """A clock that returns ``readings`` in order and then repeats the last."""

    values = list(readings)

    def clock() -> float:
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    return clock


def test_first_attempt_success_is_applied():
    calls = []

    async def fake_brief(*args, **kwargs):
        calls.append((args, kwargs))
        return _brief("brief")

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="topic",
            language="Korean",
            additional_context="ctx",
            tone="formal",
            verbosity="standard",
            instructions=None,
            presentation_id="p1",
            policy=policy.ContentBriefTimeoutPolicy(base_seconds=5, per_kchar_seconds=0.0),
        )
    )

    assert outcome.status == "applied"
    assert outcome.attempts == 1
    assert outcome.retry_timeout_seconds is None
    assert outcome.retry_skipped_reason is None
    assert outcome.brief.to_prompt_context() == "brief"
    [(args, kwargs)] = calls
    assert args == ("topic", "Korean", "ctx", "formal", "standard", None)
    assert isinstance(kwargs["call_budget"], StructuredProviderCallBudget)
    assert kwargs["call_timeout_seconds"] == 5.0
    assert "is_retry" not in kwargs
    assert policy.merge_brief_context("ctx", outcome.brief) == "ctx\n\nbrief"
    assert policy.merge_brief_context("", outcome.brief) == "brief"


def test_timeout_then_retry_with_reduced_input_is_retry_applied():
    calls = []

    async def fake_brief(content, language, context, tone, verbosity, instructions, **kwargs):
        calls.append((context, verbosity, kwargs))
        if len(calls) == 1:
            await asyncio.sleep(10)
        return _brief("second")

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="topic",
            language=None,
            additional_context="0123456789",
            tone=None,
            verbosity="text-heavy",
            instructions=None,
            presentation_id="p2",
            policy=policy.ContentBriefTimeoutPolicy(
                base_seconds=0.05, per_kchar_seconds=0.0, max_seconds=1.0
            ),
        )
    )

    assert outcome.status == "retry_applied"
    assert outcome.attempts == 2
    assert outcome.brief.to_prompt_context() == "second"
    assert [(context, verbosity) for context, verbosity, _ in calls] == [
        ("0123456789", "text-heavy"),
        ("01234", policy.RETRY_VERBOSITY),
    ]
    assert outcome.retry_timeout_seconds == pytest.approx(0.05)
    first, second = (kwargs for _, _, kwargs in calls)
    assert second["is_retry"] is True
    # Both attempts share ONE physical-call budget and each carries its own
    # transport deadline.
    assert first["call_budget"] is second["call_budget"]
    assert first["call_timeout_seconds"] == pytest.approx(0.05)
    assert second["call_timeout_seconds"] == pytest.approx(outcome.retry_timeout_seconds)


def test_retry_timeout_is_recomputed_from_the_reduced_input():
    async def fake_brief(*_args, **_kwargs):
        await asyncio.sleep(10)

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="",
            language=None,
            additional_context="x" * 100_000,
            tone=None,
            verbosity=None,
            instructions=None,
            policy=policy.ContentBriefTimeoutPolicy(
                base_seconds=0.02, per_kchar_seconds=0.0004, max_seconds=5.0
            ),
        )
    )

    assert outcome.status == "timed_out_fallback"
    assert outcome.brief is None
    assert outcome.attempts == 2
    assert outcome.timeout_seconds == pytest.approx(0.06)
    assert outcome.retry_timeout_seconds == pytest.approx(0.04)


def test_both_attempts_timing_out_is_timed_out_fallback():
    attempts = 0

    async def fake_brief(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(10)

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="topic",
            language=None,
            additional_context=None,
            tone=None,
            verbosity=None,
            instructions=None,
            presentation_id="p3",
            policy=policy.ContentBriefTimeoutPolicy(base_seconds=0.05, max_seconds=0.05),
        )
    )

    assert outcome.status == "timed_out_fallback"
    assert outcome.brief is None
    assert attempts == 2
    assert outcome.retry_skipped_reason is None


def test_non_timeout_failures_propagate_without_retry():
    attempts = 0

    async def fake_brief(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        _run(
            policy.run_content_brief_with_policy(
                fake_brief,
                content="topic",
                language=None,
                additional_context=None,
                tone=None,
                verbosity=None,
                instructions=None,
                policy=policy.ContentBriefTimeoutPolicy(base_seconds=5),
            )
        )

    assert attempts == 1


def test_skipped_status_is_logged_with_the_presentation_id(caplog):
    with caplog.at_level("INFO", logger=policy.LOGGER.name):
        policy.log_content_brief_skipped("p4", ValueError("bad json"))

    assert "status=skipped" in caplog.text
    assert "presentation=p4" in caplog.text


def test_retry_runs_under_its_own_provider_call_identity():
    """The retry must not reopen the terminal row the timed-out attempt left.

    A provider without idempotency support rejects the reuse outright
    (``public_provider_terminal_replay_unsupported``), which turned a brief
    timeout into a failed generation instead of the documented fallback.
    """

    seen = []

    async def fake_brief(*_args, is_retry=False, **_kwargs):
        seen.append(is_retry)
        if len(seen) == 1:
            await asyncio.sleep(10)
        return _brief("second")

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="topic",
            language=None,
            additional_context=None,
            tone=None,
            verbosity=None,
            instructions=None,
            presentation_id="p-retry-identity",
            policy=policy.ContentBriefTimeoutPolicy(
                base_seconds=0.05, per_kchar_seconds=0.0, max_seconds=1.0
            ),
        )
    )

    assert outcome.status == "retry_applied"
    assert seen == [False, True]


def test_retry_slot_suffix_reaches_the_structured_call(monkeypatch):
    """End to end through the real ``generate_content_brief``: ``is_retry=True``
    must arrive at ``generate_structured_with_schema_retries`` as
    ``semantic_slot_suffix=".brief-retry"``, with the shared budget and each
    attempt's transport deadline.  The identity test above passes even if that
    wiring is removed; this one does not."""

    structured_calls = []

    async def fake_structured(client, model, **kwargs):
        structured_calls.append(kwargs)
        if len(structured_calls) == 1:
            await asyncio.sleep(10)
        return ContentBrief(title="Second attempt").model_dump()

    monkeypatch.setattr(brief_module, "generate_structured_with_schema_retries", fake_structured)
    monkeypatch.setattr(brief_module, "get_client", lambda config: object())
    monkeypatch.setattr(brief_module, "get_llm_config", lambda: None)
    monkeypatch.setattr(brief_module, "get_model", lambda: "test-model")

    outcome = _run(
        policy.run_content_brief_with_policy(
            brief_module.generate_content_brief,
            content="topic",
            language="Korean",
            additional_context="0123456789",
            tone=None,
            verbosity="standard",
            instructions=None,
            presentation_id="p-suffix",
            policy=policy.ContentBriefTimeoutPolicy(
                base_seconds=0.05, per_kchar_seconds=0.0, max_seconds=1.0
            ),
        )
    )

    assert outcome.status == "retry_applied"
    assert outcome.brief.title == "Second attempt"
    first, second = structured_calls
    assert (first["is_retry"], first["semantic_slot_suffix"]) == (False, "")
    assert (second["is_retry"], second["semantic_slot_suffix"]) == (True, ".brief-retry")
    assert first["call_budget"] is second["call_budget"]
    assert isinstance(first["call_budget"], StructuredProviderCallBudget)
    assert first["call_timeout_seconds"] == pytest.approx(0.05)
    assert second["call_timeout_seconds"] == pytest.approx(outcome.retry_timeout_seconds)


def test_first_attempt_spending_the_whole_budget_skips_the_retry():
    """Deterministic through the injected clock: the first attempt's elapsed
    wall is read as 1000 s against a 0.05 s stage budget, so the arithmetic
    never depends on how late ``wait_for`` fired on this machine."""

    calls = []

    async def fake_brief(*_args, **_kwargs):
        calls.append(1)
        await asyncio.sleep(10)

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="topic",
            language=None,
            additional_context=None,
            tone=None,
            verbosity=None,
            instructions=None,
            presentation_id="p-budget",
            policy=policy.ContentBriefTimeoutPolicy(
                base_seconds=0.05,
                per_kchar_seconds=0.0,
                max_seconds=1.0,
                total_budget_seconds=0.05,
            ),
            clock=_fake_clock(0.0, 1000.0),
        )
    )

    assert outcome.status == "timed_out_fallback"
    assert outcome.attempts == 1
    assert outcome.retry_timeout_seconds == 0.0
    assert outcome.retry_skipped_reason == policy.RETRY_SKIPPED_BELOW_FLOOR
    assert calls == [1]


def test_retry_below_the_floor_falls_back_at_once(caplog):
    """What is left after the first attempt (0.03 s) is under the 0.04 s floor
    derived from the base, so no second call is made and none of the deck's
    deadline is spent on it."""

    calls = []

    async def fake_brief(*_args, **_kwargs):
        calls.append(1)
        await asyncio.sleep(10)

    with caplog.at_level("WARNING", logger=policy.LOGGER.name):
        outcome = _run(
            policy.run_content_brief_with_policy(
                fake_brief,
                content="topic",
                language=None,
                additional_context=None,
                tone=None,
                verbosity=None,
                instructions=None,
                presentation_id="p-floor",
                policy=policy.ContentBriefTimeoutPolicy(
                    base_seconds=0.05,
                    per_kchar_seconds=0.0,
                    max_seconds=0.05,
                    total_budget_seconds=0.08,
                ),
                clock=_fake_clock(0.0, 0.05),
            )
        )

    assert outcome.status == "timed_out_fallback"
    assert outcome.attempts == 1
    assert outcome.retry_skipped_reason == policy.RETRY_SKIPPED_BELOW_FLOOR
    assert calls == [1]
    assert "retry_below_floor" in caplog.text


def test_retry_is_skipped_when_the_first_attempt_used_both_physical_calls():
    """The two attempts share one two-call budget: a first attempt that spent
    both (a transient retry inside it, then the timeout) leaves the retry no
    call to make, so the stage falls back instead of raising a 503."""

    calls = []

    async def fake_brief(*_args, call_budget, **_kwargs):
        calls.append(1)
        call_budget.consume()
        call_budget.consume()
        await asyncio.sleep(10)

    outcome = _run(
        policy.run_content_brief_with_policy(
            fake_brief,
            content="topic",
            language=None,
            additional_context=None,
            tone=None,
            verbosity=None,
            instructions=None,
            presentation_id="p-calls",
            policy=policy.ContentBriefTimeoutPolicy(
                base_seconds=0.05, per_kchar_seconds=0.0, max_seconds=1.0
            ),
        )
    )

    assert outcome.status == "timed_out_fallback"
    assert outcome.attempts == 1
    assert outcome.retry_skipped_reason == policy.RETRY_SKIPPED_CALLS_EXHAUSTED
    assert calls == [1]
