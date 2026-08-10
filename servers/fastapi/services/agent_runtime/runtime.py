from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from services.agent_runtime.contracts import AgentRunResult, RunContext
from services.agent_runtime.events import EventSink, NullEventSink


class PresentationAgentRuntime:
    """Persistence-free adapter around the presentation LLM/tool engine."""

    def __init__(
        self,
        *,
        sql_session: AsyncSession,
        service_factory: Callable[..., Any],
    ) -> None:
        self._sql_session = sql_session
        self._service_factory = service_factory

    async def run(
        self,
        context: RunContext,
        event_sink: EventSink | None = None,
    ) -> AgentRunResult:
        sink = event_sink or NullEventSink()
        await context.raise_if_cancelled()
        service_kwargs: dict[str, Any] = {
            "sql_session": self._sql_session,
            "presentation_id": context.presentation_id,
            "conversation_id": context.conversation_id,
        }
        parameters = inspect.signature(self._service_factory).parameters.values()
        if context.artifact_scope is not None and (
            any(parameter.name == "artifact_directory" for parameter in parameters)
            or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        ):
            service_kwargs["artifact_directory"] = str(
                context.artifact_scope.directory
            )
        service = self._service_factory(
            **service_kwargs,
        )
        execute = getattr(service, "execute_run", None)
        if callable(execute):
            result = await execute(context, sink)
            if not isinstance(result, AgentRunResult):
                raise TypeError("agent_runtime_result_invalid")
            await context.raise_if_cancelled()
            return result

        # Narrow compatibility for deterministic legacy test/service adapters.
        # Production uses execute_run and never asks the service to persist.
        await sink.status("running")
        turn = await service.generate_reply(context.user_message)
        await context.raise_if_cancelled()
        await sink.assistant_delta(turn.response_text)
        await sink.finalize(turn.response_text)
        await context.raise_if_cancelled()
        return AgentRunResult(
            conversation_id=turn.conversation_id,
            assistant_output=turn.response_text,
            tool_calls=tuple(turn.tool_calls),
            page_remake_slide_index=turn.page_remake_slide_index,
            artifacts=(
                context.artifact_scope.discover()
                if context.artifact_scope is not None
                else ()
            ),
        )
