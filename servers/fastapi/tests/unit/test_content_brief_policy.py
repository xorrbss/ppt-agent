"""Unit tests for api.v1.ppt.endpoints.content_brief_policy (QUAL-07, SPEED-10).

Covers the input-scaled timeout formula, the single reduced retry after a
timeout, the timed-out fallback status, and that non-timeout failures are
propagated so the call sites keep their own 429 / retryability handling."""

import asyncio
from types import SimpleNamespace

import pytest

from api.v1.ppt.endpoints import content_brief_policy as policy


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
    monkeypatch.setenv(policy.MAX_TIMEOUT_ENV, "not-a-number")

    formula = policy.content_brief_timeout_policy()

    assert formula.base_seconds == 30.0
    assert formula.per_kchar_seconds == 2.0
    assert formula.max_seconds == policy.DEFAULT_MAX_TIMEOUT_SECONDS
    assert formula.timeout_for(10_000) == 50.0


def test_timeout_policy_max_never_drops_below_base(monkeypatch):
    monkeypatch.setenv(policy.BASE_TIMEOUT_ENV, "90")
    monkeypatch.setenv(policy.MAX_TIMEOUT_ENV, "10")

    formula = policy.content_brief_timeout_policy()

    assert formula.max_seconds == 90.0
    assert formula.timeout_for(0) == 90.0


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


def test_first_attempt_success_is_applied():
    calls = []

    async def fake_brief(*args, **kwargs):
        calls.append(args)
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
            policy=policy.ContentBriefTimeoutPolicy(base_seconds=5),
        )
    )

    assert outcome.status == "applied"
    assert outcome.attempts == 1
    assert outcome.retry_timeout_seconds is None
    assert outcome.brief.to_prompt_context() == "brief"
    assert calls == [("topic", "Korean", "ctx", "formal", "standard", None)]
    assert policy.merge_brief_context("ctx", outcome.brief) == "ctx\n\nbrief"
    assert policy.merge_brief_context("", outcome.brief) == "brief"


def test_timeout_then_retry_with_reduced_input_is_retry_applied():
    calls = []

    async def fake_brief(content, language, context, tone, verbosity, instructions, is_retry=False):
        calls.append((context, verbosity))
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
    assert calls == [("0123456789", "text-heavy"), ("01234", policy.RETRY_VERBOSITY)]
    assert outcome.retry_timeout_seconds == pytest.approx(0.05)


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

    async def fake_brief(content, language, context, tone, verbosity, instructions, is_retry=False):
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


def test_first_attempt_spending_the_whole_budget_skips_the_retry():
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
        )
    )

    assert outcome.status == "timed_out_fallback"
    assert outcome.attempts == 1
    assert calls == [1]


def test_retry_deadline_is_clamped_to_the_remaining_stage_budget():
    active = policy.ContentBriefTimeoutPolicy(
        base_seconds=600.0,
        per_kchar_seconds=0.0,
        max_seconds=900.0,
        total_budget_seconds=900.0,
    )

    assert active.retry_timeout_for(0, 600.0) == pytest.approx(300.0)
    assert active.retry_timeout_for(0, 900.0) == 0.0
    assert active.retry_timeout_for(0, 0.0) == pytest.approx(600.0)


def test_defaults_cover_the_measured_reasoning_model_brief_latency():
    """gpt-6-astra medium measured 325 s and 455 s for a prompt-only brief."""

    active = policy.content_brief_timeout_policy()

    assert active.timeout_for(0) >= 500.0
    assert active.total_budget_seconds >= active.max_seconds
