import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceConversation, WorkspaceMessage
from services.chat.chat_memory_store import CHAT_MEMORY_STORE
from services.chat import sql_chat_history
from services.workspace_db_adapter import reserve_conversation_positions


class ChatConversationStore:
    def __init__(self, sql_session: AsyncSession):
        self._sql = sql_session
        # Workspace operations wrap the legacy session so inner commits become
        # flushes.  Their external memory side effect must be emitted from the
        # transactional outbox only after the fenced publication commits.
        self._defer_external_memory = (
            getattr(sql_session, "defer_external_chat_memory", False) is True
        )
        self._defer_canonical_persistence = (
            getattr(sql_session, "defer_canonical_chat_persistence", False)
            is True
        )

    async def load_history(
        self,
        *,
        presentation_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> list[dict[str, str]]:
        conversation = await self._sql.get(
            WorkspaceConversation, conversation_id
        )
        if (
            conversation is not None
            and conversation.presentation_id == presentation_id
        ):
            rows = list(
                (
                    await self._sql.scalars(
                        select(WorkspaceMessage)
                        .where(
                            WorkspaceMessage.conversation_id
                            == conversation_id
                        )
                        .order_by(WorkspaceMessage.position.asc())
                    )
                ).all()
            )
            if rows:
                return [
                    {"role": row.role, "content": row.content}
                    for row in rows
                    if row.content
                ]

        # Read-only migration adapter for pre-unification records. Never copy
        # them into a second store or append new rows here.
        legacy_sql = await sql_chat_history.load_messages(
            self._sql,
            presentation_id=presentation_id,
            conversation_id=conversation_id,
        )
        if legacy_sql:
            return legacy_sql
        legacy = await CHAT_MEMORY_STORE.load_history(
            presentation_id=presentation_id,
            conversation_id=conversation_id,
        )
        return legacy

    async def append_turn(
        self,
        *,
        presentation_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message: str,
        assistant_message: str,
        tool_calls: list[str] | None = None,
    ) -> None:
        # Agent runtime execution is persistence-free; the fenced publication
        # transaction appends the canonical assistant message exactly once.
        if self._defer_canonical_persistence:
            return
        conversation = await self._sql.get(
            WorkspaceConversation, conversation_id
        )
        if (
            conversation is None
            or conversation.presentation_id != presentation_id
        ):
            raise ValueError("canonical_conversation_not_found")
        reservation = await reserve_conversation_positions(
            self._sql, conversation_id, 2
        )
        user_position, assistant_position = reservation.positions
        self._sql.add_all(
            (
                WorkspaceMessage(
                    conversation_id=conversation_id,
                    position=user_position,
                    role="user",
                    content=user_message,
                ),
                WorkspaceMessage(
                    conversation_id=conversation_id,
                    position=assistant_position,
                    role="assistant",
                    content=assistant_message,
                    message_metadata={"tool_calls": list(tool_calls or [])},
                ),
            )
        )
        if not self._defer_external_memory:
            await CHAT_MEMORY_STORE.store_chat_turn(
                presentation_id=presentation_id,
                conversation_id=conversation_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )

    async def retrieve_semantic_context(
        self,
        *,
        presentation_id: uuid.UUID,
        conversation_id: uuid.UUID,
        query: str,
    ) -> str:
        return await CHAT_MEMORY_STORE.retrieve_context(
            presentation_id=presentation_id,
            conversation_id=conversation_id,
            query=query,
        )

    async def ensure_conversation_id(
        self,
        conversation_id: uuid.UUID | None,
    ) -> uuid.UUID:
        return conversation_id or uuid.uuid4()
