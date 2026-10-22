"""Per-phase transport timeouts, the opt-in wall cap and the page-retry predicate."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import httpx
import openai
import pytest

from utils import provider_idempotency as idempotency_mod
from utils.provider_idempotency import (
    PROVIDER_CALL_TIMEOUT_SECONDS,
    PROVIDER_CONNECT_TIMEOUT_SECONDS,
    PROVIDER_POOL_TIMEOUT_SECONDS,
    PROVIDER_WRITE_TIMEOUT_SECONDS,
    provider_generate_call,
)
from utils.provider_overload import (
    BoundedProviderCall,
    ProviderOutcomeError,
    ProviderQuotaPause,
    is_page_recoverable_overload,
    safe_provider_diagnostics,
)
from utils.provider_stream_guard import GuardedResponsesResource


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Stream:
    """A Responses event stream that advances the clock as it yields."""

    def __init__(self, timed_events, clock: _Clock) -> None:
        self._events = iter(timed_events)
        self._clock = clock
        self.closed = False
        self.yielded: list[str] = []

    def __iter__(self):
        return self

    def __next__(self):
        at, event_type = next(self._events)
        self._clock.now = at
        self.yielded.append(event_type)
        return SimpleNamespace(type=event_type)

    def close(self) -> None:
        self.closed = True


class _Responses:
    def __init__(self, stream_factory) -> None:
        self._stream_factory = stream_factory
        self.streams: list[_Stream] = []

    def create(self, **_kwargs):
        stream = self._stream_factory()
        self.streams.append(stream)
        return stream


class _RawClient:
    def __init__(self, responses: _Responses | None = None) -> None:
        self.responses = responses or _Responses(lambda: _Stream([], _Clock()))
        self.cloned_options: dict | None = None

    def with_options(self, **options):
        clone = _RawClient(self.responses)
        clone.options = options
        self.cloned_options = options
        return clone


class _ChatGPTClient:
    def __init__(self, raw: _RawClient | None = None, *, error: BaseException | None = None) -> None:
        self._client = raw or _RawClient()
        self.error = error

    def generate(self, **_kwargs):
        if self.error is not None:
            raise self.error
        for _event in self._client.responses.create():
            pass
        return SimpleNamespace(content="ok")


_ChatGPTClient.__module__ = "llmai.chatgpt.client"


class _PlainClient:
    """A non-authored transport: the SDK keeps its own retry policy."""

    def __init__(self) -> None:
        self._client = _RawClient()

    def generate(self, **_kwargs):
        return SimpleNamespace(content="ok")


_PlainClient.__module__ = "llmai.other.client"


def _authored_call(client, **kwargs):
    return provider_generate_call(
        client,
        provider_id="codex",
        kwargs={},
        idempotency_key=None,
        outcome_scope="authored",
        **kwargs,
    )


# --- E-1: per-phase transport timeouts -------------------------------------


@pytest.mark.parametrize(
    ("read", "expected"),
    [
        (360.0, (30.0, 360.0, 60.0, 30.0)),
        (45.0, (30.0, 45.0, 45.0, 30.0)),
        (20.0, (20.0, 20.0, 20.0, 20.0)),
    ],
)
def test_phase_timeout_never_exceeds_the_read_timeout(read, expected):
    timeout = idempotency_mod._phase_timeout(read)
    assert isinstance(timeout, httpx.Timeout)
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == expected


def test_phase_constants():
    assert PROVIDER_CONNECT_TIMEOUT_SECONDS == 30.0
    assert PROVIDER_WRITE_TIMEOUT_SECONDS == 60.0
    assert PROVIDER_POOL_TIMEOUT_SECONDS == 30.0


def test_clone_keeps_the_read_semantics_of_the_single_float():
    raw = _RawClient()
    idempotency_mod._clone_raw_client(raw, {"max_retries": 0}, provider_id="codex")
    assert raw.cloned_options == {
        "max_retries": 0,
        "timeout": httpx.Timeout(connect=30.0, read=PROVIDER_CALL_TIMEOUT_SECONDS, write=60.0, pool=30.0),
    }
    idempotency_mod._clone_raw_client(raw, {}, provider_id="codex", timeout_seconds=500.0)
    assert raw.cloned_options["timeout"].read == 500.0
    idempotency_mod._clone_raw_client(raw, {}, provider_id="codex", timeout_seconds=0.0)
    assert raw.cloned_options["timeout"] == httpx.Timeout(0.05)


def test_clone_falls_back_to_the_float_for_a_transport_without_phase_timeouts():
    class FloatOnly(_RawClient):
        def with_options(self, **options):
            if isinstance(options.get("timeout"), httpx.Timeout):
                raise TypeError("timeout must be a number")
            return super().with_options(**options)

    raw = FloatOnly()
    idempotency_mod._clone_raw_client(raw, {"max_retries": 0}, provider_id="float-only")
    assert raw.cloned_options == {"max_retries": 0, "timeout": PROVIDER_CALL_TIMEOUT_SECONDS}


def test_the_remaining_deadline_is_the_read_timeout_of_each_physical_call():
    clock = _Clock(100.0)
    client = _ChatGPTClient()
    _authored_call(client, clock=clock)()
    assert client._client.cloned_options["timeout"] == httpx.Timeout(
        connect=30.0, read=PROVIDER_CALL_TIMEOUT_SECONDS, write=60.0, pool=30.0,
    )


# --- timeout phase diagnostics ----------------------------------------------


def _sdk_timeout(cause: BaseException) -> openai.APITimeoutError:
    request = httpx.Request("POST", "https://provider.invalid/v1/responses")
    try:
        try:
            raise cause
        except BaseException as inner:
            raise openai.APITimeoutError(request=request) from inner
    except openai.APITimeoutError as error:
        return error


@pytest.mark.parametrize(
    ("cause", "phase"),
    [
        (httpx.ConnectTimeout("private"), "connect"),
        (httpx.ReadTimeout("private"), "read"),
        (httpx.WriteTimeout("private"), "write"),
        (httpx.PoolTimeout("private"), "pool"),
    ],
)
def test_transport_timeout_names_its_phase(cause, phase):
    with pytest.raises(ProviderOutcomeError) as raised:
        _authored_call(_ChatGPTClient(error=_sdk_timeout(cause)))()
    error = raised.value
    assert error.code == "public_provider_call_timeout"
    assert error.recovery_allowed is True and error.overload is True
    assert error.diagnostics["failureKind"] == "timeout"
    assert error.diagnostics["timeoutPhase"] == phase
    assert error.diagnostics["causeExceptionType"] == type(cause).__name__
    assert is_page_recoverable_overload(error) is True


def test_unknown_timeout_phase_is_omitted():
    with pytest.raises(ProviderOutcomeError) as raised:
        _authored_call(_ChatGPTClient(error=TimeoutError("private")))()
    assert raised.value.code == "public_provider_call_timeout"
    assert raised.value.diagnostics["failureKind"] == "timeout"
    assert "timeoutPhase" not in raised.value.diagnostics


# --- E-2: opt-in wall cap ---------------------------------------------------


def _streaming_client(timed_events, clock: _Clock) -> _ChatGPTClient:
    return _ChatGPTClient(_RawClient(_Responses(lambda: _Stream(timed_events, clock))))


def test_wall_cap_closes_the_stream_and_raises_a_replayable_timeout():
    clock = _Clock(0.0)
    client = _streaming_client(
        [
            (1.0, "response.created"),
            (5.0, "response.output_text.delta"),
            (11.0, "response.output_text.delta"),
            (12.0, "response.output_text.delta"),
            (13.0, "response.completed"),
        ],
        clock,
    )
    with pytest.raises(ProviderOutcomeError) as raised:
        _authored_call(client, clock=clock, wall_timeout_seconds=10.0)()
    error = raised.value
    assert error.code == "public_provider_call_timeout"
    assert error.recovery_allowed is True and error.overload is True
    assert {
        key: error.diagnostics.get(key)
        for key in ("failureKind", "timeoutPhase", "streamStage")
    } == {
        "failureKind": "timeout",
        "timeoutPhase": "wall",
        "streamStage": "output_delta",
    }
    stream =client._client.responses.streams[-1]
    assert stream.closed is True
    # Nothing after the event that crossed the deadline was consumed.
    assert stream.yielded == [
        "response.created",
        "response.output_text.delta",
        "response.output_text.delta",
    ]
    assert is_page_recoverable_overload(error) is True


def test_wall_cap_reports_the_stage_the_stream_reached():
    clock = _Clock(0.0)
    client = _streaming_client(
        [(3.0, "response.in_progress"), (20.0, "response.created")], clock,
    )
    with pytest.raises(ProviderOutcomeError) as raised:
        _authored_call(client, clock=clock, wall_timeout_seconds=10.0)()
    assert raised.value.diagnostics["streamStage"] == "response_created"

    clock = _Clock(0.0)
    client = _streaming_client([(20.0, "response.in_progress")], clock)
    with pytest.raises(ProviderOutcomeError) as raised:
        _authored_call(client, clock=clock, wall_timeout_seconds=10.0)()
    assert raised.value.diagnostics["streamStage"] == "stream_opened"


def test_wall_cap_is_measured_from_the_start_of_the_physical_call():
    clock = _Clock(1_000.0)
    client = _streaming_client(
        [(1_004.0, "response.created"), (1_009.0, "response.completed")], clock,
    )
    assert _authored_call(client, clock=clock, wall_timeout_seconds=10.0)().content == "ok"


def test_wall_cap_does_not_discard_a_terminal_event():
    clock = _Clock(0.0)
    client = _streaming_client(
        [(1.0, "response.created"), (50.0, "response.completed")], clock,
    )
    assert _authored_call(client, clock=clock, wall_timeout_seconds=10.0)().content == "ok"
    assert client._client.responses.streams[-1].closed is False


def test_without_a_wall_cap_a_long_stream_runs_to_completion():
    clock = _Clock(0.0)
    client = _streaming_client(
        [
            (1.0, "response.created"),
            (2_000.0, "response.output_text.delta"),
            (2_206.0, "response.completed"),
        ],
        clock,
    )
    assert _authored_call(client, clock=clock)().content == "ok"
    assert client._client.responses.streams[-1].closed is False


def test_guarded_resource_checks_the_wall_on_every_event():
    clock = _Clock(0.0)
    stream = _Stream(
        [(1.0, "response.created"), (9.0, "response.output_text.delta"), (10.0, "response.output_text.delta")],
        clock,
    )
    resource = GuardedResponsesResource(
        SimpleNamespace(create=lambda **_kwargs: stream),
        abort_event=threading.Event(),
        guard_outcomes=False,
        wall_timeout_seconds=10.0,
        clock=clock,
    )
    events = resource.create(stream=True)
    assert next(events).type == "response.created"
    assert next(events).type == "response.output_text.delta"
    with pytest.raises(ProviderOutcomeError) as raised:
        next(events)
    assert raised.value.diagnostics["timeoutPhase"] == "wall"
    assert stream.closed is True


@pytest.mark.parametrize("timeout_seconds", [None, 500.0])
def test_wall_cap_leaves_phases_deadline_and_max_retries_unchanged(timeout_seconds):
    without = _ChatGPTClient()
    with_wall = _ChatGPTClient()
    call_without = _authored_call(without, timeout_seconds=timeout_seconds)
    call_with = _authored_call(with_wall, timeout_seconds=timeout_seconds, wall_timeout_seconds=893.0)
    call_without()
    call_with()
    assert with_wall._client.cloned_options == without._client.cloned_options
    assert without._client.cloned_options["max_retries"] == 0
    expected_read = PROVIDER_CALL_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    assert without._client.cloned_options["timeout"].read == expected_read
    assert call_with._deadline_seconds == call_without._deadline_seconds == expected_read
    assert call_with.receipt == call_without.receipt


def test_wall_cap_does_not_pin_sdk_retries_for_an_unbounded_caller():
    plain = _PlainClient()
    provider_generate_call(
        plain,
        provider_id="other",
        kwargs={},
        idempotency_key=None,
        wall_timeout_seconds=893.0,
    )()
    # ``timeout_seconds`` would pin ``max_retries=0``; the wall cap must not.
    assert "max_retries" not in plain._client.cloned_options


@pytest.mark.parametrize(("wall", "installed"), [(None, None), (893.0, 893.0), (0.0, 0.05)])
def test_the_guarded_resource_of_every_physical_call_carries_the_wall(wall, installed):
    class Recording(_ChatGPTClient):
        seen: list = []

        def generate(self, **kwargs):
            # ``self`` is the adapted copy: its client is the guarded wrapper.
            Recording.seen.append(self._client.responses._wall_timeout_seconds)
            return super().generate(**kwargs)

    Recording.seen = []
    _authored_call(Recording(), wall_timeout_seconds=wall)()
    assert Recording.seen == [installed]


# --- predicate ----------------------------------------------------------------


def test_page_recoverable_overload_truth_table():
    assert is_page_recoverable_overload(
        ProviderOutcomeError("public_provider_overload", recovery_allowed=True, overload=True)
    ) is True
    assert is_page_recoverable_overload(
        ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=True, overload=True)
    ) is True
    assert is_page_recoverable_overload(
        ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=False, overload=True)
    ) is False
    assert is_page_recoverable_overload(
        ProviderOutcomeError("public_provider_response_failed", recovery_allowed=True)
    ) is False
    assert is_page_recoverable_overload(ProviderQuotaPause(reset_at=2_000_000_000)) is False
    assert is_page_recoverable_overload(TimeoutError("timeout")) is False
    assert is_page_recoverable_overload(RuntimeError("public_provider_overload")) is False


def test_deadline_exhausted_timeout_is_not_page_recoverable():
    clock = _Clock(0.0)
    call = BoundedProviderCall(
        lambda _remaining: SimpleNamespace(content="unused"),
        threading.Event(),
        clock=clock,
        deadline_seconds=0.0,
    )
    with pytest.raises(ProviderOutcomeError) as raised:
        call()
    assert raised.value.code == "public_provider_call_timeout"
    assert raised.value.recovery_allowed is False
    assert is_page_recoverable_overload(raised.value) is False


# --- diagnostics allowlist ----------------------------------------------------


def test_allowlist_keeps_timeout_phase_and_stream_timing():
    safe = safe_provider_diagnostics(diagnostics={
        "timeoutPhase": "wall",
        "firstEventMs": 1_250,
        "maxEventGapMs": 0,
        "eventCount": 4_321,
    })
    assert safe == {
        "timeoutPhase": "wall",
        "firstEventMs": 1_250,
        "maxEventGapMs": 0,
        "eventCount": 4_321,
    }
    for phase in ("connect", "read", "write", "pool", "wall"):
        assert safe_provider_diagnostics(diagnostics={"timeoutPhase": phase}) == {"timeoutPhase": phase}


@pytest.mark.parametrize(
    "diagnostics",
    [
        {"timeoutPhase": "dns"},
        {"timeoutPhase": "WALL"},
        {"firstEventMs": -1},
        {"maxEventGapMs": 1.5},
        {"eventCount": True},
        {"eventCount": "12"},
        {"firstEventMs": 10**13},
        {"unknownTiming": 5},
    ],
)
def test_allowlist_drops_unknown_values(diagnostics):
    assert safe_provider_diagnostics(diagnostics=diagnostics) == {}


def test_outcome_error_keeps_the_new_fields():
    error = ProviderOutcomeError(
        "public_provider_call_timeout",
        recovery_allowed=True,
        overload=True,
        diagnostics={"timeoutPhase": "connect", "eventCount": 0, "private": "x"},
    )
    assert error.diagnostics == {"timeoutPhase": "connect", "eventCount": 0}
