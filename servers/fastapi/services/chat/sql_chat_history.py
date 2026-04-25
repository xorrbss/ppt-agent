"""Persist presentation chat threads in ``KeyValueSqlModel``.

Each conversation is one row: key ``ppt_chat:{presentation_id}:{conversation_id}``,
value is JSON: ``{version, messages, updated_at, ...}``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.key_value import KeyValueSqlModel

CHAT_HISTORY_KEY_PREFIX = "ppt_chat"
SCHEMA_VERSION = 1


def chat_history_key(presentation_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return f"{CHAT_HISTORY_KEY_PREFIX}:{presentation_id}:{conversation_id}"


def _parse_conversation_key(key: str, presentation_id: uuid.UUID) -> uuid.UUID | None:
    expected_prefix = f"{CHAT_HISTORY_KEY_PREFIX}:{presentation_id}:"
    if not key.startswith(expected_prefix):
        return None
    rest = key[len(expected_prefix) :]
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_messages(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[dict[str, str]]:
    """Load ordered user/assistant messages for LLM context (role + content only)."""
    key = chat_history_key(presentation_id, conversation_id)
    row = await session.scalar(
        select(KeyValueSqlModel).where(KeyValueSqlModel.key == key)
    )
    if not row or not isinstance(row.value, dict):
        return []
    messages = row.value.get("messages")
    if not isinstance(messages, list):
        return []
    out: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": item["role"], "content": content})
    return out


async def load_messages_with_meta(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Load messages including optional ``created_at`` for API / UI."""
    key = chat_history_key(presentation_id, conversation_id)
    row = await session.scalar(
        select(KeyValueSqlModel).where(KeyValueSqlModel.key == key)
    )
    if not row or not isinstance(row.value, dict):
        return []
    messages = row.value.get("messages")
    if not isinstance(messages, list):
        return []
    out: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        entry: dict[str, Any] = {
            "role": item["role"],
            "content": content,
        }
        created = item.get("created_at")
        if isinstance(created, str) and created.strip():
            entry["created_at"] = created.strip()
        out.append(entry)
    return out


async def replace_messages(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
    messages: list[dict[str, str]],
) -> None:
    """Replace transcript (e.g. one-time sync from mem0)."""
    key = chat_history_key(presentation_id, conversation_id)
    row = await session.scalar(
        select(KeyValueSqlModel).where(KeyValueSqlModel.key == key)
    )
    now = _utc_now_iso()
    built: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        built.append(
            {
                "role": m["role"],
                "content": content,
                "created_at": now,
            }
        )
    value = {
        "version": SCHEMA_VERSION,
        "presentation_id": str(presentation_id),
        "conversation_id": str(conversation_id),
        "messages": built,
        "updated_at": now,
    }
    if row is None:
        session.add(KeyValueSqlModel(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def append_turn(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
) -> None:
    key = chat_history_key(presentation_id, conversation_id)
    row = await session.scalar(
        select(KeyValueSqlModel).where(KeyValueSqlModel.key == key)
    )
    now = _utc_now_iso()
    new_messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message, "created_at": now},
        {"role": "assistant", "content": assistant_message, "created_at": now},
    ]
    if row is None:
        value: dict[str, Any] = {
            "version": SCHEMA_VERSION,
            "presentation_id": str(presentation_id),
            "conversation_id": str(conversation_id),
            "messages": new_messages,
            "updated_at": now,
        }
        session.add(KeyValueSqlModel(key=key, value=value))
    else:
        data = row.value if isinstance(row.value, dict) else {}
        existing = data.get("messages")
        if not isinstance(existing, list):
            existing = []
        combined = [m for m in existing if isinstance(m, dict)]
        combined.extend(new_messages)
        data["version"] = SCHEMA_VERSION
        data["presentation_id"] = str(presentation_id)
        data["conversation_id"] = str(conversation_id)
        data["messages"] = combined
        data["updated_at"] = now
        row.value = data
    await session.flush()


async def list_conversations(
    session: AsyncSession, *, presentation_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Return conversation summaries for a presentation, newest ``updated_at`` first."""
    prefix = f"{CHAT_HISTORY_KEY_PREFIX}:{presentation_id}:"
    result = await session.scalars(
        select(KeyValueSqlModel).where(KeyValueSqlModel.key.startswith(prefix))
    )
    rows = list(result.all())
    summaries: list[dict[str, Any]] = []
    for row in rows:
        cid = _parse_conversation_key(row.key, presentation_id)
        if cid is None:
            continue
        data = row.value if isinstance(row.value, dict) else {}
        updated_at: str | None = None
        raw_u = data.get("updated_at")
        if isinstance(raw_u, str) and raw_u.strip():
            updated_at = raw_u.strip()
        messages = data.get("messages")
        preview: str | None = None
        if isinstance(messages, list) and messages:
            for item in reversed(messages):
                if not isinstance(item, dict):
                    continue
                c = item.get("content")
                if isinstance(c, str) and c.strip():
                    preview = c.strip()
                    if len(preview) > 200:
                        preview = f"{preview[:200]}…"
                    break
        summaries.append(
            {
                "conversation_id": str(cid),
                "updated_at": updated_at,
                "last_message_preview": preview,
            }
        )
    summaries.sort(
        key=lambda s: s.get("updated_at") or "",
        reverse=True,
    )
    return summaries
