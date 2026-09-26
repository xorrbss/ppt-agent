"""A durable chat-memory delivery must keep its submitting tenant."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.workspace_chat_memory_outbox import deliver_workspace_chat_memory
from services.workspace_operation_principal import PRINCIPAL_TENANT_PAYLOAD_KEY
from test_workspace_chat_operation_handler import _database, _seed


def test_second_tenant_cannot_deliver_another_tenants_operation(tmp_path: Path) -> None:
    asyncio.run(_second_tenant_cannot_deliver_another_tenants_operation(tmp_path))


async def _second_tenant_cannot_deliver_another_tenants_operation(tmp_path: Path) -> None:
    engine, maker = await _database(tmp_path / "tenant-memory.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=3, expected_revision=3
        )
        owner_id = uuid.uuid4()
        async with maker() as session, session.begin():
            session.add(User(id=owner_id, username=f"tenant-{owner_id}", hashed_password="unused"))
            await session.flush()
            conversation = await session.get(WorkspaceConversation, claim.conversation_id)
            conversation.owner_id = owner_id
            operation = await session.get(WorkspaceOperation, operation_id)
            operation.owner_id = owner_id
            operation.owner_scope = str(owner_id)
            operation.request_payload = {
                "command": "change the deck",
                PRINCIPAL_TENANT_PAYLOAD_KEY: "tenant-a",
            }
            operation.state = "succeeded"
            operation.result_revision = 4
            operation.result = {"summary": "Deck updated."}
            operation.attempt_token = None

        payload = {
            "operation_id": str(operation_id),
            "owner_id": str(owner_id),
            PRINCIPAL_TENANT_PAYLOAD_KEY: "tenant-b",
            "presentation_id": str(presentation_id),
            "conversation_id": str(claim.conversation_id),
            "result_revision": 4,
        }
        memory = SimpleNamespace(store_chat_turn=AsyncMock())
        with pytest.raises(ValueError, match="workspace_chat_memory_publication_missing"):
            await deliver_workspace_chat_memory(
                payload, session_factory=maker, memory_store=memory
            )
        memory.store_chat_turn.assert_not_awaited()
        payload[PRINCIPAL_TENANT_PAYLOAD_KEY] = "tenant-a"
        await deliver_workspace_chat_memory(
            payload, session_factory=maker, memory_store=memory
        )
        memory.store_chat_turn.assert_awaited_once()
    finally:
        await engine.dispose()
