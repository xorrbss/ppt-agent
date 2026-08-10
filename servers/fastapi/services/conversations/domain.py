"""Canonical domain names mapped to the single durable SQL store.

This is an intentional anti-corruption layer, not a second ORM mapping.  Keeping
the exact same class objects means every canonical write retains workspace CAS,
fencing, idempotency, retention and outbox semantics.
"""

from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)

Conversation = WorkspaceConversation
Message = WorkspaceMessage
AgentRun = WorkspaceOperation
RunEvent = WorkspaceOperationEvent
Asset = WorkspaceAsset

__all__ = ["Conversation", "Message", "AgentRun", "RunEvent", "Asset"]
