"""The content brief may spend only a share of the remaining operation deadline.

Before this the brief stage's own wall ceiling (900 s) equalled a small deck's
whole base budget, so one slow brief could exhaust the operation and leave the
outline, the authoring waves, the repair rounds and the review with nothing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from api.v1.ppt.endpoints.content_brief_operation_share import (
    DEFAULT_OPERATION_DEADLINE_SHARE,
    clamp_brief_policy_to_operation,
    operation_deadline_share,
)
from api.v1.ppt.endpoints.content_brief_policy import (
    BRIEF_STATUS_APPLIED,
    ContentBriefTimeoutPolicy,
    content_brief_timeout_policy,
    run_content_brief_with_policy,
)
from services.public_operation_deadline_scope import (
    public_operation_deadline_at,
    public_operation_deadline_scope,
    remaining_public_operation_seconds,
)


@pytest.fixture(autouse=True)
def _shipped_brief_policy(monkeypatch):
    for knob in (
        "CONTENT_BRIEF_TIMEOUT_SECONDS",
        "CONTENT_BRIEF_TIMEOUT_PER_KCHAR_SECONDS",
        "CONTENT_BRIEF_TIMEOUT_MAX_SECONDS",
        "CONTENT_BRIEF_TOTAL_BUDGET_SECONDS",
        "CONTENT_BRIEF_OPERATION_DEADLINE_SHARE",
    ):
        monkeypatch.delenv(knob, raising=False)


def test_the_share_defaults_to_a_quarter_and_rejects_unusable_values(monkeypatch):
    assert DEFAULT_OPERATION_DEADLINE_SHARE == 0.25
    assert operation_deadline_share() == 0.25

    monkeypatch.setenv("CONTENT_BRIEF_OPERATION_DEADLINE_SHARE", "0.4")
    assert operation_deadline_share() == 0.4

    # A value outside ``(0, 1]`` falls back to the default.  (A non-numeric
    # value is rejected earlier, when the runtime settings snapshot coerces the
    # registry ``number``, so it never reaches this accessor.)
    for unusable in ("0", "-0.2", "1.5"):
        monkeypatch.setenv("CONTENT_BRIEF_OPERATION_DEADLINE_SHARE", unusable)
        assert operation_deadline_share() == 0.25


def test_a_full_size_deck_keeps_the_whole_brief_budget():
    """A 24-slide deck is budgeted 6622 s; a quarter of that is above 1500 s."""

    policy = content_brief_timeout_policy()
    assert policy.total_budget_seconds == 1500.0

    unchanged = clamp_brief_policy_to_operation(policy, 6622)
    assert unchanged is policy
    assert unchanged.timeout_for(0) == 600.0
    assert unchanged.timeout_for(500_000) == 900.0
    # Under the whole budget the retry gets its full base term.
    assert unchanged.retry_timeout_for(0, 600.0) == 600.0


def test_a_small_deck_clamps_the_brief_to_its_share():
    """A 3258 s one-slide budget gives the brief 814.5 s, not the whole deck."""

    policy = content_brief_timeout_policy()
    clamped = clamp_brief_policy_to_operation(policy, 3258)

    assert clamped.total_budget_seconds == pytest.approx(814.5)
    assert clamped.max_seconds == pytest.approx(814.5)
    # The scaled deadline can no longer exceed the share, however large the input.
    assert clamped.timeout_for(500_000) == pytest.approx(814.5)
    # The retry still has to fit inside the same stage budget, and what a
    # 600 s first attempt leaves (214.5 s) is below the 480 s retry floor:
    # the stage falls back at once instead of spending it on a doomed call.
    assert clamped.retry_floor == pytest.approx(480.0)
    assert clamped.retry_timeout_for(0, 600.0) == 0.0
    assert clamped.retry_timeout_for(0, 814.5) == 0.0


def test_no_operation_deadline_leaves_the_policy_untouched():
    policy = content_brief_timeout_policy()
    assert clamp_brief_policy_to_operation(policy, None) is policy


def test_an_exhausted_deadline_clamps_the_stage_to_zero():
    policy = content_brief_timeout_policy()
    clamped = clamp_brief_policy_to_operation(policy, 0)
    assert clamped.total_budget_seconds == 0.0
    assert clamped.timeout_for(1000) == 0.0


def test_the_scope_reports_what_is_left_and_nothing_outside_an_operation():
    assert public_operation_deadline_at() is None
    assert remaining_public_operation_seconds() is None

    now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
    with public_operation_deadline_scope(now + timedelta(seconds=1800)):
        assert public_operation_deadline_at() == now + timedelta(seconds=1800)
        assert remaining_public_operation_seconds(now=now) == 1800.0
        # A deadline already in the past reads as an exhausted allowance.
        assert remaining_public_operation_seconds(
            now=now + timedelta(seconds=3600)
        ) == 0.0

    assert remaining_public_operation_seconds() is None


def test_run_content_brief_applies_the_clamp_to_the_running_stage():
    """The clamp reaches the real wait: a brief slower than its share times out."""

    slept: list[float] = []

    async def slow_brief(*_args, **_kwargs):
        await asyncio.sleep(5)
        raise AssertionError("the clamped deadline should have fired first")

    async def quick_brief(*_args, **_kwargs):
        return "brief"

    async def scenario():
        # 0.4 s of remaining deadline gives the brief 0.1 s at the default share.
        outcome = await run_content_brief_with_policy(
            slow_brief,
            content="c",
            language="ko",
            additional_context=None,
            tone=None,
            verbosity=None,
            instructions=None,
            policy=ContentBriefTimeoutPolicy(
                base_seconds=30.0,
                per_kchar_seconds=0.0,
                max_seconds=30.0,
                total_budget_seconds=30.0,
            ),
            remaining_operation_seconds=0.4,
        )
        slept.append(outcome.timeout_seconds)
        assert outcome.timeout_seconds == pytest.approx(0.1)
        assert outcome.brief is None

        # The same policy without an operation deadline keeps its 30 s.
        applied = await run_content_brief_with_policy(
            quick_brief,
            content="c",
            language="ko",
            additional_context=None,
            tone=None,
            verbosity=None,
            instructions=None,
            policy=ContentBriefTimeoutPolicy(
                base_seconds=30.0,
                per_kchar_seconds=0.0,
                max_seconds=30.0,
                total_budget_seconds=30.0,
            ),
        )
        assert applied.status == BRIEF_STATUS_APPLIED
        assert applied.timeout_seconds == 30.0

    asyncio.run(scenario())
    assert slept == [pytest.approx(0.1)]
