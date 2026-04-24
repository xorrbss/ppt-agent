import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from llmai import get_client  # type: ignore[import-not-found]
from llmai.shared import (  # type: ignore[import-not-found]
    AssistantMessage,
    Message,
    SystemMessage,
    ToolResponseMessage,
    UserMessage,
)
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from services.chat.conversation_store import ChatConversationStore
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.prompts import build_system_prompt
from services.chat.tools import ChatTools
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import get_llm_config
from utils.llm_provider import get_model
from utils.llm_utils import extract_text, get_generate_kwargs

LOGGER = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 16


@dataclass(frozen=True)
class ChatTurnResult:
    conversation_id: uuid.UUID
    response_text: str
    tool_calls: list[str]


class PresentationChatService:
    def __init__(
        self,
        sql_session: AsyncSession,
        presentation_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
    ):
        self._sql_session = sql_session
        self._presentation_id = presentation_id
        self._conversation_id = conversation_id

        self._conversation_store = ChatConversationStore(sql_session)
        self._memory = PresentationChatMemoryLayer(sql_session, presentation_id)
        self._tools = ChatTools(self._memory)

    async def generate_reply(self, user_message: str) -> ChatTurnResult:
        if not (user_message or "").strip():
            raise HTTPException(status_code=400, detail="Message is required")

        presentation = await self._sql_session.get(PresentationModel, self._presentation_id)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")

        conversation_id = await self._conversation_store.ensure_conversation_id(
            self._conversation_id
        )
        history = await self._conversation_store.load_history(
            presentation_id=self._presentation_id,
            conversation_id=conversation_id,
        )
        history_messages = self._convert_history_to_messages(history)

        memory_context = await self._memory.retrieve_context(user_message)
        messages: list[Message] = [
            SystemMessage(content=build_system_prompt(memory_context)),
            *history_messages,
            UserMessage(content=user_message),
        ]

        response_text, tool_calls = await self._run_llm_with_tools(messages)
        await self._conversation_store.append_turn(
            presentation_id=self._presentation_id,
            conversation_id=conversation_id,
            user_message=user_message,
            assistant_message=response_text,
        )

        return ChatTurnResult(
            conversation_id=conversation_id,
            response_text=response_text,
            tool_calls=tool_calls,
        )

    async def _run_llm_with_tools(self, messages: list[Message]) -> tuple[str, list[str]]:
        # llmai is the only LLM entrypoint; provider selection comes from app config.
        client = get_client(config=get_llm_config())
        model = get_model()
        tools = self._tools.get_tool_definitions()

        called_tools: list[str] = []
        last_tool_results: list[dict[str, Any]] = []

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = await asyncio.to_thread(
                    client.generate,
                    **get_generate_kwargs(
                        model=model,
                        messages=messages,
                        tools=tools,
                    ),
                )
            except Exception as exc:
                raise handle_llm_client_exceptions(exc)

            if not response.tool_calls:
                response_text = extract_text(response.content) or (
                    "I could not generate a response for that request."
                )
                return response_text, called_tools

            called_tools.extend([tool_call.name for tool_call in response.tool_calls])
            # Reuse llmai-returned threaded messages so provider adapters keep state.
            messages = list(response.messages) if response.messages else list(messages)

            last_tool_results = []
            for tool_call in response.tool_calls:
                tool_result = await self._tools.execute_tool_call(tool_call)
                last_tool_results.append(tool_result)
                tool_response_content = json.dumps(tool_result, ensure_ascii=False)
                # Tool responses are fed back into llmai to let the model continue.
                messages.append(
                    ToolResponseMessage(
                        id=tool_call.id,
                        content=[tool_response_content],
                    )
                )

        LOGGER.warning("Max tool rounds reached in chat flow")
        final_response = await self._try_final_response_without_tools(
            client=client,
            model=model,
            messages=messages,
        )
        if final_response:
            return final_response, called_tools

        return self._build_tool_limit_fallback(last_tool_results), called_tools

    async def _try_final_response_without_tools(
        self,
        *,
        client: Any,
        model: str,
        messages: list[Message],
    ) -> str | None:
        """
        Give the model one final chance to synthesize a natural-language answer
        from already-executed tool outputs, without allowing more tool calls.
        """
        try:
            response = await asyncio.to_thread(
                client.generate,
                **get_generate_kwargs(
                    model=model,
                    messages=messages,
                ),
            )
        except Exception:
            LOGGER.warning("Final no-tool synthesis call failed", exc_info=True)
            return None

        return extract_text(response.content)

    @staticmethod
    def _build_tool_limit_fallback(last_tool_results: list[dict[str, Any]]) -> str:
        for entry in reversed(last_tool_results):
            if not isinstance(entry, dict):
                continue
            if not entry.get("ok"):
                continue
            result = entry.get("result")
            if not isinstance(result, dict):
                continue
            message = result.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()

        return (
            "I completed several tool operations but could not finalize the response "
            "within the tool limit. Please ask a follow-up and I will continue."
        )

    @staticmethod
    def _convert_history_to_messages(history: list[dict[str, str]]) -> list[Message]:
        messages: list[Message] = []
        for item in history:
            role = item.get("role")
            content = item.get("content")
            if not content:
                continue
            if role == "user":
                messages.append(UserMessage(content=content))
            elif role == "assistant":
                messages.append(AssistantMessage(content=[content]))
        return messages
