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
from utils.llm_calls.author_slide import Brand
from services.authored_author_stage_deferral import author_stage_failure
from services.authored_batch_policy import is_authored_provider_overload
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


def test_idempotent_overload_retry_reserves_and_stays_inside_deadline():
    class Unavailable(RuntimeError):
        status_code = 503

    client = _OpenAIClient([Unavailable("private"), "ok"])
    now = [10.0]
    reservations: list[int] = []

    def sleep(seconds: float) -> None:
        now[0] += seconds

    call = provider_generate_call(
        client,
        provider_id="openai",
        kwargs={},
        idempotency_key="stable-key",
        outcome_scope="authored",
        timeout_seconds=60,
        retry_reserver=reservations.append,
        clock=lambda: now[0],
        sleep=sleep,
    )

    assert call().content == "ok"
    assert client.call_count[0] == 2
    assert reservations == [2]
    assert client._client.cloned_options["timeout"] == 30.0
    assert client._client.cloned_options["max_retries"] == 0
    assert call.receipt == {
        "schemaVersion": 1,
        "physicalCallCount": 2,
        "overloadRetryCount": 1,
        "cooldownWaitSeconds": 30.0,
        "deadlineExhausted": False,
        "quotaPauseResetAt": None,
    }


def test_overload_retry_does_not_start_past_the_deadline():
    class Unavailable(RuntimeError):
        status_code = 504

    client = _OpenAIClient([Unavailable("private"), "must not run"])
    reservations: list[int] = []
    call = provider_generate_call(
        client,
        provider_id="openai",
        kwargs={},
        idempotency_key="stable-key",
        outcome_scope="authored",
        timeout_seconds=10,
        retry_reserver=reservations.append,
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ProviderOutcomeError, match="public_provider_overload"):
        call()

    assert client.call_count[0] == 1
    assert reservations == []
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


def test_run22_usage_limit_shape_is_a_durable_deck_pause():
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "authored_provider_usage_limit_run22.txt"
    ).read_text(encoding="utf-8")

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
