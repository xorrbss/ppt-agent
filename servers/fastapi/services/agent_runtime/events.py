from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from services.conversation_event_contract import (
    ConversationEventType,
    UnifiedConversationEvent,
    build_conversation_event,
)


DurableEventWriter = Callable[[str, dict[str, object]], Awaitable[bool]]
_MAX_TRANSIENT_DELTA_CHARS = 100_000


class RuntimeEventFenceLost(RuntimeError):
    """The durable writer rejected an event from a stale run attempt."""


class EventSink(Protocol):
    async def status(self, status: str) -> None: ...

    async def assistant_delta(self, delta: str) -> None: ...

    async def tool_started(self, *, tool: str, summary: str) -> None: ...

    async def tool_completed(
        self, *, tool: str, status: str, summary: str
    ) -> None: ...

    async def finalize(self, assistant_output: str) -> None: ...


class NullEventSink:
    async def status(self, status: str) -> None:
        return None

    async def assistant_delta(self, delta: str) -> None:
        return None

    async def tool_started(self, *, tool: str, summary: str) -> None:
        return None

    async def tool_completed(
        self, *, tool: str, status: str, summary: str
    ) -> None:
        return None

    async def finalize(self, assistant_output: str) -> None:
        return None


class CompositeEventSink:
    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = sinks

    async def status(self, status: str) -> None:
        for sink in self._sinks:
            await sink.status(status)

    async def assistant_delta(self, delta: str) -> None:
        for sink in self._sinks:
            await sink.assistant_delta(delta)

    async def tool_started(self, *, tool: str, summary: str) -> None:
        for sink in self._sinks:
            await sink.tool_started(tool=tool, summary=summary)

    async def tool_completed(
        self, *, tool: str, status: str, summary: str
    ) -> None:
        for sink in self._sinks:
            await sink.tool_completed(tool=tool, status=status, summary=summary)

    async def finalize(self, assistant_output: str) -> None:
        for sink in self._sinks:
            await sink.finalize(assistant_output)


class TransientRunEventBroadcaster:
    """Bounded live fanout using the canonical transport-neutral envelope."""

    def __init__(self, *, queue_size: int = 128) -> None:
        self._queue_size = max(1, queue_size)
        self._subscribers: dict[
            uuid.UUID, set[asyncio.Queue[UnifiedConversationEvent]]
        ] = {}
        self._conversation_subscribers: dict[
            uuid.UUID, set[asyncio.Queue[UnifiedConversationEvent]]
        ] = {}

    def subscribe(
        self, run_id: uuid.UUID
    ) -> asyncio.Queue[UnifiedConversationEvent]:
        queue: asyncio.Queue[UnifiedConversationEvent] = asyncio.Queue(
            self._queue_size
        )
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(
        self,
        run_id: uuid.UUID,
        queue: asyncio.Queue[UnifiedConversationEvent],
    ) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    async def publish(self, event: UnifiedConversationEvent) -> None:
        if event.type != ConversationEventType.ASSISTANT_DELTA:
            raise ValueError("transient_broadcaster_accepts_deltas_only")
        assert event.run_id is not None
        for conversation_queue in tuple(
            self._conversation_subscribers.get(event.conversation_id, ())
        ):
            if conversation_queue.full():
                try:
                    conversation_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            conversation_queue.put_nowait(event)
        for queue in tuple(self._subscribers.get(event.run_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    async def next_event(
        self,
        *,
        conversation_id: uuid.UUID,
        after_checkpoint: str | None,
    ) -> UnifiedConversationEvent | None:
        """Implement the canonical SSE live-source composition protocol.

        ``after_checkpoint`` is deliberately not treated as a live sequence:
        transient deltas never allocate or advance durable cursors. Reconnect
        first replays the durable checkpoint, then accepts whatever delta is
        currently live.
        """

        del after_checkpoint
        # A call represents one active SSE waiter. Do not retain a conversation
        # queue after it disconnects: transient deltas published without an
        # active subscriber must never become an accidental replay buffer.
        # Per-waiter queues also preserve fanout when multiple clients watch the
        # same conversation instead of load-balancing tokens between them.
        queue: asyncio.Queue[UnifiedConversationEvent] = asyncio.Queue(
            self._queue_size
        )
        subscribers = self._conversation_subscribers.setdefault(
            conversation_id, set()
        )
        subscribers.add(queue)
        try:
            return await queue.get()
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._conversation_subscribers.pop(conversation_id, None)


TRANSIENT_RUN_EVENTS = TransientRunEventBroadcaster()


class BoundedCheckpointEventSink:
    """Split token deltas from fenced, durable reconnect checkpoints."""

    def __init__(
        self,
        *,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        message_id: uuid.UUID,
        durable_writer: DurableEventWriter,
        transient: TransientRunEventBroadcaster = TRANSIENT_RUN_EVENTS,
        checkpoint_every_chars: int = 512,
        max_checkpoint_chars: int = 100_000,
    ) -> None:
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._message_id = message_id
        self._durable_writer = durable_writer
        self._transient = transient
        self._checkpoint_every = max(1, checkpoint_every_chars)
        self._max_chars = max(1, min(max_checkpoint_chars, 100_000))
        self._assistant_text = ""
        self._uncheckpointed_chars = 0
        self._last_checkpoint_text: str | None = None
        self._sequence = 0

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    async def _write_durable(
        self, event_type: ConversationEventType, payload: dict[str, object]
    ) -> None:
        # False is not an advisory loss: the fenced operation attempt no longer
        # owns the run. Stop provider/tool work before it can produce more
        # provisional mutations or public events under a stale lease.
        if not await self._durable_writer(event_type.value, payload):
            raise RuntimeEventFenceLost("agent_runtime_event_fence_lost")

    async def status(self, status: str) -> None:
        await self._write_durable(
            ConversationEventType.RUN_STATUS,
            {"status": status[:128]},
        )

    async def assistant_delta(self, delta: str) -> None:
        if not delta:
            return
        for offset in range(0, len(delta), _MAX_TRANSIENT_DELTA_CHARS):
            public_delta = delta[offset : offset + _MAX_TRANSIENT_DELTA_CHARS]
            sequence = self._next_sequence()
            self._assistant_text = (
                self._assistant_text + public_delta
            )[-self._max_chars :]
            self._uncheckpointed_chars += len(public_delta)
            await self._transient.publish(
                build_conversation_event(
                    event_id=f"live:{self._run_id}:{sequence}",
                    event_type=ConversationEventType.ASSISTANT_DELTA,
                    conversation_id=self._conversation_id,
                    run_id=self._run_id,
                    message_id=self._message_id,
                    data={"delta": public_delta},
                )
            )
            if self._uncheckpointed_chars >= self._checkpoint_every:
                await self._checkpoint(status="streaming", sequence=sequence)

    async def _checkpoint(
        self, *, status: str, sequence: int, force: bool = False
    ) -> None:
        if self._assistant_text == self._last_checkpoint_text:
            return
        if not force and not self._assistant_text:
            return
        await self._write_durable(
            ConversationEventType.ASSISTANT_CHECKPOINT,
            {
                "message_id": str(self._message_id),
                "assistant_text": self._assistant_text,
                "checkpoint_sequence": sequence,
                "status": status,
            },
        )
        self._last_checkpoint_text = self._assistant_text
        self._uncheckpointed_chars = 0

    async def tool_started(self, *, tool: str, summary: str) -> None:
        self._next_sequence()
        await self._write_durable(
            ConversationEventType.TOOL_STARTED,
            {
                "tool_name": tool[:128],
                "status": "started",
                "tool_summary": summary[:500],
            },
        )

    async def tool_completed(
        self, *, tool: str, status: str, summary: str
    ) -> None:
        self._next_sequence()
        await self._write_durable(
            ConversationEventType.TOOL_COMPLETED,
            {
                "tool_name": tool[:128],
                "status": status[:32],
                "tool_summary": summary[:500],
            },
        )

    async def finalize(self, assistant_output: str) -> None:
        # The final answer is canonical. Tool-planning rounds may have streamed
        # provisional text that is intentionally absent from assistant_output;
        # the final durable checkpoint must replace that text for reconnect.
        normalized = assistant_output[-self._max_chars :]
        if normalized != self._assistant_text:
            self._assistant_text = normalized
            self._uncheckpointed_chars += len(normalized)
        await self._checkpoint(
            status="completed", sequence=self._next_sequence(), force=True
        )
