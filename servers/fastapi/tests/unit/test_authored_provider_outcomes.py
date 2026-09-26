"""Focused authored provider outcome and recovery-boundary contracts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls import author_slide as author_slide_mod
from utils.llm_calls.author_slide import Brand
from services.authored_author_stage_deferral import author_stage_failure
from services.authored_batch_policy import is_authored_provider_overload
from services.authored_provider_attempt_ledger import (
    ProviderAttemptReservation,
    current_provider_attempt_reservations,
    publish_provider_attempt_reservation,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
    WorkspaceOperationDeferred,
)
from services.workspace_operation_runner import (
    defer_workspace_operation_claim,
    sanitize_workspace_event_payload,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.provider_idempotency import (
    PROVIDER_CALL_TIMEOUT_SECONDS,
    ProviderOutcomeError,
    ProviderQuotaPause,
    provider_generate_call,
)
from utils.provider_overload import is_provider_overload


_VALID_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1 data-qid='title'>제목</h1></body></html>"
)


class _Responses:
    def __init__(self, events):
        self.events = events

    def create(self, **_kwargs):
        return iter(self.events)


class _RawClient:
    def __init__(self, events):
        self.responses = _Responses(events)
        self.options = None
        self.cloned_options = None

    def with_options(self, **options):
        cloned = _RawClient(self.responses.events)
        cloned.options = options
        # Kept on the original so a test can inspect what the clone received.
        self.cloned_options = options
        return cloned


class _ChatGPTClient:
    def __init__(self, events, *, result_content="unused", error=None):
        self._client = _RawClient(events)
        self.result_content = result_content
        self.error = error

    def generate(self, **_kwargs):
        if self.error is not None:
            raise self.error
        for _event in self._client.responses.create():
            pass
        return SimpleNamespace(content=self.result_content)


_ChatGPTClient.__module__ = "llmai.chatgpt.client"


def _call(client):
    return provider_generate_call(
        client,
        provider_id="codex",
        kwargs={},
        idempotency_key=None,
        outcome_scope="authored",
    )


@pytest.mark.parametrize(
    ("event_type", "expected_code", "recovery_allowed"),
    [
        ("response.failed", "public_provider_response_failed", True),
        ("response.incomplete", "public_provider_response_incomplete", False),
    ],
)
def test_terminal_response_events_have_distinct_durable_outcomes(
    event_type, expected_code, recovery_allowed
):
    client = _ChatGPTClient([SimpleNamespace(type=event_type)])

    with pytest.raises(ProviderOutcomeError) as raised:
        _call(client)()

    assert raised.value.code == expected_code
    assert raised.value.retryable is False
    assert raised.value.recovery_allowed is recovery_allowed


def test_empty_output_and_classified_overload_are_not_terminal_event_aliases():
    empty = _ChatGPTClient([], result_content="  ")
    with pytest.raises(ProviderOutcomeError) as raised_empty:
        _call(empty)()
    assert raised_empty.value.code == "public_provider_empty_output"
    assert raised_empty.value.retryable is False
    assert raised_empty.value.recovery_allowed is True

    class ProviderUnavailable(RuntimeError):
        status_code = 503

    broken = _ChatGPTClient([], error=ProviderUnavailable("private detail"))
    with pytest.raises(ProviderOutcomeError) as raised_bridge:
        _call(broken)()
    assert raised_bridge.value.code == "public_provider_overload"
    assert raised_bridge.value.retryable is False
    assert raised_bridge.value.recovery_allowed is True
    assert "private detail" not in str(raised_bridge.value)


def test_structured_deck_response_with_no_findings_is_output():
    from llmai.shared import JSONSchemaResponse

    client = _ChatGPTClient([], result_content={"slides": []})
    call = provider_generate_call(
        client,
        provider_id="codex",
        kwargs={"response_format": JSONSchemaResponse(
            name="vision_advisory", json_schema={"type": "object"}, strict=False,
        )},
        idempotency_key=None,
        outcome_scope="authored_deck",
    )
    assert call().content == {"slides": []}


class _PlainClient:
    """A transport with no idempotency support but a cloneable raw client."""

    def __init__(self, error=None):
        self._client = _RawClient([])
        self.error = error

    def generate(self, **_kwargs):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content="ok")


_PlainClient.__module__ = "llmai.other.client"


class _UncloneableClient:
    def __init__(self):
        self._client = SimpleNamespace()

    def generate(self, **_kwargs):
        return SimpleNamespace(content="ok")


_UncloneableClient.__module__ = "llmai.other.client"


def test_every_cloneable_transport_gets_the_bounded_call_timeout():
    """The timeout is applied even when nothing else needs adapting."""

    adapted = _ChatGPTClient([])
    _call(adapted)()
    assert adapted._client.options is None  # the shared client is never mutated
    assert adapted._client.cloned_options == {
        "max_retries": 0,
        "timeout": PROVIDER_CALL_TIMEOUT_SECONDS,
    }

    plain = _PlainClient()
    generate = provider_generate_call(
        plain,
        provider_id="anthropic",
        kwargs={},
        idempotency_key=None,
        outcome_scope=None,
    )
    generate()
    # Cloned purely for the timeout: no retry/idempotency adaptation applies.
    assert plain._client.options is None
    assert plain._client.cloned_options == {
        "timeout": PROVIDER_CALL_TIMEOUT_SECONDS
    }


def test_call_timeout_is_a_distinct_recoverable_outcome():
    class ReadTimeout(RuntimeError):
        pass

    timed_out = _ChatGPTClient([], error=ReadTimeout("private endpoint detail"))
    with pytest.raises(ProviderOutcomeError) as raised:
        _call(timed_out)()

    assert raised.value.code == "public_provider_call_timeout"
    assert raised.value.code != "public_provider_bridge_exception"
    assert raised.value.retryable is False
    assert raised.value.recovery_allowed is True
    assert "private endpoint detail" not in str(raised.value)

    # A stuck call is an overload signal: the job halves its wave concurrency
    # and enters a cooldown before it may recover.
    assert is_authored_provider_overload(raised.value) is True


def test_rate_limited_bridge_call_is_an_overload_signal():
    """A 429 from the transport must slow the wave, not just retry the call."""

    class RateLimited(RuntimeError):
        status_code = 429

    throttled = _ChatGPTClient([], error=RateLimited("private quota detail"))
    with pytest.raises(ProviderOutcomeError) as raised:
        _call(throttled)()

    assert raised.value.code == "public_provider_overload"
    assert raised.value.recovery_allowed is True
    assert "private quota detail" not in str(raised.value)
    assert is_authored_provider_overload(raised.value) is True
    assert is_authored_provider_overload(RateLimited("throttled")) is True


def test_generic_bridge_error_is_not_transient_wholesale():
    broken = _ChatGPTClient([], error=RuntimeError("private bridge detail"))

    with pytest.raises(ProviderOutcomeError) as raised:
        _call(broken)()

    assert raised.value.code == "public_provider_bridge_exception"
    assert raised.value.recovery_allowed is False
    assert is_authored_provider_overload(raised.value) is False


class _OpenAIClient:
    def __init__(self, outcomes):
        self._client = _RawClient([])
        self.outcomes = list(outcomes)
        self.call_count = [0]

    def generate(self, **_kwargs):
        self.call_count[0] += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(content=outcome)


_OpenAIClient.__module__ = "llmai.openai.client"


class _FakeRetryReservation:
    """A retry reservation the tests can watch.

    Mirrors the real lease: ``claim`` is made by the bounded call as its first
    physical request starts and is idempotent; ``__call__`` is the replay
    authority a confirmed overload spends.
    """

    def __init__(self) -> None:
        self.claimed = False
        self.ordinals: list[int] = []

    @property
    def pending(self) -> bool:
        return True

    def claim(self) -> None:
        self.claimed = True

    def __call__(self, physical_ordinal: int) -> None:
        self.ordinals.append(physical_ordinal)


def test_idempotent_overload_retry_reserves_and_stays_inside_deadline():
    class Unavailable(RuntimeError):
        status_code = 503

    client = _OpenAIClient([Unavailable("private"), "ok"])
    now = [10.0]
    reserver = _FakeRetryReservation()

    def sleep(seconds: float) -> None:
        now[0] += seconds

    call = provider_generate_call(
        client,
        provider_id="openai",
        kwargs={},
        idempotency_key="stable-key",
        outcome_scope="authored",
        timeout_seconds=60,
        retry_reserver=reserver,
        clock=lambda: now[0],
        sleep=sleep,
    )

    # Nothing is claimed until the physical call starts.
    assert reserver.claimed is False
    assert call().content == "ok"
    assert client.call_count[0] == 2
    assert reserver.claimed is True
    assert reserver.ordinals == [2]
    assert client._client.cloned_options["timeout"] == 30.0
    assert client._client.cloned_options["max_retries"] == 0
    assert call.receipt == {
        "schemaVersion": 1,
        "physicalCallCount": 2,
        "overloadRetryCount": 1,
        "cooldownWaitSeconds": 30.0,
        "deadlineExhausted": False,
        "quotaPauseResetAt": None,
        "idempotencyCapable": True,
        "replayUnavailable": None,
    }


def test_overload_retry_does_not_start_past_the_deadline():
    class Unavailable(RuntimeError):
        status_code = 504

    client = _OpenAIClient([Unavailable("private"), "must not run"])
    reserver = _FakeRetryReservation()
    call = provider_generate_call(
        client,
        provider_id="openai",
        kwargs={},
        idempotency_key="stable-key",
        outcome_scope="authored",
        timeout_seconds=10,
        retry_reserver=reserver,
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ProviderOutcomeError, match="public_provider_overload"):
        call()

    assert client.call_count[0] == 1
    assert reserver.ordinals == []
    assert call.receipt["deadlineExhausted"] is True


def test_unsupported_transport_never_replays_inside_one_admission():
    class Unavailable(RuntimeError):
        status_code = 502

    client = _PlainClient(error=Unavailable("private"))
    call = provider_generate_call(
        client,
        provider_id="anthropic",
        kwargs={},
        idempotency_key=None,
        outcome_scope="authored",
        sleep=lambda _seconds: pytest.fail("unsupported transport retried"),
    )

    with pytest.raises(ProviderOutcomeError, match="public_provider_overload"):
        call()

    assert client._client.cloned_options["max_retries"] == 0
    assert call.receipt["physicalCallCount"] == 1


class _StreamingChatGPTClient:
    """The shape the streaming author path calls: ``generate`` yields events."""

    def __init__(self, events):
        self._client = _RawClient(events)
        self.events = list(events)

    def generate(self, **_kwargs):
        return (event for event in self.events)


_StreamingChatGPTClient.__module__ = "llmai.chatgpt.client"


def _reservation(slide_index: int = 0) -> ProviderAttemptReservation:
    return ProviderAttemptReservation(
        job_id=uuid.uuid4(),
        slide_index=slide_index,
        attempt_token="attempt-a",
        ordinal=1,
        fencing_token=1,
    )


def _seed_reservation(slide_index: int = 0) -> None:
    """Publish one reservation into the running task, as the ledger does."""

    publish_provider_attempt_reservation(_reservation(slide_index))


def test_a_pending_reservation_does_not_impose_the_authored_document_guard():
    """A stream has no ``.content``: the empty-output guard must not read it.

    The streaming author path omits ``outcome_scope`` on purpose.  When a
    pending attempt reservation could turn the guard back on, every streamed
    slide died as ``public_provider_empty_output`` before its first event.
    """

    events = [SimpleNamespace(type="response.output_text.delta")]
    client = _StreamingChatGPTClient(events)

    async def scenario():
        _seed_reservation()
        call = provider_generate_call(
            client,
            provider_id="codex",
            kwargs={"stream": True},
            idempotency_key=None,
        )
        return list(call())

    assert asyncio.run(scenario()) == events


def test_a_generic_call_is_untouched_by_a_pending_authored_reservation():
    """An unrelated structured call keeps its own error and the author's budget."""

    class Unavailable(RuntimeError):
        status_code = 503

    client = _PlainClient(error=Unavailable("private"))

    async def scenario():
        _seed_reservation()
        call = provider_generate_call(
            client,
            provider_id="anthropic",
            kwargs={},
            idempotency_key=None,
        )
        with pytest.raises(Unavailable):
            call()
        return dict(call.receipt), current_provider_attempt_reservations()

    receipt, still_pending = asyncio.run(scenario())

    # No authored outcome mapping, no authored retry pinning, and the slide's
    # reservation was never spent by a call that does not belong to it.
    assert receipt["physicalCallCount"] == 1
    assert receipt["idempotencyCapable"] is False
    assert client._client.cloned_options == {"timeout": PROVIDER_CALL_TIMEOUT_SECONDS}
    assert len(still_pending) == 1


def _authored_openai_call(*, kwargs: dict | None = None, scope: str = "authored"):
    return provider_generate_call(
        _OpenAIClient(["ok"]),
        provider_id="openai",
        kwargs={} if kwargs is None else kwargs,
        idempotency_key="stable-key",
        outcome_scope=scope,
    )


def test_an_authored_call_consumes_its_reservation_exactly_once():
    """The second call in the same task must not inherit the first's replay.

    The reservation is spent by the call that *runs*, so this drives the first
    call rather than only constructing it: construction alone must leave the
    reservation where it is (see the dropped-wrapper test below).
    """

    async def scenario():
        _seed_reservation()
        first = _authored_openai_call()
        first()
        after_first = current_provider_attempt_reservations()
        second = _authored_openai_call()
        second()
        return after_first, dict(first.receipt), dict(second.receipt)

    after_first, first_receipt, second_receipt = asyncio.run(scenario())

    assert after_first == ()
    assert first_receipt["replayUnavailable"] is None
    assert second_receipt["idempotencyCapable"] is True
    assert second_receipt["replayUnavailable"] == "attempt_reservation_unavailable"


def test_a_dropped_authored_wrapper_leaves_its_reservation_behind():
    """Item 1: a wrapper that is built and never run must spend nothing.

    Construction, an executor hand-off that is cancelled before the queued work
    starts, and a branch that discards the wrapper are all the same shape: the
    physical call never happened, so the ledger must not believe a replayable
    one did.
    """

    async def scenario():
        _seed_reservation()
        dropped = _authored_openai_call()
        after_construction = current_provider_attempt_reservations()
        ran = _authored_openai_call()
        ran()
        return (
            after_construction,
            current_provider_attempt_reservations(),
            dict(dropped.receipt),
            dict(ran.receipt),
        )

    after_construction, after_run, dropped_receipt, ran_receipt = asyncio.run(scenario())

    # Nothing was spent until a call actually started...
    assert len(after_construction) == 1
    assert dropped_receipt["physicalCallCount"] == 0
    # ...and the call that did start is the one that got the replay authority.
    assert after_run == ()
    assert ran_receipt["physicalCallCount"] == 1
    assert ran_receipt["replayUnavailable"] is None


def test_a_failing_authored_call_still_spends_its_reservation():
    """A call that starts has spent it, whatever the provider then did."""

    class Rejected(RuntimeError):
        status_code = 400

    async def scenario():
        _seed_reservation()
        call = provider_generate_call(
            _OpenAIClient([Rejected("private")]),
            provider_id="openai",
            kwargs={},
            idempotency_key="stable-key",
            outcome_scope="authored",
        )
        with pytest.raises(ProviderOutcomeError):
            call()
        return current_provider_attempt_reservations(), dict(call.receipt)

    remaining, receipt = asyncio.run(scenario())

    assert remaining == ()
    assert receipt["physicalCallCount"] == 1


def test_a_deck_scope_authored_call_never_leases_a_slide_reservation():
    """Item 1, third call site: a deck-level call owns no slide attempt.

    ``run_vision_advisory`` runs from a deck-level stage, where a slide's
    reservation may still be pending in the same task.  The scope it declares
    is what keeps it from spending one, enforced at the single constructor
    rather than by a guard the caller could omit.
    """

    async def scenario():
        _seed_reservation()
        call = _authored_openai_call(scope="authored_deck")
        call()
        return current_provider_attempt_reservations(), dict(call.receipt)

    remaining, receipt = asyncio.run(scenario())

    assert len(remaining) == 1
    assert receipt["physicalCallCount"] == 1
    assert receipt["replayUnavailable"] == "deck_scope_no_slide_attempt"


def test_an_unsupported_outcome_scope_is_still_refused():
    """The scope vocabulary is closed: a typo is a failure, not a silent scope."""

    with pytest.raises(ValueError):
        _authored_openai_call(scope="authored-deck")


def test_a_streamed_call_states_that_its_response_cannot_be_replayed():
    """Item 2: ``generate(stream=True)`` returns a generator, so the request is
    made at iteration -- outside the bounded wrapper, where no overload can
    reach the replay branch on any transport, openai API key included.  The
    receipt has to say that rather than leave ``replayUnavailable`` at ``None``,
    which reads as "this call never needed a replay"."""

    async def scenario():
        _seed_reservation()
        call = _authored_openai_call(kwargs={"stream": True})
        return dict(call.receipt), current_provider_attempt_reservations()

    receipt, remaining = asyncio.run(scenario())

    assert receipt["replayUnavailable"] == "stream_response_not_replayable"
    # A wrapper that can never replay must not hold a reservation either.
    assert len(remaining) == 1


def test_a_child_task_never_publishes_into_its_parents_reservations():
    """Per-task isolation: worker tasks author many slides under one parent."""

    async def scenario():
        _seed_reservation(slide_index=0)

        async def child() -> tuple[int, int]:
            _seed_reservation(slide_index=7)
            return len(current_provider_attempt_reservations())

        child_count = await asyncio.create_task(child())
        parent = current_provider_attempt_reservations()
        return child_count, tuple(item.slide_index for item in parent)

    child_count, parent_slides = asyncio.run(scenario())

    assert child_count == 1
    assert parent_slides == (0,)


# --- R2: the wrapper that spends the reservation must match the call that runs -----


_AUTHOR_CALL_VALID_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>제목</h1></body></html>"
)


def _patch_author_call_environment(monkeypatch, *, streaming: bool, client) -> None:
    """Install a fake provider client and pin the streaming switch for one call."""

    async def no_rate_limit() -> None:
        return None

    monkeypatch.setattr(author_slide_mod, "get_client", lambda **_kwargs: client)
    monkeypatch.setattr(author_slide_mod, "get_llm_config", lambda: {})
    monkeypatch.setattr(author_slide_mod, "get_model", lambda: "fake-model")
    monkeypatch.setattr(author_slide_mod, "get_generate_kwargs", lambda **kwargs: kwargs)
    monkeypatch.setattr(author_slide_mod, "is_codex_selected", lambda: False)
    monkeypatch.setattr(author_slide_mod, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_slide_mod, "_wait_for_author_rate_slot", no_rate_limit)
    monkeypatch.setattr(
        author_slide_mod, "is_authored_author_streaming_enabled", lambda: streaming
    )


def test_streaming_author_call_leaves_the_reservation_intact(monkeypatch):
    """R2: building the non-stream wrapper unconditionally spent the reservation
    on a call ``stream_author_call`` then ran instead. The wrapper must only be
    built on the path that actually uses it, so a pending reservation survives
    the streaming call untouched."""

    class _RefusingClient:
        def generate(self, **_kwargs):
            raise AssertionError("the non-streaming client must not be called")

    async def fake_stream_author_call(_client, _kwargs, **_extra):
        return SimpleNamespace(content=_AUTHOR_CALL_VALID_HTML)

    _patch_author_call_environment(monkeypatch, streaming=True, client=_RefusingClient())
    monkeypatch.setattr(author_slide_mod, "stream_author_call", fake_stream_author_call)

    async def scenario():
        _seed_reservation()
        html = await author_slide_mod.author_slide_html(
            "content", "DESIGN", Brand(topic="주제"), "COVER", 0, 1,
        )
        return html, current_provider_attempt_reservations()

    html, remaining = asyncio.run(scenario())

    assert "제목" in html
    assert len(remaining) == 1


def test_non_streaming_author_call_consumes_the_reservation_exactly_once(monkeypatch):
    calls: list[dict] = []

    class _FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=_AUTHOR_CALL_VALID_HTML)

    _patch_author_call_environment(monkeypatch, streaming=False, client=_FakeClient())

    async def scenario():
        _seed_reservation()
        html = await author_slide_mod.author_slide_html(
            "content", "DESIGN", Brand(topic="주제"), "COVER", 0, 1,
        )
        return html, current_provider_attempt_reservations()

    html, remaining = asyncio.run(scenario())

    assert "제목" in html
    assert len(calls) == 1
    assert remaining == ()


class _ReservationAccountingCall:
    """A bounded call that records whether the caller ever invoked it.

    ``__getattr__`` forwards ``terminal_outcome`` / ``receipt`` / ``abort`` so
    the wrapper is indistinguishable from the real one to ``author_slide``.
    """

    def __init__(self, call, record: dict) -> None:
        self._call = call
        self._record = record

    def __call__(self, *args, **kwargs):
        self._record["invoked"] += 1
        return self._call(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._call, name)


def _record_reservation_accounting(monkeypatch) -> list[dict]:
    """Record, for every bounded call ``author_slide`` builds, how many
    reservations its construction spent and how many times it was invoked."""

    records: list[dict] = []
    real_provider_generate_call = author_slide_mod.provider_generate_call

    def recording(*args, **kwargs):
        before = len(current_provider_attempt_reservations())
        call = real_provider_generate_call(*args, **kwargs)
        record = {
            "outcome_scope": kwargs.get("outcome_scope"),
            "consumed": before - len(current_provider_attempt_reservations()),
            "invoked": 0,
        }
        records.append(record)
        return _ReservationAccountingCall(call, record)

    monkeypatch.setattr(author_slide_mod, "provider_generate_call", recording)
    return records


@pytest.mark.parametrize("streaming", [False, True])
def test_a_reservation_author_slide_spends_is_spent_by_a_call_that_runs(
    monkeypatch, streaming
):
    """The invariant, stated once for every branch ``author_slide_html`` has.

    A reservation authorizes exactly one physical replay of the call that
    spends it, so spending one for a wrapper the function then drops is an
    accounting leak: the ledger believes a replayable physical call was made
    and nothing can replay it.  Construction therefore spends nothing at all,
    and the number of reservations gone from the ledger is exactly the number
    of authored wrappers that were invoked.  This asserts the property rather
    than the branch -- ``streaming`` is only the control that exercises both
    legs -- so a future early return between construction and invocation stays
    harmless even though it is a branch this test does not know about.
    """

    class _FakeClient:
        def generate(self, **_kwargs):
            return SimpleNamespace(content=_AUTHOR_CALL_VALID_HTML)

    async def fake_stream_author_call(_client, _kwargs, **_extra):
        return SimpleNamespace(content=_AUTHOR_CALL_VALID_HTML)

    _patch_author_call_environment(
        monkeypatch, streaming=streaming, client=_FakeClient()
    )
    monkeypatch.setattr(
        author_slide_mod, "stream_author_call", fake_stream_author_call
    )
    records = _record_reservation_accounting(monkeypatch)

    async def scenario():
        _seed_reservation()
        html = await author_slide_mod.author_slide_html(
            "content", "DESIGN", Brand(topic="주제"), "COVER", 0, 1,
        )
        return html, current_provider_attempt_reservations()

    html, remaining = asyncio.run(scenario())

    assert "제목" in html
    # The invariant: building a wrapper spends nothing at all...
    assert [record for record in records if record["consumed"]] == []
    # ...and exactly the wrappers that ran spent the seeded reservation.
    spent = 1 - len(remaining)
    invoked = [
        record
        for record in records
        if record["outcome_scope"] == "authored" and record["invoked"]
    ]
    assert spent == len(invoked)
    # Control: the two legs must differ, or the parametrization proves nothing.
    if streaming:
        assert spent == 0 and len(remaining) == 1
    else:
        assert spent == 1 and remaining == ()


def test_a_wrapper_author_slide_builds_and_drops_spends_nothing(monkeypatch):
    """Sensitivity control for the assertion above: it must not be vacuous.

    Constructing an authored wrapper and never invoking it is exactly the R2
    defect shape (an authored bounded call built on one branch and discarded on
    another), and it is what a cancelled executor hand-off leaves behind.
    Reproduced through the construction seam itself, so the proof needs no edit
    to the code under test: under the previous construction-time consumption
    this left the ledger empty.
    """

    class _UncalledClient:
        def generate(self, **_kwargs):  # pragma: no cover -- never invoked
            raise AssertionError("the dropped wrapper must not reach the client")

    records = _record_reservation_accounting(monkeypatch)

    async def scenario():
        _seed_reservation()
        author_slide_mod.provider_generate_call(
            _UncalledClient(),
            provider_id="openai",
            kwargs={},
            idempotency_key=None,
            outcome_scope="authored",
        )
        return current_provider_attempt_reservations()

    remaining = asyncio.run(scenario())

    assert len(remaining) == 1
    assert [record for record in records if record["invoked"]] == []
    assert [record for record in records if record["consumed"]] == []


def test_codex_transport_records_that_no_replay_was_available():
    """An unconfirmed transport still gets its one call, and says why only one."""

    class Unavailable(RuntimeError):
        status_code = 503

    client = _ChatGPTClient([], error=Unavailable("private"))
    call = _call(client)

    with pytest.raises(ProviderOutcomeError, match="public_provider_overload"):
        call()

    assert call.receipt["physicalCallCount"] == 1
    assert call.receipt["idempotencyCapable"] is False
    assert call.receipt["replayUnavailable"] == "transport_idempotency_unconfirmed"


def test_a_status_coded_rejection_is_not_overload_for_a_number_in_its_message():
    """``400 ... must be <= 500`` must not buy an identical, certain failure."""

    class InvalidRequest(RuntimeError):
        status_code = 400

    client = _ChatGPTClient(
        [], error=InvalidRequest("max_output_tokens must be <= 500")
    )

    with pytest.raises(ProviderOutcomeError) as raised:
        _call(client)()

    assert raised.value.code == "public_provider_bridge_exception"
    assert raised.value.recovery_allowed is False
    assert is_provider_overload(raised.value) is False


def test_a_message_only_overload_still_classifies_from_its_text():
    """Transports that expose no status keep the free-text fallback."""

    client = _ChatGPTClient([], error=RuntimeError("Rate limit reached for gpt-5.2"))

    with pytest.raises(ProviderOutcomeError) as raised:
        _call(client)()

    assert raised.value.code == "public_provider_overload"
    assert is_provider_overload(raised.value) is True


# --- R3: a non-overload status code still yields to an unambiguous message ---------


def test_a_non_429_status_with_a_usage_limit_message_is_still_overload():
    """A 400 that carries llmai's ``usage_limit_reached`` text must not be lost
    just because it does not use the 429 status code."""

    class UsageLimit(RuntimeError):
        status_code = 400

    assert is_provider_overload(UsageLimit("usage limit reached")) is True


def test_a_non_5xx_status_with_a_timeout_message_is_still_overload():
    """A 408 with an explicit timeout message must classify as overload even
    though 408 is not in the structured overload status set."""

    class RequestTimeout(RuntimeError):
        status_code = 408

    assert is_provider_overload(RequestTimeout("Request timed out.")) is True


def test_a_non_overload_status_with_an_unrelated_number_stays_non_overload():
    """The motivating case: a bare number in the message must not, on its own,
    make a 400 read as overload once the bare HTTP-number pattern is
    suppressed for a structured status."""

    class InvalidRequest(RuntimeError):
        status_code = 400

    assert is_provider_overload(InvalidRequest("max_output_tokens must be <= 500")) is False


def test_run22_usage_limit_shape_is_a_durable_deck_pause(monkeypatch):
    monkeypatch.setattr("utils.provider_overload.time.time", lambda: 1789437216)
    fixture_path = Path(__file__).parents[1] / "fixtures" / "authored_provider_usage_limit_run22.txt"
    fixture = fixture_path.read_text(encoding="utf-8")

    class UsageLimit(RuntimeError):
        status_code = 429

    client = _ChatGPTClient([], error=UsageLimit(fixture))
    call = _call(client)
    with pytest.raises(ProviderQuotaPause) as raised:
        call()

    pause = raised.value
    assert isinstance(pause, WorkspaceOperationDeferred)
    assert pause.reset_at == 1789437217
    assert pause.detail == {
        "provider_quota_reset_at": 1789437217,
        "provider_quota_resume_semantics": "coordinator_requeues_after_reset",
    }
    assert call.receipt["quotaPauseResetAt"] == 1789437217
    assert author_stage_failure(21, pause, provider_attempt_count=3) is pause


def test_quota_pause_reset_is_persisted_on_the_operation(tmp_path):
    async def scenario() -> dict[str, object]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pause.db'}")
        tables = [
            User.__table__,
            PresentationModel.__table__,
            WorkspaceConversation.__table__,
            WorkspaceOperation.__table__,
            WorkspaceOperationEvent.__table__,
            WorkspaceOutbox.__table__,
        ]
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(sync, tables=tables)
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = uuid.uuid4()
        presentation_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        attempt_token = "quota-pause-attempt"
        now = get_current_utc_datetime()
        async with maker() as session, session.begin():
            session.add(User(id=owner_id, username="quota-pause", hashed_password="test"))
            session.add(
                PresentationModel(
                    id=presentation_id,
                    owner_id=owner_id,
                    content="quota pause",
                    n_slides=1,
                    language="en",
                    mode="authored",
                    theme={},
                )
            )
            session.add(
                WorkspaceConversation(
                    id=conversation_id,
                    owner_id=owner_id,
                    owner_scope=str(owner_id),
                    presentation_id=presentation_id,
                )
            )
            session.add(
                WorkspaceOperation(
                    id=operation_id,
                    owner_id=owner_id,
                    owner_scope=str(owner_id),
                    conversation_id=conversation_id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="design.apply",
                    state="running",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={},
                    attempt_number=1,
                    attempt_token=attempt_token,
                    fencing_token=4,
                    lease_expires_at=now + timedelta(minutes=5),
                )
            )
        reset_at = int(now.timestamp()) + 120
        pause = ProviderQuotaPause(reset_at=reset_at, observed_at=int(now.timestamp()))
        claim = WorkspaceOperationClaim(
            operation_id=operation_id,
            owner_id=owner_id,
            owner_scope=str(owner_id),
            conversation_id=conversation_id,
            presentation_id=presentation_id,
            kind="design.apply",
            request_payload={},
            expected_revision=None,
            attempt_number=1,
            attempt_token=attempt_token,
            fencing_token=4,
        )
        assert await defer_workspace_operation_claim(claim, pause, session_factory=maker)
        async with maker() as session:
            persisted = await session.get(WorkspaceOperation, operation_id)
            result = {
                "state": persisted.state,
                "attempt_number": persisted.attempt_number,
                "reset_at": persisted.progress["provider_quota_reset_at"],
                "resume_semantics": persisted.progress[
                    "provider_quota_resume_semantics"
                ],
                "reason": persisted.progress["wait_reason"],
                "next_attempt_at": persisted.next_attempt_at,
                "expected_reset_at": reset_at,
            }
        await engine.dispose()
        return result

    persisted = asyncio.run(scenario())
    assert persisted["state"] == "queued"
    assert persisted["attempt_number"] == 0
    assert persisted["reset_at"] == persisted["expected_reset_at"]
    assert persisted["resume_semantics"] == "coordinator_requeues_after_reset"
    assert persisted["reason"] == "public_provider_usage_limit_paused"
    assert persisted["next_attempt_at"] is not None


def test_quota_pause_sanitizer_keeps_only_the_coded_resume_receipt():
    assert sanitize_workspace_event_payload(
        {
            "provider_quota_reset_at": 1_789_437_217,
            "provider_quota_resume_semantics": "coordinator_requeues_after_reset",
            "provider_error_body": "must-not-persist",
        }
    ) == {
        "provider_quota_reset_at": 1_789_437_217,
        "provider_quota_resume_semantics": "coordinator_requeues_after_reset",
    }


def test_unenforceable_timeout_is_reported_rather_than_assumed(caplog):
    with caplog.at_level("WARNING"):
        provider_generate_call(
            _UncloneableClient(),
            provider_id="uncloneable-provider",
            kwargs={},
            idempotency_key=None,
            outcome_scope=None,
        )()

    assert "provider_call_timeout_not_enforced" in caplog.text
    assert "uncloneable-provider" in caplog.text


def test_provider_outcome_gets_only_the_explicit_single_recovery(monkeypatch):
    brand = Brand(
        topic="주제",
        language="Korean",
        primary="#2563EB",
        fonts="Noto Sans KR",
    )
    calls = []

    async def succeeds_after_empty(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderOutcomeError(
                "public_provider_empty_output", recovery_allowed=True
            )
        return _VALID_HTML

    monkeypatch.setattr(author_deck_mod, "author_slide_html", succeeds_after_empty)
    result = asyncio.run(
        author_deck_mod._author_one_resilient(
            "slide", "design", brand, "COVER", 0, 1, raise_on_failure=True
        )
    )

    assert result == _VALID_HTML
    assert [call["receipt_attempt_ordinal"] for call in calls] == [1, 2]
    assert all(call["allow_provider_fallback"] is False for call in calls)

    calls.clear()

    async def always_failed(*_args, **kwargs):
        calls.append(kwargs)
        raise ProviderOutcomeError(
            "public_provider_response_failed", recovery_allowed=True
        )

    monkeypatch.setattr(author_deck_mod, "author_slide_html", always_failed)
    with pytest.raises(ProviderOutcomeError) as terminal:
        asyncio.run(
            author_deck_mod._author_one_resilient(
                "slide", "design", brand, "COVER", 0, 1,
                raise_on_failure=True,
            )
        )
    assert terminal.value.code == "public_provider_response_failed"
    assert terminal.value.retryable is False
    assert [call["receipt_attempt_ordinal"] for call in calls] == [1, 2]
