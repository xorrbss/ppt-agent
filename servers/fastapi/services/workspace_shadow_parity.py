"""Best-effort dual-read parity checks for chat-first workspace timelines.

The check is intentionally content-blind outside this process: message text is
used only for in-memory equality and is never logged or attached to a metric.
Failures remain non-authoritative so a telemetry outage cannot break reads.
"""

from __future__ import annotations

from collections import Counter
import logging
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.chat.sql_chat_history import load_messages
from services.workspace_chat_operation_handler import CHAT_MUTATION_OPERATION_KINDS
from services.workspace_metrics import WORKSPACE_METRICS
from services.workspace_rollout_policy import resolve_active_workspace_write_rollout


LOGGER = logging.getLogger(__name__)


def _legacy_turns(
    messages: Sequence[Mapping[str, Any]],
) -> Counter[tuple[str, str]]:
    turns: Counter[tuple[str, str]] = Counter()
    for index in range(len(messages) - 1):
        user = messages[index]
        assistant = messages[index + 1]
        if user.get("role") != "user" or assistant.get("role") != "assistant":
            continue
        user_content = user.get("content")
        assistant_content = assistant.get("content")
        if isinstance(user_content, str) and isinstance(assistant_content, str):
            turns[(user_content, assistant_content)] += 1
    return turns


def _workspace_turns(
    timeline: Mapping[str, Any],
) -> tuple[list[tuple[str, str]], bool, bool]:
    """Return expected turns, whether any are comparable, and silent loss."""

    items = [item for item in timeline.get("items", []) if isinstance(item, Mapping)]
    by_position = {
        item.get("position"): item
        for item in items
        if isinstance(item.get("position"), int)
        and not isinstance(item.get("position"), bool)
    }
    min_position = min(by_position, default=None)
    expected: list[tuple[str, str]] = []
    comparable = False
    silent_loss = False

    for operation in items:
        if (
            operation.get("anchor_type") != "operation"
            or operation.get("status") != "succeeded"
            or operation.get("operation_type") not in CHAT_MUTATION_OPERATION_KINDS
        ):
            continue
        position = operation.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            continue
        message = by_position.get(position - 1)
        if message is None and position == min_position:
            # The command message may be just outside this backwards page.
            continue
        comparable = True
        if (
            not isinstance(message, Mapping)
            or message.get("anchor_type") != "message"
            or message.get("role") != "user"
            or not isinstance(message.get("content"), str)
        ):
            silent_loss = True
            continue
        result = operation.get("result")
        summary = result.get("summary") if isinstance(result, Mapping) else None
        events = operation.get("events")
        terminal_event = isinstance(events, list) and any(
            isinstance(event, Mapping) and event.get("type") == "operation.result"
            for event in events
        )
        if not isinstance(summary, str) or not terminal_event:
            silent_loss = True
            continue
        expected.append((message["content"], summary))

    return expected, comparable, silent_loss


async def observe_workspace_timeline_shadow_parity(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    timeline: Mapping[str, Any],
) -> None:
    """Compare the primary workspace timeline with the live legacy SQL view."""

    try:
        if not resolve_active_workspace_write_rollout(
            presentation_id
        ).shadow_mode:
            return
        raw_conversation_id = timeline.get("conversation_id")
        if raw_conversation_id is None:
            return
        conversation_id = uuid.UUID(str(raw_conversation_id))
        expected, comparable, silent_loss = _workspace_turns(timeline)
        if not comparable:
            return
        legacy = await load_messages(
            session,
            presentation_id=presentation_id,
            conversation_id=conversation_id,
        )
        available = _legacy_turns(legacy)
        matched = not silent_loss
        for pair in expected:
            if available[pair] <= 0:
                matched = False
                silent_loss = True
                continue
            available[pair] -= 1
        WORKSPACE_METRICS.record_shadow_parity(
            matched=matched,
            silent_loss=silent_loss,
        )
    except Exception:
        # Do not include exception text: driver errors can contain SQL values.
        LOGGER.warning("Workspace timeline shadow comparison failed")
        WORKSPACE_METRICS.record_signal_failure("shadow_parity")
