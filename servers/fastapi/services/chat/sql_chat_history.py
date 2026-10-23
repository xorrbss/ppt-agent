"""Persist presentation chat threads in SQL rows."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.chat_history_message import ChatHistoryMessageModel


def _serialize_created_at(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def load_messages(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[dict[str, str]]:
    rows = await load_messages_with_meta(
        session,
        presentation_id=presentation_id,
        conversation_id=conversation_id,
    )
    return [
        {"role": row["role"], "content": row["content"]}
        for row in rows
        if isinstance(row.get("role"), str) and isinstance(row.get("content"), str)
    ]


async def load_messages_with_meta(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.scalars(
                select(ChatHistoryMessageModel)
                .where(
                    ChatHistoryMessageModel.presentation_id == presentation_id,
                    ChatHistoryMessageModel.conversation_id == conversation_id,
                )
                .order_by(ChatHistoryMessageModel.position.asc())
            )
        ).all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "role": row.role,
            "content": row.content,
        }
        created = _serialize_created_at(row.created_at)
        if created:
            entry["created_at"] = created
        out.append(entry)
    return out
