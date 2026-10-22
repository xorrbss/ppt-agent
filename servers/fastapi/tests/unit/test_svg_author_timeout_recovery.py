"""SVG page author: transport-timeout recovery through the existing overload retry branch.

A replayable timeout gets one page retry (bounded run-wide by
``MAX_TIMEOUT_RETRIES_PER_RUN``); a deadline-exhausted timeout and a quota pause
stay terminal. The SVG author call sites carry a wall cap derived from their
own output budget.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.generation_core.drivers.svg import author
from services.generation_core.drivers.svg.collect import _RunState
from utils.llm_calls.author_slide_effort import (
    AUTHOR_MAX_TOKENS, svg_author_call_timeout_seconds,
)
from utils.provider_overload import ProviderOutcomeError, ProviderQuotaPause

_REAL_SLEEP = asyncio.sleep


def _timeout(recovery_allowed=True):
    return ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=recovery_allowed,
                                overload=True, diagnostics={"failureKind": "timeout",
                                                            "streamStage": "before_stream"})


def _overload():
    return ProviderOutcomeError("public_provider_overload", recovery_allowed=True, overload=True,
                                cooldown_seconds=30.0)


def _ctx():
    # The plain (non-source, non-raster) page branch: ``author_slide_svg``.
    return SimpleNamespace(manifests=[{}, {}, {}, {}, {}, {}], snapshot={}, canonical_assets=False,
                           source_reflow=False, raster_reconstruction=False, physical_kwargs={})


class _Provider:
    """Stands in for ``author_slide_svg``: passes ``before_dispatch`` then raises."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.dispatched = []

    async def __call__(self, *, index, before_dispatch, **_kwargs):
        await before_dispatch()
        self.dispatched.append(index)
        await _REAL_SLEEP(0)
        raise self.errors.pop(0)


@pytest.fixture
def harness(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(author.asyncio, "sleep", fake_sleep)

    def install(errors):
        provider = _Provider(errors)
        monkeypatch.setattr("services.generation_core.authoring.author_slide_svg.author_slide_svg", provider)
        return provider

    return SimpleNamespace(install=install, sleeps=sleeps)


async def _attempt(state, payload, *, index=0, provider_ordinal=1, checkpoint=None):
    return await author.attempt_author(
        ctx=_ctx(), request=SimpleNamespace(instructions=None, _document_canvas=None), index=index,
        content="slide", role="content", count=6, payload=payload, ordinal=0,
        provider_ordinal=provider_ordinal, job=None, state=state,
        checkpoint=checkpoint or AsyncMock(), async_session_maker=None)


def test_one_timeout_retries_and_records_failure_second_timeout_raises(harness):
    provider = harness.install([_timeout(), _timeout()])
    state, payload = _RunState(), {"attempts": []}

    async def run():
        first = await _attempt(state, payload, provider_ordinal=1)
        assert first == author.AuthorAttempt(svg=None, retry=True)
        assert payload["provider_failures"] == [
            {"ordinal": 1, "code": "public_provider_call_timeout", "run_timeout_retry": 1}]
        assert payload["inflight"] is None
        assert harness.sleeps == [30.0]
        with pytest.raises(ProviderOutcomeError) as second:
            await _attempt(state, payload, provider_ordinal=2)
        assert second.value.code == "public_provider_call_timeout"

    asyncio.run(run())
    assert provider.dispatched == [0, 0]
    # The page's second failure is the existing overload stop: recorded, no
    # second cooldown, and it does not spend a run-wide slot.
    assert [entry["ordinal"] for entry in payload["provider_failures"]] == [1, 2]
    assert "run_timeout_retry" not in payload["provider_failures"][1]
    assert harness.sleeps == [30.0]
    assert state.timeout_retries == 1


def test_deadline_exhausted_timeout_is_terminal(harness):
    harness.install([_timeout(recovery_allowed=False)])
    state, payload = _RunState(), {"attempts": []}
    with pytest.raises(ProviderOutcomeError) as raised:
        asyncio.run(_attempt(state, payload))
    assert raised.value.code == "public_provider_call_timeout" and not raised.value.recovery_allowed
    assert "provider_failures" not in payload
    assert payload["provider_diagnostics"][-1]["code"] == "public_provider_call_timeout"
    assert harness.sleeps == [] and state.timeout_retries == 0


def test_quota_pause_is_not_retried_by_the_page_branch(harness):
    pause = ProviderQuotaPause(reset_at=2_000_000_000, observed_at=1_999_999_000)
    harness.install([pause])
    state, payload = _RunState(), {"attempts": []}
    with pytest.raises(ProviderQuotaPause):
        asyncio.run(_attempt(state, payload))
    assert "provider_failures" not in payload
    assert harness.sleeps == [] and state.timeout_retries == 0


def test_overload_retry_unchanged_and_does_not_spend_the_timeout_cap(harness):
    harness.install([_overload()])
    state = _RunState(timeout_retries=author.MAX_TIMEOUT_RETRIES_PER_RUN)
    payload = {"attempts": []}
    result = asyncio.run(_attempt(state, payload))
    assert result.retry is True
    assert payload["provider_failures"] == [{"ordinal": 1, "code": "public_provider_overload"}]
    assert state.timeout_retries == author.MAX_TIMEOUT_RETRIES_PER_RUN


def test_run_cap_allows_three_timeout_retries_then_the_fourth_raises(harness):
    harness.install([_timeout() for _ in range(4)])
    state = _RunState()
    payloads = [{"attempts": []} for _ in range(4)]

    async def run():
        for index in range(3):
            assert (await _attempt(state, payloads[index], index=index)).retry is True
        with pytest.raises(ProviderOutcomeError) as fourth:
            await _attempt(state, payloads[3], index=3)
        assert fourth.value.code == "public_provider_call_timeout"

    asyncio.run(run())
    assert [p["provider_failures"][0]["run_timeout_retry"] for p in payloads[:3]] == [1, 2, 3]
    assert "provider_failures" not in payloads[3]
    assert state.timeout_retries == 3 and harness.sleeps == [30.0] * 3


def test_concurrent_pages_cannot_exceed_the_run_cap(harness):
    harness.install([_timeout() for _ in range(6)])
    state = _RunState()
    payloads = [{"attempts": []} for _ in range(6)]

    async def run():
        return await asyncio.gather(*(_attempt(state, payloads[index], index=index) for index in range(6)),
                                    return_exceptions=True)

    results = asyncio.run(run())
    retried = [result for result in results if isinstance(result, author.AuthorAttempt) and result.retry]
    raised = [result for result in results if isinstance(result, ProviderOutcomeError)]
    assert len(retried) == 3 and len(raised) == 3
    assert state.timeout_retries == 3
    slots = sorted(p["provider_failures"][0]["run_timeout_retry"] for p in payloads if "provider_failures" in p)
    assert slots == [1, 2, 3]


def test_state_without_counter_behaves_as_cap_available(harness):
    harness.install([_timeout()])
    state = SimpleNamespace(fatal_error=None)
    payload = {"attempts": []}
    assert asyncio.run(_attempt(state, payload)).retry is True
    assert state.timeout_retries == 1


def test_timeout_restoring_last_valid_svg_spends_no_cap_slot(harness):
    provider = harness.install([_timeout()])
    state = _RunState(timeout_retries=author.MAX_TIMEOUT_RETRIES_PER_RUN)
    payload = {"attempts": [], "last_valid_svg": "<svg/>"}
    result = asyncio.run(_attempt(state, payload))
    assert result.retry is True and payload["svg_content"] == "<svg/>" and payload["repair_unavailable"]
    assert provider.dispatched == [0] and harness.sleeps == []
    assert state.timeout_retries == author.MAX_TIMEOUT_RETRIES_PER_RUN


def test_retry_of_a_page_approved_before_a_freeze_passes_before_dispatch(harness):
    # W2: a sibling's page-local failure freezes dispatch after page 0 was
    # approved. Page 0's timeout retry still dispatches; a never-approved page
    # is stopped before admission.
    provider = harness.install([_timeout(), _timeout()])
    state = _RunState()
    payload = {"attempts": []}

    async def run():
        assert (await _attempt(state, payload, index=0, provider_ordinal=1)).retry is True
        state.dispatch_frozen = True
        with pytest.raises(ProviderOutcomeError) as retried:
            await _attempt(state, payload, index=0, provider_ordinal=2)
        assert retried.value.code == "public_provider_call_timeout"
        with pytest.raises(author.PageDispatchFrozen):
            await _attempt(state, {"attempts": []}, index=1)

    asyncio.run(run())
    assert provider.dispatched == [0, 0]
    assert state.dispatch_approved == {0}


def test_svg_author_call_timeout_seconds():
    assert svg_author_call_timeout_seconds(16000) == pytest.approx(16000 / 30.0 + 360.0)
    assert round(svg_author_call_timeout_seconds(16000)) == 893
    assert svg_author_call_timeout_seconds() == svg_author_call_timeout_seconds(AUTHOR_MAX_TOKENS)


_AUTHORING = Path(author.__file__).resolve().parents[2] / "authoring"


@pytest.mark.parametrize("module_name", [
    "author_slide_svg.py", "author_source_svg_layout.py", "author_raster_svg_layout.py"])
def test_each_svg_author_call_site_passes_its_own_wall_cap(module_name):
    tree = ast.parse((_AUTHORING / module_name).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "provider_generate_call"]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert "timeout_seconds" not in keywords
    wall = keywords["wall_timeout_seconds"]
    assert ast.unparse(wall) == "svg_author_call_timeout_seconds(budget)"


def test_author_slide_svg_sends_the_wall_cap_for_its_budget(monkeypatch):
    from services.generation_core.authoring import author_slide_svg as module
    from test_author_slide_svg_asset_offer import _stub_provider, _style

    _stub_provider(monkeypatch, "", {})
    seen = {}
    stop = ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=False, overload=True)

    def capture(*_args, **kwargs):
        seen.update(kwargs)

        def call():
            raise stop
        return call

    monkeypatch.setattr(module, "provider_generate_call", capture)
    with pytest.raises(ProviderOutcomeError):
        asyncio.run(module.author_slide_svg("# 제목", _style(), "content", 1, 3))
    # ``_stub_provider`` pins the output budget to 1000 tokens.
    assert seen["wall_timeout_seconds"] == svg_author_call_timeout_seconds(1000)
    assert "timeout_seconds" not in seen
