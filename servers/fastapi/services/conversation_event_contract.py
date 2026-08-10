"""Canonical, transport-neutral Unified Conversation Runtime event contract.

The workspace SSE cursor remains the durable replay cursor.  A transport adapts
these envelopes to SSE (``id`` = ``cursor``) or a polling event page without
inventing a second event store.  Token deltas are deliberately transient; a
durable assistant checkpoint is the reconnect source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re
from collections.abc import Iterable
from typing import Any, Mapping
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1
_CANONICAL_CURSOR = re.compile(r"^(0|[1-9][0-9]*)$")


class ConversationEventType(StrEnum):
    MESSAGE_CREATED = "message.created"
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_STATUS = "run.status"
    ASSISTANT_DELTA = "assistant.delta"
    ASSISTANT_CHECKPOINT = "assistant.checkpoint"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUIRED = "approval.required"
    PRESENTATION_PUBLISHED = "presentation.published"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class EventDurability(StrEnum):
    DURABLE = "durable"
    TRANSIENT = "transient"


TRANSIENT_EVENT_TYPES = frozenset({ConversationEventType.ASSISTANT_DELTA})
DURABLE_EVENT_TYPES = frozenset(set(ConversationEventType) - TRANSIENT_EVENT_TYPES)

# These names are forbidden recursively, not merely at the envelope root.
# Provider payloads and hidden reasoning must remain server-private even when a
# tool summary is constructed by a third-party runtime adapter.
_PRIVATE_DATA_KEYS = frozenset(
    {
        "chainofthought",
        "cot",
        "rawproviderresponse",
        "providerresponse",
        "providerraw",
        "prompt",
        "systemprompt",
        "authorization",
        "authorizationheader",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "bearertoken",
        "clientsecret",
        "credential",
        "credentials",
        "password",
        "stacktrace",
        "traceback",
    }
)
_MAX_PUBLIC_ASSISTANT_TEXT_LENGTH = 100_000

_REQUIRED_EVENT_DATA: dict[ConversationEventType, frozenset[str]] = {
    ConversationEventType.MESSAGE_CREATED: frozenset({"role", "content"}),
    ConversationEventType.RUN_QUEUED: frozenset({"status"}),
    ConversationEventType.RUN_STARTED: frozenset({"status"}),
    ConversationEventType.RUN_STATUS: frozenset({"status"}),
    ConversationEventType.ASSISTANT_DELTA: frozenset({"delta"}),
    ConversationEventType.ASSISTANT_CHECKPOINT: frozenset({"content"}),
    ConversationEventType.TOOL_STARTED: frozenset({"tool", "summary"}),
    ConversationEventType.TOOL_COMPLETED: frozenset({"tool", "summary"}),
    ConversationEventType.APPROVAL_REQUIRED: frozenset({"approval_id"}),
    ConversationEventType.PRESENTATION_PUBLISHED: frozenset({"revision"}),
    ConversationEventType.RUN_COMPLETED: frozenset({"status"}),
    ConversationEventType.RUN_FAILED: frozenset({"status", "error_code"}),
}


def canonical_event_cursor(value: str | int | None) -> str | None:
    """Return a bounded, decimal replay cursor compatible with Workspace SSE."""

    if value is None:
        return None
    candidate = str(value)
    if not _CANONICAL_CURSOR.fullmatch(candidate):
        raise ValueError("cursor must be a canonical non-negative decimal string")
    return candidate


def _reject_private_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = "".join(
                character
                for character in str(key).strip().lower()
                if character.isalnum()
            )
            if normalized_key in _PRIVATE_DATA_KEYS:
                raise ValueError("event data contains a server-private field")
            _reject_private_data(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_private_data(child)
    return value


class UnifiedConversationEvent(BaseModel):
    """One public event emitted by the canonical EventSink interface.

    Callers persist a durable envelope and allocate its cursor in the same
    transaction as the domain transition.  Transient delta events may be sent
    directly to active subscribers but never become the replay record.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION, frozen=True)
    id: str = Field(min_length=1, max_length=255)
    type: ConversationEventType
    durability: EventDurability
    conversation_id: uuid.UUID
    run_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    presentation_id: uuid.UUID | None = None
    cursor: str | None = None
    checkpoint_cursor: str | None = None
    occurred_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def require_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError("unsupported unified conversation event schema")
        return value

    @field_validator("cursor", "checkpoint_cursor", mode="before")
    @classmethod
    def validate_cursor(cls, value: str | int | None) -> str | None:
        return canonical_event_cursor(value)

    @field_validator("data")
    @classmethod
    def safe_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_private_data(value)
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_durability(self) -> "UnifiedConversationEvent":
        expected = (
            EventDurability.TRANSIENT
            if self.type in TRANSIENT_EVENT_TYPES
            else EventDurability.DURABLE
        )
        if self.durability != expected:
            raise ValueError(f"{self.type} must be {expected}")
        if self.durability == EventDurability.DURABLE and self.cursor is None:
            raise ValueError("durable events require a replay cursor")
        if self.durability == EventDurability.TRANSIENT and self.cursor is not None:
            raise ValueError("transient events must not allocate a replay cursor")

        missing = _REQUIRED_EVENT_DATA[self.type].difference(self.data)
        if missing:
            fields = ", ".join(f"data.{key}" for key in sorted(missing))
            raise ValueError(f"{self.type} requires {fields}")
        if self.type == ConversationEventType.ASSISTANT_DELTA:
            if not isinstance(self.data.get("delta"), str):
                raise ValueError("assistant.delta requires string data.delta")
            if len(self.data["delta"]) > _MAX_PUBLIC_ASSISTANT_TEXT_LENGTH:
                raise ValueError("assistant.delta exceeds public text limit")
            if self.run_id is None or self.message_id is None:
                raise ValueError("assistant.delta requires run_id and message_id")
        if self.type == ConversationEventType.ASSISTANT_CHECKPOINT:
            if not isinstance(self.data.get("content"), str):
                raise ValueError("assistant.checkpoint requires string data.content")
            if len(self.data["content"]) > _MAX_PUBLIC_ASSISTANT_TEXT_LENGTH:
                raise ValueError("assistant.checkpoint exceeds public text limit")
            if self.run_id is None or self.message_id is None:
                raise ValueError("assistant.checkpoint requires run_id and message_id")
        if self.type == ConversationEventType.PRESENTATION_PUBLISHED:
            if self.presentation_id is None or not isinstance(self.data.get("revision"), int):
                raise ValueError("presentation.published requires presentation_id and integer data.revision")
        return self


def build_conversation_event(
    *,
    event_id: str,
    event_type: ConversationEventType | str,
    conversation_id: uuid.UUID | str,
    data: Mapping[str, Any],
    cursor: str | int | None = None,
    checkpoint_cursor: str | int | None = None,
    run_id: uuid.UUID | str | None = None,
    message_id: uuid.UUID | str | None = None,
    presentation_id: uuid.UUID | str | None = None,
    occurred_at: datetime | None = None,
) -> UnifiedConversationEvent:
    """Factory expected by canonical EventSink implementations and adapters."""

    normalized_type = ConversationEventType(event_type)
    return UnifiedConversationEvent(
        id=event_id,
        type=normalized_type,
        durability=(
            EventDurability.TRANSIENT
            if normalized_type in TRANSIENT_EVENT_TYPES
            else EventDurability.DURABLE
        ),
        conversation_id=conversation_id,
        run_id=run_id,
        message_id=message_id,
        presentation_id=presentation_id,
        cursor=cursor,
        checkpoint_cursor=checkpoint_cursor,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        data=dict(data),
    )


def recover_assistant_checkpoints(
    events: Iterable[UnifiedConversationEvent],
    *,
    after_cursor: str | int | None = None,
) -> tuple[str | None, dict[uuid.UUID, str]]:
    """Apply a durable replay page and return its cursor plus assistant content.

    Event consumers use this before accepting live deltas after an SSE reconnect.
    Only checkpoint events update rendered assistant text, so missing transient
    packets cannot produce a corrupt or duplicate response.
    """

    high_watermark = canonical_event_cursor(after_cursor)
    replay_floor = int(high_watermark) if high_watermark is not None else None
    checkpoints: dict[uuid.UUID, str] = {}
    checkpoint_cursors: dict[uuid.UUID, int] = {}
    for event in events:
        if event.durability != EventDurability.DURABLE:
            continue
        assert event.cursor is not None
        event_cursor = int(event.cursor)
        if replay_floor is not None and event_cursor <= replay_floor:
            continue
        if high_watermark is None or event_cursor > int(high_watermark):
            high_watermark = event.cursor
        if event.type == ConversationEventType.ASSISTANT_CHECKPOINT:
            assert event.message_id is not None
            if event_cursor > checkpoint_cursors.get(event.message_id, -1):
                checkpoint_cursors[event.message_id] = event_cursor
                checkpoints[event.message_id] = str(event.data["content"])
    return high_watermark, checkpoints
