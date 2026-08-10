from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.conversation import (
    AgentRunItem,
    ConversationMessageCreateRequest,
    PresentationRevision,
)
from services.agent_runtime import (
    BoundedCheckpointEventSink,
    OperationArtifactScope,
    RunContext,
    RuntimeEventFenceLost,
    TransientRunEventBroadcaster,
)
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.service import PresentationChatService
from services.conversation_event_contract import (
    ConversationEventType,
    EventDurability,
    build_conversation_event,
    recover_assistant_checkpoints,
)
from services.workspace_chat_operation_handler import _default_chat_service_factory


class _PresentationSession:
    def __init__(self, generation_mode: str) -> None:
        self.presentation = SimpleNamespace(generation_mode=generation_mode)

    async def get(self, _model, _identifier):
        return self.presentation


@pytest.mark.parametrize(
    ("stored_mode", "spoofed_mode", "required_tool", "forbidden_tool"),
    (
        ("standard", "smart", "getPresentationOutline", "getSmartPresentationContext"),
        ("smart", "standard", "getSmartPresentationContext", "getPresentationOutline"),
    ),
)
def test_real_default_factory_uses_db_capability_and_tool_registry(
    monkeypatch,
    tmp_path: Path,
    stored_mode: str,
    spoofed_mode: str,
    required_tool: str,
    forbidden_tool: str,
):
    async def scenario() -> None:
        presentation_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        session = _PresentationSession(stored_mode)
        monkeypatch.setattr(
            PresentationChatMemoryLayer,
            "retrieve_context",
            AsyncMock(return_value=""),
        )
        service = _default_chat_service_factory(
            sql_session=session,
            presentation_id=presentation_id,
            conversation_id=conversation_id,
            artifact_directory=str(tmp_path),
        )
        assert isinstance(service, PresentationChatService)
        service._conversation_store.retrieve_semantic_context = AsyncMock(
            return_value=""
        )
        context = RunContext(
            run_id=uuid.uuid4(),
            presentation_id=presentation_id,
            conversation_id=conversation_id,
            user_message="inspect",
            expected_revision=3,
            generation_mode=spoofed_mode,  # type: ignore[arg-type]
            artifact_scope=OperationArtifactScope(tmp_path),
        )

        await service._prepare_turn_context(
            "inspect", canonical_history=(), run_context=context
        )

        assert service._memory is not None
        assert service._memory.presentation_type == stored_mode
        assert service._memory._images_directory() == str(tmp_path)
        assert service._tools is not None
        names = {tool.name for tool in service._tools.get_tool_definitions()}
        assert required_tool in names
        assert forbidden_tool not in names

    asyncio.run(scenario())


def test_event_sink_splits_live_delta_from_durable_checkpoint():
    async def scenario() -> None:
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        message_id = uuid.uuid4()
        durable: list[tuple[str, dict[str, object]]] = []
        transient = TransientRunEventBroadcaster()

        async def writer(event_type: str, payload: dict[str, object]) -> bool:
            durable.append((event_type, payload))
            return True

        sink = BoundedCheckpointEventSink(
            conversation_id=conversation_id,
            run_id=run_id,
            message_id=message_id,
            durable_writer=writer,
            transient=transient,
            checkpoint_every_chars=5,
        )
        pending = asyncio.create_task(
            transient.next_event(
                conversation_id=conversation_id,
                after_checkpoint="9",
            )
        )
        await asyncio.sleep(0)
        await sink.assistant_delta("hello")
        live = await asyncio.wait_for(pending, timeout=1)
        await sink.finalize("hello")

        assert live.type == ConversationEventType.ASSISTANT_DELTA
        assert live.durability == EventDurability.TRANSIENT
        assert live.cursor is None
        assert live.data == {"delta": "hello"}
        checkpoints = [
            payload
            for event_type, payload in durable
            if event_type == ConversationEventType.ASSISTANT_CHECKPOINT.value
        ]
        assert checkpoints[-1]["assistant_text"] == "hello"
        assert checkpoints[-1]["message_id"] == str(message_id)
        assert all("provider" not in str(payload).lower() for payload in checkpoints)

    asyncio.run(scenario())


def test_transient_conversation_events_fan_out_without_replay_buffering():
    async def scenario() -> None:
        broadcaster = TransientRunEventBroadcaster()
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        event = build_conversation_event(
            event_id="live:1",
            event_type=ConversationEventType.ASSISTANT_DELTA,
            conversation_id=conversation_id,
            run_id=run_id,
            message_id=uuid.uuid4(),
            data={"delta": "live"},
        )
        first = asyncio.create_task(
            broadcaster.next_event(
                conversation_id=conversation_id, after_checkpoint="3"
            )
        )
        second = asyncio.create_task(
            broadcaster.next_event(
                conversation_id=conversation_id, after_checkpoint="3"
            )
        )
        await asyncio.sleep(0)
        await broadcaster.publish(event)
        assert await asyncio.wait_for(first, timeout=1) is event
        assert await asyncio.wait_for(second, timeout=1) is event

        # No active waiter means a transient token is dropped, not replayed to
        # a later SSE connection after its durable checkpoint recovery.
        await broadcaster.publish(event)
        stale_waiter = asyncio.create_task(
            broadcaster.next_event(
                conversation_id=conversation_id, after_checkpoint="4"
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(stale_waiter, timeout=0.01)
        assert conversation_id not in broadcaster._conversation_subscribers

    asyncio.run(scenario())


def test_checkpointing_continues_at_capacity_and_final_state_is_canonical():
    async def scenario() -> None:
        durable: list[tuple[str, dict[str, object]]] = []

        async def writer(event_type: str, payload: dict[str, object]) -> bool:
            durable.append((event_type, payload))
            return True

        sink = BoundedCheckpointEventSink(
            conversation_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            durable_writer=writer,
            transient=TransientRunEventBroadcaster(),
            checkpoint_every_chars=3,
            max_checkpoint_chars=5,
        )
        await sink.assistant_delta("abc")
        await sink.assistant_delta("def")
        await sink.assistant_delta("ghi")
        await sink.finalize("FINAL")

        checkpoints = [
            payload["assistant_text"]
            for event_type, payload in durable
            if event_type == ConversationEventType.ASSISTANT_CHECKPOINT.value
        ]
        assert checkpoints == ["abc", "bcdef", "efghi", "FINAL"]

    asyncio.run(scenario())


def test_checkpoint_recovery_ignores_floor_and_keeps_latest_cursor_per_message():
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    message_id = uuid.uuid4()

    def checkpoint(cursor: int, content: str):
        return build_conversation_event(
            event_id=f"checkpoint:{cursor}",
            event_type=ConversationEventType.ASSISTANT_CHECKPOINT,
            conversation_id=conversation_id,
            run_id=run_id,
            message_id=message_id,
            cursor=cursor,
            checkpoint_cursor=cursor,
            data={"content": content},
        )

    # Replay sources are normally ordered by SQL, but recovery remains correct
    # for duplicated/out-of-order transport pages and never reapplies its floor.
    high_watermark, recovered = recover_assistant_checkpoints(
        (checkpoint(12, "latest"), checkpoint(9, "before-floor"), checkpoint(11, "older")),
        after_cursor="10",
    )
    assert high_watermark == "12"
    assert recovered == {message_id: "latest"}


def test_event_sink_stops_when_the_durable_attempt_fence_is_lost():
    async def scenario() -> None:
        async def rejected_writer(
            _event_type: str, _payload: dict[str, object]
        ) -> bool:
            return False

        sink = BoundedCheckpointEventSink(
            conversation_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            durable_writer=rejected_writer,
            transient=TransientRunEventBroadcaster(),
        )
        with pytest.raises(RuntimeEventFenceLost, match="fence_lost"):
            await sink.status("running")

    asyncio.run(scenario())


_REQUIRED_EVENT_DATA = {
    ConversationEventType.MESSAGE_CREATED: {"role": "user", "content": "hello"},
    ConversationEventType.RUN_QUEUED: {"status": "queued"},
    ConversationEventType.RUN_STARTED: {"status": "running"},
    ConversationEventType.RUN_STATUS: {"status": "running"},
    ConversationEventType.ASSISTANT_DELTA: {"delta": "hello"},
    ConversationEventType.ASSISTANT_CHECKPOINT: {"content": "hello"},
    ConversationEventType.TOOL_STARTED: {"tool": "saveSlide", "summary": "Started."},
    ConversationEventType.TOOL_COMPLETED: {"tool": "saveSlide", "summary": "Done."},
    ConversationEventType.APPROVAL_REQUIRED: {"approval_id": "approval-1"},
    ConversationEventType.PRESENTATION_PUBLISHED: {"revision": 8},
    ConversationEventType.RUN_COMPLETED: {"status": "succeeded"},
    ConversationEventType.RUN_FAILED: {
        "status": "failed",
        "error_code": "presentation_edit_failed",
    },
}


@pytest.mark.parametrize("event_type", tuple(ConversationEventType))
def test_public_event_contract_enforces_every_required_data_field(event_type):
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    message_id = uuid.uuid4()
    kwargs = {
        "event_id": f"event:{event_type.value}",
        "event_type": event_type,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "message_id": message_id,
        "presentation_id": uuid.uuid4(),
        "cursor": None
        if event_type == ConversationEventType.ASSISTANT_DELTA
        else "1",
    }
    required = _REQUIRED_EVENT_DATA[event_type]
    build_conversation_event(data=required, **kwargs)
    for key in required:
        with pytest.raises(ValidationError, match=f"data.{key}"):
            build_conversation_event(
                data={name: value for name, value in required.items() if name != key},
                **kwargs,
            )


@pytest.mark.parametrize(
    "private_key",
    (
        "rawProviderResponse",
        "chain-of-thought",
        "systemPrompt",
        "authorization_header",
        "apiKey",
        "client-secret",
        "credentials",
        "stackTrace",
    ),
)
def test_public_event_contract_rejects_private_key_spelling_variants(private_key):
    with pytest.raises(ValidationError, match="server-private"):
        build_conversation_event(
            event_id="private",
            event_type=ConversationEventType.RUN_STATUS,
            conversation_id=uuid.uuid4(),
            cursor="1",
            data={"status": "running", "nested": {private_key: "secret"}},
        )


def test_conversation_models_enforce_state_revision_and_typed_command_rules():
    request = ConversationMessageCreateRequest(
        content="Export this deck",
        idempotency_key="export-1",
        command_type="export.pdf",
        operation_context={"quality": "print"},
    )
    assert request.command_type == "export.pdf"
    assert request.operation_context == {"quality": "print"}
    assert (
        ConversationMessageCreateRequest(
            content="Answer a question", idempotency_key="default-1"
        ).command_type
        == "presentation.edit"
    )
    with pytest.raises(ValidationError, match="unsupported conversation command_type"):
        ConversationMessageCreateRequest(
            content="Unsafe callback",
            idempotency_key="unsafe-1",
            command_type="callback.invoke",
        )
    with pytest.raises(ValidationError, match="must not precede"):
        PresentationRevision(
            presentation_id=uuid.uuid4(),
            source_revision=4,
            result_revision=3,
            published=False,
        )
    with pytest.raises(ValidationError):
        AgentRunItem(
            id=uuid.uuid4(),
            position=1,
            run_type="presentation.edit",
            status="mystery",
            version=0,
            revision=PresentationRevision(
                presentation_id=uuid.uuid4(), source_revision=0
            ),
        )


def test_production_runtime_forwards_stream_chunks_to_event_sink(
    monkeypatch, tmp_path: Path
):
    async def scenario() -> None:
        class Tools:
            def get_tool_definitions(self):
                return []

        class Sink:
            def __init__(self):
                self.deltas: list[str] = []

            async def status(self, status):
                return None

            async def assistant_delta(self, delta):
                self.deltas.append(delta)

            async def tool_started(self, **_kwargs):
                return None

            async def tool_completed(self, **_kwargs):
                return None

            async def finalize(self, assistant_output):
                return None

        async def events(_client, **_kwargs):
            yield SimpleNamespace(type="content", chunk="Hel")
            yield SimpleNamespace(type="content", chunk="lo")
            yield SimpleNamespace(
                type="completion",
                tool_calls=[],
                content=["Hello"],
                messages=[],
            )

        service = PresentationChatService.__new__(PresentationChatService)
        service._tools = Tools()
        monkeypatch.setattr(
            "services.chat.service.stream_generate_events", events
        )
        monkeypatch.setattr(
            "services.chat.service.get_client", lambda **_kwargs: object()
        )
        monkeypatch.setattr("services.chat.service.get_model", lambda: "model")
        monkeypatch.setattr(
            "services.chat.service.get_llm_config", lambda: None
        )
        monkeypatch.setattr(
            "services.chat.service.build_chat_llm_tools", lambda _tools: []
        )
        monkeypatch.setattr(
            "services.chat.service.get_generate_kwargs", lambda **kwargs: kwargs
        )
        context = RunContext(
            run_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            user_message="hello",
            expected_revision=1,
            generation_mode="standard",
            artifact_scope=OperationArtifactScope(tmp_path),
        )
        sink = Sink()

        result = await service._run_agent_with_tools(
            [], context=context, event_sink=sink
        )

        assert result[0] == "Hello"
        assert sink.deltas == ["Hel", "lo"]

    asyncio.run(scenario())


def test_production_runtime_drops_thinking_and_sanitizes_provider_failures(
    monkeypatch, tmp_path: Path
):
    async def scenario() -> None:
        class Tools:
            def get_tool_definitions(self):
                return []

        class Sink:
            async def status(self, _status):
                return None

            async def assistant_delta(self, _delta):
                pytest.fail("provider failure must not publish a delta")

            async def tool_started(self, **_kwargs):
                return None

            async def tool_completed(self, **_kwargs):
                return None

            async def finalize(self, _assistant_output):
                return None

        class RawProviderFailure(RuntimeError):
            status_code = 503

        async def events(_client, **_kwargs):
            yield SimpleNamespace(type="thinking", chunk="private reasoning")
            raise RawProviderFailure("rawProviderResponse=secret-api-key")

        service = PresentationChatService.__new__(PresentationChatService)
        service._tools = Tools()
        monkeypatch.setattr("services.chat.service.stream_generate_events", events)
        monkeypatch.setattr("services.chat.service.get_client", lambda **_kwargs: object())
        monkeypatch.setattr("services.chat.service.get_model", lambda: "model")
        monkeypatch.setattr("services.chat.service.get_llm_config", lambda: None)
        monkeypatch.setattr("services.chat.service.build_chat_llm_tools", lambda _tools: [])
        monkeypatch.setattr(
            "services.chat.service.get_generate_kwargs", lambda **kwargs: kwargs
        )
        context = RunContext(
            run_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            user_message="hello",
            expected_revision=1,
            generation_mode="standard",
            artifact_scope=OperationArtifactScope(tmp_path),
        )

        with pytest.raises(HTTPException) as caught:
            await service._run_agent_with_tools(
                [], context=context, event_sink=Sink()
            )
        assert caught.value.status_code == 503
        assert caught.value.detail == {"code": "presentation_edit_failed"}
        assert "secret" not in str(caught.value).lower()

    asyncio.run(scenario())
