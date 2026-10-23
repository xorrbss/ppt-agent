import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceConversation, WorkspaceMessage
from services.chat.chat_memory_store import CHAT_MEMORY_STORE
from services.chat import sql_chat_history


class ChatConversationStore:
    def __init__(self, sql_session: AsyncSession):
        self._sql = sql_session

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
