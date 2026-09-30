"""A document the provider cut at its output-token ceiling is its own failure.

The stream client here is shaped like ``llmai``'s chatgpt client: it drains the
raw Responses events through ``self._client.responses`` (which the guarded
transport wraps) and translates them into llmai stream chunks, building the
completion chunk's usage from ``response.completed`` only -- so a stream that
ends with ``response.incomplete`` arrives with ``usage=None``, exactly the
0.3.7 behaviour the reader has to see through.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from services import authored_author_stage_rescue as rescue_mod
from services.authored_batch_policy import new_authored_batch_policy_state
from services.authored_wave_scheduler import AuthoredWaveSchedule
from services.authored_author_stage_deferral import (
    AUTHOR_BUDGET_EXHAUSTED_CODE,
    author_failure_is_rescuable,
    author_failure_retryable,
    author_stage_failure,
    defer_rescuable_author_failures,
)
from services.authored_generation_timing_receipt_e4 import flagged_slide_entries
from services.provider_usage_instrumentation import response_usage
from services.generation_core.authoring import author_deck as author_deck_mod
from services.generation_core.authoring.author_deck import (
    AuthoredBatchFailure,
    AuthoredProviderBudgetExhausted,
)
from services.generation_core.authoring.author_slide import Brand
from services.generation_core.authoring.author_slide_fallback import (
    reraise_author_call_error,
    text_only_fallback_applies,
)
from services.generation_core.authoring.author_slide_stream import (
    OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE,
    OUTPUT_TOKEN_LIMIT_CORRECTION_HINT,
    OUTPUT_TOKEN_LIMIT_GUIDANCE,
    OUTPUT_TOKEN_LIMIT_REASON,
    AuthoredStreamAborted,
    output_token_limit_in_cause_chain,
    raise_if_output_token_limited,
    stream_author_call,
)
from utils.provider_terminal_outcome import (
    ProviderTerminalOutcome,
    ProviderTerminalUsage,
)

_DOCUMENT = (
    "<!DOCTYPE html><html><head></head><body>"
    "<h1 data-qid='s.title'>authored</h1></body></html>"
)
_TRUNCATED = _DOCUMENT[: _DOCUMENT.index("</body>")] + "<p>cut mid-"
_USAGE = SimpleNamespace(input_tokens=120, output_tokens=16_000, total_tokens=16_120)


def _run(coro):
    return asyncio.run(coro)


# --- the llmai-shaped stream client over a guarded raw transport --------------


def _delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _incomplete(usage=_USAGE):
    return SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=usage,
        ),
    )


def _completed(usage=_USAGE):
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(status="completed", incomplete_details=None, usage=usage),
    )


class _Responses:
    def __init__(self, events):
        self.events = events

    def create(self, **_kwargs):
        return iter(self.events)


class _RawClient:
    def __init__(self, events):
        self.responses = _Responses(events)

    def with_options(self, **_options):
        return _RawClient(self.responses.events)


class _CompletionChunk:
    """A mutable completion chunk, like llmai's pydantic model."""

    type = "completion"

    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


class _CodexStreamClient:
    def __init__(self, raw_events):
        self._client = _RawClient(raw_events)
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return self._events()

    def _events(self):
        text = ""
        final_response = None
        for event in self._client.responses.create(stream=True):
            if event.type == "response.output_text.delta":
                text += event.delta
                yield SimpleNamespace(type="content", chunk=event.delta)
            elif event.type == "response.completed":
                final_response = event.response
        usage = getattr(final_response, "usage", None)
        yield _CompletionChunk(text, usage)


_CodexStreamClient.__module__ = "llmai.chatgpt.client"


def _stream(raw_events):
    return _run(
        stream_author_call(
            _CodexStreamClient(raw_events), {"model": "test-model", "max_tokens": 16_000}
        )
    )


def test_length_without_a_document_end_aborts_as_output_token_limit():
    with pytest.raises(AuthoredStreamAborted) as raised:
        _stream([_delta(_TRUNCATED[:30]), _delta(_TRUNCATED[30:]), _incomplete()])

    assert raised.value.reason == OUTPUT_TOKEN_LIMIT_REASON
    assert raised.value.retryable is True
    assert raised.value.chars == len(_TRUNCATED)


def test_length_with_the_document_end_present_is_kept():
    response = _stream([_delta(_DOCUMENT), _incomplete()])

    assert response.content == _DOCUMENT


def test_stop_with_a_truncated_document_is_returned_unchanged():
    """A model that stopped on its own mid-document is still admission's case."""

    response = _stream([_delta(_TRUNCATED), _completed()])

    assert response.content == _TRUNCATED
    assert response.usage is _USAGE


def test_usage_is_recovered_from_the_terminal_event_when_the_chunk_lost_it():
    response = _stream([_delta(_DOCUMENT), _incomplete()])

    assert response.usage is not None
    assert response.usage.output_tokens == 16_000
    # The receipt reads the same fields, so the token counts survive.
    assert response_usage(response) == ("reported", 120, 16_000, None)


def test_a_completion_that_keeps_its_own_usage_is_not_overwritten():
    response = _stream([_delta(_DOCUMENT), _completed()])

    assert response.usage is _USAGE


# --- the non-streaming seam ---------------------------------------------------


def _outcome(finish_reason, usage=None):
    return ProviderTerminalOutcome(finish_reason, usage=usage)


def test_the_non_streaming_check_uses_the_same_rule():
    with pytest.raises(AuthoredStreamAborted) as raised:
        raise_if_output_token_limited(_outcome("length"), _TRUNCATED)
    assert raised.value.reason == OUTPUT_TOKEN_LIMIT_REASON

    raise_if_output_token_limited(_outcome("length"), _DOCUMENT + "\n  ")
    raise_if_output_token_limited(_outcome("stop"), _TRUNCATED)
    raise_if_output_token_limited(None, _TRUNCATED)


# --- how the abort travels through the attempt loop ---------------------------


def test_the_abort_survives_the_author_call_boundary_untouched():
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=60_000)

    with pytest.raises(AuthoredStreamAborted) as raised:
        reraise_author_call_error(abort)

    assert raised.value is abort
    # Dropping the images does not change the output budget.
    assert text_only_fallback_applies(abort) is False


def test_the_attempt_loop_classifies_the_abort_as_a_size_correction():
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=60_000)

    correction = author_deck_mod._classify_author_validation_error(abort)

    assert correction.category == "output_token_limit"
    assert correction.hint == OUTPUT_TOKEN_LIMIT_CORRECTION_HINT
    assert "</html>" in correction.hint
    assert author_deck_mod._author_retry_correction(abort) == correction.hint


def test_the_cause_chain_walk_recognises_the_abort_and_its_budget_code():
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON)
    exhausted = AuthoredProviderBudgetExhausted(
        OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE, abort
    )
    wrapped = RuntimeError("stage")
    wrapped.__cause__ = abort

    assert output_token_limit_in_cause_chain(abort) is True
    assert output_token_limit_in_cause_chain(exhausted) is True
    assert output_token_limit_in_cause_chain(wrapped) is True
    assert output_token_limit_in_cause_chain(
        AuthoredStreamAborted("output_budget_exceeded")
    ) is False
    assert output_token_limit_in_cause_chain(RuntimeError("other")) is False
    assert output_token_limit_in_cause_chain("not an error") is False


# --- the batch lane hands it to the rescue ------------------------------------


def _profile():
    return SimpleNamespace(strict_dom_gate=True, allow_summarization=True)


def test_the_batch_lane_defers_the_abort_to_the_rescue(monkeypatch):
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=60_000)
    failure = AuthoredBatchFailure(error=abort, correction_hint="compact")

    assert author_failure_is_rescuable(failure) is True
    assert author_failure_retryable(abort) is False

    monkeypatch.setattr(
        "services.authored_author_stage_deferral.terminal_fallback_active",
        lambda **_kwargs: True,
    )
    deferred = defer_rescuable_author_failures(
        1,
        successful={},
        failures={0: failure},
        attempt_counts={0: 2},
        profile=_profile(),
        reference_mode="style",
        source_reflow=False,
    )

    assert deferred[0].code == OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE
    assert deferred[0].code != AUTHOR_BUDGET_EXHAUSTED_CODE
    assert output_token_limit_in_cause_chain(deferred[0]) is True


def test_the_failure_envelope_tells_the_operator_what_to_change():
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=60_000)
    exhausted = AuthoredProviderBudgetExhausted(
        OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE, abort
    )

    envelope = author_stage_failure(3, exhausted, provider_attempt_count=2)

    assert envelope.code == "authored_slide_author_failed"
    assert envelope.retryable is False
    assert envelope.safe_message == OUTPUT_TOKEN_LIMIT_GUIDANCE
    assert "output-token ceiling" in envelope.as_safe_dict()["message"]
    # An ordinary spent budget keeps today's message.
    ordinary = author_stage_failure(
        3, AuthoredProviderBudgetExhausted("authored_invalid_html_budget_exhausted", "x")
    )
    assert "output-token ceiling" not in ordinary.safe_message


# --- the guarded (non-streaming) chatgpt path names the ceiling too -----------


def _guard_incomplete_error():
    from utils.provider_idempotency import ProviderOutcomeError

    return ProviderOutcomeError(
        "public_provider_response_incomplete", recovery_allowed=False
    )


def test_the_guard_verdict_becomes_the_ceiling_abort_only_for_a_length_outcome():
    from services.generation_core.authoring.author_slide_stream import (
        output_token_limit_from_provider_outcome,
    )

    aborted = output_token_limit_from_provider_outcome(
        _guard_incomplete_error(), _outcome("length")
    )
    assert isinstance(aborted, AuthoredStreamAborted)
    assert aborted.reason == OUTPUT_TOKEN_LIMIT_REASON

    # Any other incomplete reason keeps the guard's terminal verdict.
    assert output_token_limit_from_provider_outcome(
        _guard_incomplete_error(), _outcome("content_filter")
    ) is None
    assert output_token_limit_from_provider_outcome(
        _guard_incomplete_error(), None
    ) is None
    # And an unrelated provider code is never rewritten.
    assert output_token_limit_from_provider_outcome(
        RuntimeError("public_provider_response_failed"), _outcome("length")
    ) is None


def test_the_ceiling_abort_outranks_the_guard_code_it_wraps():
    """Chained from the guard's error, the abort must still be the rescue's."""
    from services.authored_author_stage_deferral import author_failure_is_rescuable

    guard_error = _guard_incomplete_error()
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON)
    abort.__cause__ = guard_error

    assert output_token_limit_in_cause_chain(abort)
    assert author_failure_retryable(abort) is False
    failure = SimpleNamespace(error=abort, rejected_html=None, correction_hint=None)
    assert author_failure_is_rescuable(failure) is True

    envelope = author_stage_failure(0, abort, provider_attempt_count=1)
    assert envelope.code == "authored_slide_author_failed"
    assert envelope.safe_message == OUTPUT_TOKEN_LIMIT_GUIDANCE

    # The bare guard verdict, with no ceiling behind it, stays terminal.
    bare = SimpleNamespace(error=guard_error, rejected_html=None, correction_hint=None)
    assert author_failure_is_rescuable(bare) is False
    assert author_stage_failure(0, guard_error).code == "public_provider_response_incomplete"


# --- the author-stage rescue flags it by name ---------------------------------


def _plan():
    return SimpleNamespace(
        contents=("분기 실적 요약\n매출 120억 원\n영업이익 18억 원",),
        roles=("CONTENT",),
        brand=Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR"),
        header_contract_at=lambda _index: None,
    )


def _rescue(monkeypatch, error, *, third_call):
    monkeypatch.setattr(rescue_mod, "terminal_fallback_active", lambda **_kwargs: True)
    monkeypatch.setattr(rescue_mod, "author_planned_slide", third_call)
    return _run(
        rescue_mod.rescue_author_stage(
            _plan(),
            0,
            error,
            author_kwargs={},
            profile=_profile(),
            reference_mode="style",
            source_reflow=False,
            schedule=AuthoredWaveSchedule(new_authored_batch_policy_state()),
        )
    )


def test_the_rescue_flags_the_exhausted_token_limit_by_name(monkeypatch, caplog):
    exhausted = AuthoredProviderBudgetExhausted(
        OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE,
        AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=60_000),
    )

    async def third_call_also_truncated(_plan, _index, **_kwargs):
        raise AuthoredProviderBudgetExhausted(
            OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE, "still truncated"
        )

    with caplog.at_level(logging.WARNING, logger=rescue_mod.LOGGER.name):
        rescue = _rescue(monkeypatch, exhausted, third_call=third_call_also_truncated)

    assert rescue is not None
    assert rescue.flagged_entry == {
        "index": 0,
        "reason": rescue_mod.AUTHOR_OUTPUT_TOKEN_LIMIT_REASON,
        "remakeAttempted": True,
        "remakeOutcome": "author_rescue_budget_exhausted",
    }
    assert rescue.flagged_entry["reason"] == "author:output_token_limit"
    assert flagged_slide_entries([rescue.flagged_entry]) == (rescue.flagged_entry,)
    assert "AUTHOR_MAX_TOKENS" in caplog.text
    assert "reasoning effort" in caplog.text


def test_the_rescue_flags_a_token_limit_hit_only_on_the_third_call(monkeypatch):
    ordinary = AuthoredProviderBudgetExhausted(
        "authored_header_contract_budget_exhausted", "header contract violated"
    )

    async def third_call_truncated(_plan, _index, **_kwargs):
        raise AuthoredProviderBudgetExhausted(
            OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE,
            AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON),
        )

    rescue = _rescue(monkeypatch, ordinary, third_call=third_call_truncated)

    assert rescue.flagged_entry["reason"] == rescue_mod.AUTHOR_OUTPUT_TOKEN_LIMIT_REASON


def test_an_ordinary_exhausted_budget_keeps_todays_flag(monkeypatch):
    ordinary = AuthoredProviderBudgetExhausted(
        "authored_header_contract_budget_exhausted", "header contract violated"
    )

    async def third_call_rejected(_plan, _index, **_kwargs):
        raise AuthoredProviderBudgetExhausted(
            "authored_header_contract_budget_exhausted", "still violated"
        )

    rescue = _rescue(monkeypatch, ordinary, third_call=third_call_rejected)

    assert rescue.flagged_entry["reason"] == rescue_mod.AUTHOR_ATTEMPTS_EXHAUSTED_REASON


def test_a_rescued_token_limited_slide_publishes_unflagged(monkeypatch):
    exhausted = AuthoredProviderBudgetExhausted(
        OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE,
        AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON),
    )

    async def plainer_third_call(_plan, _index, **_kwargs):
        return _DOCUMENT

    rescue = _rescue(monkeypatch, exhausted, third_call=plainer_third_call)

    assert rescue.html == _DOCUMENT
    assert rescue.flagged_entry is None


def test_terminal_usage_stands_in_for_a_missing_usage_everywhere_it_is_read():
    usage = ProviderTerminalUsage(
        input_tokens=10,
        output_tokens=20,
        details={"input_tokens_details": {"cached_tokens": 4}},
    )

    assert response_usage(SimpleNamespace(usage=usage)) == ("reported", 10, 20, 4)
