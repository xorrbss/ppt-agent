from datetime import datetime, timezone
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.key_value import KeyValueSqlModel

CHAT_CONVERSATION_KEY_PREFIX = "chat_conversation"
MAX_STORED_TURNS = 20


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Conversation message cannot be empty.")
        return normalized


class ConversationPayload(BaseModel):
    conversation_id: uuid.UUID
    presentation_id: uuid.UUID
    messages: list[ConversationMessage] = Field(default_factory=list)
    updated_at: datetime

    model_config = ConfigDict(extra="forbid", strict=True)


class ChatConversationStore:
    def __init__(self, sql_session: AsyncSession):
        self._sql_session = sql_session

    async def load_history(
        self,
        *,
        presentation_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> list[dict[str, str]]:
        payload = await self._get_payload(conversation_id)
        if not payload or payload.presentation_id != presentation_id:
            return []
        return [
            {"role": message.role, "content": message.content}
            for message in payload.messages
        ]

    async def append_turn(
        self,
        *,
        presentation_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message: str,
        assistant_message: str,
    ) -> None:
        payload = await self._get_payload(conversation_id)
        messages = list(payload.messages) if payload else []

        messages.append(ConversationMessage(role="user", content=user_message))
        messages.append(ConversationMessage(role="assistant", content=assistant_message))
        max_messages = MAX_STORED_TURNS * 2
        messages = messages[-max_messages:]

        next_payload = ConversationPayload(
            conversation_id=conversation_id,
            presentation_id=presentation_id,
            messages=messages,
            updated_at=datetime.now(timezone.utc),
        )

        row = await self._get_row(conversation_id)
        if row:
            row.value = next_payload.model_dump(mode="json")
            self._sql_session.add(row)
        else:
            self._sql_session.add(
                KeyValueSqlModel(
                    key=self._conversation_key(conversation_id),
                    value=next_payload.model_dump(mode="json"),
                )
            )

        await self._sql_session.commit()

    async def ensure_conversation_id(
        self,
        conversation_id: uuid.UUID | None,
    ) -> uuid.UUID:
        return conversation_id or uuid.uuid4()

    async def _get_payload(
        self, conversation_id: uuid.UUID
    ) -> ConversationPayload | None:
        row = await self._get_row(conversation_id)
        if not row or not isinstance(row.value, dict):
            return None

        raw_payload: dict[str, Any] = row.value
        try:
            return ConversationPayload.model_validate(raw_payload)
        except ValidationError:
            return self._coerce_payload(raw_payload)

    def _coerce_payload(self, payload: dict[str, Any]) -> ConversationPayload | None:
        try:
            conversation_id = uuid.UUID(str(payload.get("conversation_id")))
            presentation_id = uuid.UUID(str(payload.get("presentation_id")))
        except Exception:
            return None

        raw_messages = payload.get("messages")
        messages: list[ConversationMessage] = []
        if isinstance(raw_messages, list):
            for entry in raw_messages:
                if not isinstance(entry, dict):
                    continue
                try:
                    message = ConversationMessage.model_validate(entry)
                    messages.append(message)
                except ValidationError:
                    continue

        return ConversationPayload(
            conversation_id=conversation_id,
            presentation_id=presentation_id,
            messages=messages[-(MAX_STORED_TURNS * 2) :],
            updated_at=datetime.now(timezone.utc),
        )

    async def _get_row(self, conversation_id: uuid.UUID) -> KeyValueSqlModel | None:
        return await self._sql_session.scalar(
            select(KeyValueSqlModel).where(
                KeyValueSqlModel.key == self._conversation_key(conversation_id)
            )
        )

    @staticmethod
    def _conversation_key(conversation_id: uuid.UUID) -> str:
        return f"{CHAT_CONVERSATION_KEY_PREFIX}:{conversation_id}"
