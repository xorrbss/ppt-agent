import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from services.chat.conversation_store import ChatConversationStore


class TestChatConversationStore:
    def test_load_history_reads_sql_first(self):
        presentation_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        expected_history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        sql_session = MagicMock()
        sql_session.get = AsyncMock(return_value=None)

        with patch(
            "services.chat.conversation_store.sql_chat_history.load_messages",
            new=AsyncMock(return_value=expected_history),
        ) as load_sql, patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.load_history",
            new=AsyncMock(),
        ) as load_mem0:
            store = ChatConversationStore(sql_session)
            history = asyncio.run(
                store.load_history(
                    presentation_id=presentation_id,
                    conversation_id=conversation_id,
                )
            )

        load_sql.assert_awaited_once_with(
            sql_session,
            presentation_id=presentation_id,
            conversation_id=conversation_id,
        )
        load_mem0.assert_not_called()
        assert history == expected_history

    def test_load_history_falls_back_to_mem0_without_dual_write(self):
        presentation_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        legacy = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "from mem0"},
        ]
        sql_session = MagicMock()
        sql_session.get = AsyncMock(return_value=None)

        with patch(
            "services.chat.conversation_store.sql_chat_history.load_messages",
            new=AsyncMock(return_value=[]),
        ) as load_sql, patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.load_history",
            new=AsyncMock(return_value=legacy),
        ) as load_mem0:
            store = ChatConversationStore(sql_session)
            history = asyncio.run(
                store.load_history(
                    presentation_id=presentation_id,
                    conversation_id=conversation_id,
                )
            )

        load_sql.assert_awaited_once()
        load_mem0.assert_awaited_once()
        assert history == legacy

    def test_retrieve_semantic_context_delegates_to_chat_memory_store(self):
        store = ChatConversationStore(MagicMock())
        presentation_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        with patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.retrieve_context",
            new=AsyncMock(return_value="conversation-scoped context"),
        ) as retrieve_context:
            context = asyncio.run(
                store.retrieve_semantic_context(
                    presentation_id=presentation_id,
                    conversation_id=conversation_id,
                    query="What did we decide?",
                )
            )

        retrieve_context.assert_awaited_once_with(
            presentation_id=presentation_id,
            conversation_id=conversation_id,
            query="What did we decide?",
        )
        assert context == "conversation-scoped context"
