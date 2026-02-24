import asyncio
import dirtyjson
import httpx
import json
from typing import AsyncGenerator, List, Optional
from fastapi import HTTPException
from openai import AsyncOpenAI
from openai.types.chat.chat_completion_chunk import (
    ChatCompletionChunk as OpenAIChatCompletionChunk,
)
from google import genai
from google.genai.types import Content as GoogleContent, Part as GoogleContentPart
from google.genai.types import (
    GenerateContentConfig,
    GoogleSearch,
    ToolConfig as GoogleToolConfig,
    FunctionCallingConfig as GoogleFunctionCallingConfig,
    FunctionCallingConfigMode as GoogleFunctionCallingConfigMode,
)
from google.genai.types import Tool as GoogleTool
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic import MessageStreamEvent as AnthropicMessageStreamEvent
from enums.llm_provider import LLMProvider
from models.llm_message import (
    AnthropicAssistantMessage,
    AnthropicUserMessage,
    GoogleAssistantMessage,
    GoogleToolCallMessage,
    OpenAIAssistantMessage,
    LLMMessage,
    LLMSystemMessage,
    LLMUserMessage,
)
from models.llm_tool_call import (
    AnthropicToolCall,
    GoogleToolCall,
    LLMToolCall,
    OpenAIToolCall,
    OpenAIToolCallFunction,
)
from models.llm_tools import LLMDynamicTool, LLMTool
from services.llm_tool_calls_handler import LLMToolCallsHandler
from utils.async_iterator import iterator_to_async
from utils.dummy_functions import do_nothing_async
from utils.get_env import (
    get_anthropic_api_key_env,
    get_codex_access_token_env,
    get_codex_account_id_env,
    get_codex_refresh_token_env,
    get_codex_token_expires_env,
    get_custom_llm_api_key_env,
    get_custom_llm_url_env,
    get_disable_thinking_env,
    get_google_api_key_env,
    get_ollama_url_env,
    get_openai_api_key_env,
    get_tool_calls_env,
    get_web_grounding_env,
)
from utils.set_env import (
    set_codex_access_token_env,
    set_codex_account_id_env,
    set_codex_refresh_token_env,
    set_codex_token_expires_env,
)
from utils.llm_provider import get_llm_provider, get_model
from utils.parsers import parse_bool_or_none
from utils.schema_utils import (
    ensure_strict_json_schema,
    flatten_json_schema,
    remove_titles_from_schema,
)


def _to_responses_tools(chat_tools: List[dict]) -> List[dict]:
    """Convert Chat Completions tool format to flat Responses API format.

    Chat Completions:  {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses API:     {"type": "function", "name": ..., "description": ..., "parameters": ...}
    """
    result = []
    for tool in chat_tools:
        if tool.get("type") != "function":
            result.append(tool)
            continue
        fn = tool.get("function") or tool
        result.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        })
    return result


class LLMClient:
    def __init__(self):
        self.llm_provider = get_llm_provider()
        self._client = self._get_client()
        self.tool_calls_handler = LLMToolCallsHandler(self)

    # ? Use tool calls
    def use_tool_calls_for_structured_output(self) -> bool:
        if self.llm_provider != LLMProvider.CUSTOM:
            return False
        return parse_bool_or_none(get_tool_calls_env()) or False

    # ? Web Grounding
    def enable_web_grounding(self) -> bool:
        if (
            self.llm_provider == LLMProvider.OLLAMA
            or self.llm_provider == LLMProvider.CUSTOM
        ):
            return False
        return parse_bool_or_none(get_web_grounding_env()) or False

    # ? Disable thinking
    def disable_thinking(self) -> bool:
        return parse_bool_or_none(get_disable_thinking_env()) or False

    # ? Clients
    def _get_client(self):
        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._get_openai_client()
            case LLMProvider.GOOGLE:
                return self._get_google_client()
            case LLMProvider.ANTHROPIC:
                return self._get_anthropic_client()
            case LLMProvider.OLLAMA:
                return self._get_ollama_client()
            case LLMProvider.CUSTOM:
                return self._get_custom_client()
            case LLMProvider.CODEX:
                return None  # Codex uses direct httpx calls, not self._client
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="LLM Provider must be either openai, google, anthropic, ollama, custom, or codex",
                )

    def _get_openai_client(self):
        if not get_openai_api_key_env():
            raise HTTPException(
                status_code=400,
                detail="OpenAI API Key is not set",
            )
        return AsyncOpenAI()

    def _get_google_client(self):
        if not get_google_api_key_env():
            raise HTTPException(
                status_code=400,
                detail="Google API Key is not set",
            )
        return genai.Client()

    def _get_anthropic_client(self):
        if not get_anthropic_api_key_env():
            raise HTTPException(
                status_code=400,
                detail="Anthropic API Key is not set",
            )
        return AsyncAnthropic()

    def _get_ollama_client(self):
        return AsyncOpenAI(
            base_url=(get_ollama_url_env() or "http://localhost:11434") + "/v1",
            api_key="ollama",
        )

    def _get_custom_client(self):
        if not get_custom_llm_url_env():
            raise HTTPException(
                status_code=400,
                detail="Custom LLM URL is not set",
            )
        return AsyncOpenAI(
            base_url=get_custom_llm_url_env(),
            api_key=get_custom_llm_api_key_env() or "null",
        )

    def _get_codex_headers(self) -> dict:
        """Return the HTTP headers required for Codex Responses API requests.

        Handles token auto-refresh if the stored token is expired or within
        60 s of expiry before building the header dict.
        """
        access_token = get_codex_access_token_env()
        if not access_token:
            raise HTTPException(
                status_code=400,
                detail="Codex OAuth access token is not set. Please authenticate via /api/v1/ppt/codex/auth/initiate",
            )

        # Auto-refresh if the token is expired or about to expire (within 60 s)
        expires_str = get_codex_token_expires_env()
        if expires_str:
            try:
                expires_ms = int(expires_str)
                now_ms = int(__import__("time").time() * 1000)
                if now_ms >= expires_ms - 60_000:
                    refresh_token = get_codex_refresh_token_env()
                    if refresh_token:
                        from utils.oauth.openai_codex import (
                            get_account_id,
                            refresh_access_token,
                            TokenSuccess,
                        )
                        result = refresh_access_token(refresh_token)
                        if isinstance(result, TokenSuccess):
                            set_codex_access_token_env(result.access)
                            set_codex_refresh_token_env(result.refresh)
                            set_codex_token_expires_env(str(result.expires))
                            account_id = get_account_id(result.access)
                            if account_id:
                                set_codex_account_id_env(account_id)
                            access_token = result.access
            except (ValueError, TypeError):
                pass

        account_id = get_codex_account_id_env() or ""
        return {
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

    # -------------------------------------------------------------------------
    # Codex (Responses API) helpers
    # -------------------------------------------------------------------------

    def _build_codex_body(
        self,
        model: str,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
    ) -> dict:
        """Convert LLMMessages to the Responses API request body for Codex."""
        instructions = None
        input_messages = []

        for msg in messages:
            if isinstance(msg, LLMSystemMessage):
                instructions = msg.content
            elif isinstance(msg, LLMUserMessage):
                input_messages.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": msg.content}],
                })
            elif isinstance(msg, OpenAIAssistantMessage):
                # Assistant turn — may carry text or tool-call data
                text = msg.content or ""
                if text:
                    input_messages.append({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    })
                # Tool calls from a previous assistant turn are already resolved
                # via the tool result messages that follow; skip raw tool call data.
            else:
                # Generic fallback: treat as user message
                text = getattr(msg, "content", "") or ""
                if text:
                    input_messages.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    })

        body: dict = {
            "model": model,
            "store": False,
            "stream": True,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        if instructions:
            body["instructions"] = instructions
        if input_messages:
            body["input"] = input_messages
        if tools:
            # Responses API uses flat format: {"type":"function","name":...}
            body["tools"] = tools

        return body

    async def _stream_codex_raw(
        self,
        model: str,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
    ):
        """Async generator of raw SSE event dicts from the Codex Responses API."""
        headers = self._get_codex_headers()
        body = self._build_codex_body(model, messages, tools)

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            async with http_client.stream(
                "POST",
                "https://chatgpt.com/backend-api/codex/responses",
                json=body,
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=f"Codex API error {resp.status_code}: {error_text.decode(errors='replace')[:400]}",
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue

    async def _stream_codex(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        """Stream text from the Codex Responses API, handling tool calls."""
        # Convert Chat-Completions tool format to flat Responses API format
        responses_tools = _to_responses_tools(tools) if tools else None

        tool_calls_by_id: dict[str, dict] = {}

        async for event in self._stream_codex_raw(model, messages, responses_tools):
            event_type = event.get("type", "")

            if event_type == "response.output_text.delta":
                delta = event.get("delta", "")
                if delta:
                    yield delta

            elif event_type == "response.output_item.done":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    tool_calls_by_id[item.get("call_id", item.get("id", ""))] = item

            elif event_type in ("response.failed", "error"):
                msg_text = event.get("message") or str(event)
                raise HTTPException(status_code=502, detail=f"Codex stream error: {msg_text}")

        if tool_calls_by_id and tools and depth < 5:
            openai_calls = [
                OpenAIToolCall(
                    id=item.get("call_id", item.get("id", "")),
                    type="function",
                    function=OpenAIToolCallFunction(
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    ),
                )
                for item in tool_calls_by_id.values()
            ]
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_openai(
                openai_calls
            )
            new_messages = [
                *messages,
                OpenAIAssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[tc.model_dump() for tc in openai_calls],
                ),
                *tool_call_messages,
            ]
            async for chunk in self._stream_codex(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            ):
                yield chunk

    async def _generate_codex(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> str | None:
        """Non-streaming text generation via Codex Responses API."""
        responses_tools = _to_responses_tools(tools) if tools else None

        text_parts: list[str] = []
        tool_calls_by_id: dict[str, dict] = {}

        async for event in self._stream_codex_raw(model, messages, responses_tools):
            event_type = event.get("type", "")

            if event_type == "response.output_text.delta":
                delta = event.get("delta", "")
                if delta:
                    text_parts.append(delta)

            elif event_type == "response.output_item.done":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    tool_calls_by_id[item.get("call_id", item.get("id", ""))] = item

            elif event_type in ("response.failed", "error"):
                msg_text = event.get("message") or str(event)
                raise HTTPException(status_code=502, detail=f"Codex error: {msg_text}")

        if tool_calls_by_id and tools and depth < 5:
            openai_calls = [
                OpenAIToolCall(
                    id=item.get("call_id", item.get("id", "")),
                    type="function",
                    function=OpenAIToolCallFunction(
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    ),
                )
                for item in tool_calls_by_id.values()
            ]
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_openai(
                openai_calls
            )
            new_messages = [
                *messages,
                OpenAIAssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[tc.model_dump() for tc in openai_calls],
                ),
                *tool_call_messages,
            ]
            return await self._generate_codex(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            )

        return "".join(text_parts) or None

    async def _stream_codex_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        """Stream structured output from Codex via a ResponseSchema tool call."""
        # Build the ResponseSchema tool in flat Responses API format
        schema = response_format
        if strict and depth == 0:
            schema = ensure_strict_json_schema(schema, path=(), root=schema)

        response_schema_tool = {
            "type": "function",
            "name": "ResponseSchema",
            "description": "Provide response to the user",
            "parameters": schema,
        }

        all_tools: list[dict] = [response_schema_tool]
        if tools:
            all_tools.extend(_to_responses_tools(tools))

        has_response_schema_call = False
        tool_calls_by_id: dict[str, dict] = {}
        current_call_id: Optional[str] = None

        async for event in self._stream_codex_raw(model, messages, all_tools):
            event_type = event.get("type", "")

            if event_type == "response.output_item.added":
                item = event.get("item") or {}
                if item.get("type") == "function_call" and item.get("name") == "ResponseSchema":
                    current_call_id = item.get("call_id", item.get("id"))
                    has_response_schema_call = True

            elif event_type == "response.function_call_arguments.delta":
                if current_call_id is not None or has_response_schema_call:
                    delta = event.get("delta", "")
                    if delta:
                        yield delta

            elif event_type == "response.output_item.done":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    tool_calls_by_id[item.get("call_id", item.get("id", ""))] = item

            elif event_type in ("response.failed", "error"):
                msg_text = event.get("message") or str(event)
                raise HTTPException(status_code=502, detail=f"Codex structured error: {msg_text}")

        # Handle non-ResponseSchema tool calls recursively
        other_tool_calls = {
            k: v for k, v in tool_calls_by_id.items()
            if v.get("name") != "ResponseSchema"
        }
        if other_tool_calls and tools and depth < 5:
            openai_calls = [
                OpenAIToolCall(
                    id=item.get("call_id", item.get("id", "")),
                    type="function",
                    function=OpenAIToolCallFunction(
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    ),
                )
                for item in other_tool_calls.values()
            ]
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_openai(
                openai_calls
            )
            new_messages = [
                *messages,
                OpenAIAssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[tc.model_dump() for tc in openai_calls],
                ),
                *tool_call_messages,
            ]
            async for chunk in self._stream_codex_structured(
                model=model,
                messages=new_messages,
                response_format=response_format,
                strict=strict,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            ):
                yield chunk

    async def _generate_codex_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> dict | None:
        """Non-streaming structured output from Codex via ResponseSchema tool."""
        accumulated: list[str] = []
        async for chunk in self._stream_codex_structured(
            model=model,
            messages=messages,
            response_format=response_format,
            strict=strict,
            max_tokens=max_tokens,
            tools=tools,
            depth=depth,
        ):
            accumulated.append(chunk)

        raw = "".join(accumulated)
        if not raw:
            return None
        if depth == 0:
            return dict(dirtyjson.loads(raw))
        return raw

    # ? Prompts
    def _get_system_prompt(self, messages: List[LLMMessage]) -> str:
        for message in messages:
            if isinstance(message, LLMSystemMessage):
                return message.content
        return ""

    def _get_google_messages(self, messages: List[LLMMessage]) -> List[GoogleContent]:
        contents = []
        for message in messages:
            if isinstance(message, LLMUserMessage):
                contents.append(
                    GoogleContent(
                        role=message.role,
                        parts=[GoogleContentPart(text=message.content)],
                    )
                )
            elif isinstance(message, GoogleAssistantMessage):
                contents.append(message.content)
            elif isinstance(message, GoogleToolCallMessage):
                contents.append(
                    GoogleContent(
                        role="user",
                        parts=[
                            GoogleContentPart.from_function_response(
                                name=message.name,
                                response=message.response,
                            )
                        ],
                    )
                )

        return contents

    def _get_anthropic_messages(self, messages: List[LLMMessage]) -> List[LLMMessage]:
        return [
            message for message in messages if not isinstance(message, LLMSystemMessage)
        ]

    # ? Generate Unstructured Content
    async def _generate_openai(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        extra_body: Optional[dict] = None,
        depth: int = 0,
    ) -> str | None:
        client: AsyncOpenAI = self._client
        response = await client.chat.completions.create(
            model=model,
            messages=[message.model_dump() for message in messages],
            max_completion_tokens=max_tokens,
            tools=tools,
            extra_body=extra_body,
        )

        if len(response.choices) == 0:
            return None

        tool_calls = response.choices[0].message.tool_calls
        if tool_calls:
            parsed_tool_calls = [
                OpenAIToolCall(
                    id=tool_call.id,
                    type=tool_call.type,
                    function=OpenAIToolCallFunction(
                        name=tool_call.function.name,
                        arguments=tool_call.function.arguments,
                    ),
                )
                for tool_call in tool_calls
            ]
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_openai(
                parsed_tool_calls
            )
            assistant_message = OpenAIAssistantMessage(
                role="assistant",
                content=response.choices[0].message.content,
                tool_calls=[tool_call.model_dump() for tool_call in parsed_tool_calls],
            )
            new_messages = [
                *messages,
                assistant_message,
                *tool_call_messages,
            ]
            return await self._generate_openai(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                extra_body=extra_body,
                depth=depth + 1,
            )

        return response.choices[0].message.content

    async def _generate_google(
        self,
        model: str,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ) -> str | None:
        client: genai.Client = self._client

        google_tools = None
        if tools:
            google_tools = [GoogleTool(function_declarations=[tool]) for tool in tools]

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=self._get_google_messages(messages),
            config=GenerateContentConfig(
                tools=google_tools,
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="text/plain",
                max_output_tokens=max_tokens,
            ),
        )

        content = response.candidates[0].content
        response_parts = content.parts

        if not response_parts:
            return None

        text_content = None
        tool_calls = []
        for each_part in response_parts:
            if each_part.function_call:
                tool_calls.append(
                    GoogleToolCall(
                        id=each_part.function_call.id,
                        name=each_part.function_call.name,
                        arguments=each_part.function_call.args,
                    )
                )
            if each_part.text:
                text_content = each_part.text

        if tool_calls:
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_google(
                tool_calls
            )
            new_messages = [
                *messages,
                GoogleAssistantMessage(
                    role="assistant",
                    content=content,
                ),
                *tool_call_messages,
            ]
            return await self._generate_google(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            )

        return text_content

    async def _generate_anthropic(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> str | None:
        client: AsyncAnthropic = self._client

        response: AnthropicMessage = await client.messages.create(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_anthropic_messages(messages)
            ],
            tools=tools,
            max_tokens=max_tokens or 4000,
        )
        text_content = None
        tool_calls: List[AnthropicToolCall] = []
        for content in response.content:
            if content.type == "text" and isinstance(content.text, str):
                text_content = content.text

            if content.type == "tool_use":
                tool_calls.append(
                    AnthropicToolCall(
                        id=content.id,
                        type=content.type,
                        name=content.name,
                        input=content.input,
                    )
                )

        if tool_calls:
            tool_call_messages = (
                await self.tool_calls_handler.handle_tool_calls_anthropic(tool_calls)
            )
            new_messages = [
                *messages,
                AnthropicAssistantMessage(
                    role="assistant",
                    content=[each.model_dump() for each in tool_calls],
                ),
                AnthropicUserMessage(
                    role="user",
                    content=[each.model_dump() for each in tool_call_messages],
                ),
            ]
            return await self._generate_anthropic(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            )

        return text_content

    async def _generate_ollama(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        return await self._generate_openai(
            model=model, messages=messages, max_tokens=max_tokens, depth=depth
        )

    async def _generate_custom(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        extra_body = {"enable_thinking": False} if self.disable_thinking() else None
        return await self._generate_openai(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            extra_body=extra_body,
            depth=depth,
        )

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[type[LLMTool] | LLMDynamicTool]] = None,
    ):
        parsed_tools = self.tool_calls_handler.parse_tools(tools)

        content = None
        match self.llm_provider:
            case LLMProvider.OPENAI:
                content = await self._generate_openai(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.CODEX:
                content = await self._generate_codex(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.GOOGLE:
                content = await self._generate_google(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.OLLAMA:
                content = await self._generate_ollama(
                    model=model, messages=messages, max_tokens=max_tokens
                )
            case LLMProvider.CUSTOM:
                content = await self._generate_custom(
                    model=model, messages=messages, max_tokens=max_tokens
                )
        if content is None:
            raise HTTPException(
                status_code=400,
                detail="LLM did not return any content",
            )
        return content

    # ? Generate Structured Content
    async def _generate_openai_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        extra_body: Optional[dict] = None,
        depth: int = 0,
    ) -> dict | None:
        client: AsyncOpenAI = self._client
        response_schema = response_format
        all_tools = [*tools] if tools else None

        use_tool_calls_for_structured_output = (
            self.use_tool_calls_for_structured_output()
        )
        if strict and depth == 0:
            response_schema = ensure_strict_json_schema(
                response_schema,
                path=(),
                root=response_schema,
            )
        if use_tool_calls_for_structured_output and depth == 0:
            if all_tools is None:
                all_tools = []
            all_tools.append(
                self.tool_calls_handler.parse_tool(
                    LLMDynamicTool(
                        name="ResponseSchema",
                        description="Provide response to the user",
                        parameters=response_schema,
                        handler=do_nothing_async,
                    ),
                    strict=strict,
                )
            )

        response = await client.chat.completions.create(
            model=model,
            messages=[message.model_dump() for message in messages],
            response_format=(
                {
                    "type": "json_schema",
                    "json_schema": (
                        {
                            "name": "ResponseSchema",
                            "strict": strict,
                            "schema": response_schema,
                        }
                    ),
                }
                if not use_tool_calls_for_structured_output
                else None
            ),
            max_completion_tokens=max_tokens,
            tools=all_tools,
            extra_body=extra_body,
        )

        if len(response.choices) == 0:
            return None

        content = response.choices[0].message.content

        tool_calls = response.choices[0].message.tool_calls
        has_response_schema = False

        if tool_calls:
            for tool_call in tool_calls:
                if tool_call.function.name == "ResponseSchema":
                    content = tool_call.function.arguments
                    has_response_schema = True

            if not has_response_schema:
                parsed_tool_calls = [
                    OpenAIToolCall(
                        id=tool_call.id,
                        type=tool_call.type,
                        function=OpenAIToolCallFunction(
                            name=tool_call.function.name,
                            arguments=tool_call.function.arguments,
                        ),
                    )
                    for tool_call in tool_calls
                ]
                tool_call_messages = (
                    await self.tool_calls_handler.handle_tool_calls_openai(
                        parsed_tool_calls
                    )
                )
                new_messages = [
                    *messages,
                    OpenAIAssistantMessage(
                        role="assistant",
                        content=response.choices[0].message.content,
                        tool_calls=[each.model_dump() for each in parsed_tool_calls],
                    ),
                    *tool_call_messages,
                ]
                content = await self._generate_openai_structured(
                    model=model,
                    messages=new_messages,
                    response_format=response_schema,
                    strict=strict,
                    max_tokens=max_tokens,
                    tools=all_tools,
                    extra_body=extra_body,
                    depth=depth + 1,
                )
        if content:
            if depth == 0:
                return dict(dirtyjson.loads(content))
            return content
        return None

    async def _generate_google_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> dict | None:
        client: genai.Client = self._client

        google_tools = None
        if tools:
            google_tools = [GoogleTool(function_declarations=[tool]) for tool in tools]
            google_tools.append(
                GoogleTool(
                    function_declarations=[
                        {
                            "name": "ResponseSchema",
                            "description": "Provide response to the user",
                            "parameters": remove_titles_from_schema(
                                flatten_json_schema(response_format)
                            ),
                        }
                    ]
                )
            )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=self._get_google_messages(messages),
            config=GenerateContentConfig(
                tools=google_tools,
                tool_config=(
                    GoogleToolConfig(
                        function_calling_config=GoogleFunctionCallingConfig(
                            mode=GoogleFunctionCallingConfigMode.ANY,
                        ),
                    )
                    if tools
                    else None
                ),
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="application/json" if not tools else None,
                response_json_schema=response_format if not tools else None,
                max_output_tokens=max_tokens,
            ),
        )

        content = response.candidates[0].content
        response_parts = content.parts
        text_content = None

        if not response_parts:
            return None

        tool_calls: List[GoogleToolCall] = []
        for each_part in response_parts:
            if each_part.function_call:
                tool_calls.append(
                    GoogleToolCall(
                        id=each_part.function_call.id,
                        name=each_part.function_call.name,
                        arguments=each_part.function_call.args,
                    )
                )

            if each_part.text:
                text_content = each_part.text

        for each in tool_calls:
            if each.name == "ResponseSchema":
                return each.arguments

        if tool_calls:
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_google(
                tool_calls
            )
            new_messages = [
                *messages,
                GoogleAssistantMessage(
                    role="assistant",
                    content=content,
                ),
                *tool_call_messages,
            ]
            return await self._generate_google_structured(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
                depth=depth + 1,
            )

        if text_content:
            return dict(dirtyjson.loads(text_content))
        return None

    async def _generate_anthropic_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        tools: Optional[List[dict]] = None,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: AsyncAnthropic = self._client
        response: AnthropicMessage = await client.messages.create(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_anthropic_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
            tools=[
                {
                    "name": "ResponseSchema",
                    "description": "A response to the user's message",
                    "input_schema": response_format,
                },
                *(tools or []),
            ],
        )
        tool_calls: List[AnthropicToolCall] = []
        for content in response.content:
            if content.type == "tool_use":
                tool_calls.append(
                    AnthropicToolCall(
                        id=content.id,
                        type=content.type,
                        name=content.name,
                        input=content.input,
                    )
                )

        for each in tool_calls:
            if each.name == "ResponseSchema":
                return each.input

        if tool_calls:
            tool_call_messages = (
                await self.tool_calls_handler.handle_tool_calls_anthropic(tool_calls)
            )
            new_messages = [
                *messages,
                AnthropicAssistantMessage(
                    role="assistant",
                    content=[each.model_dump() for each in tool_calls],
                ),
                AnthropicUserMessage(
                    role="user",
                    content=[each.model_dump() for each in tool_call_messages],
                ),
            ]
            return await self._generate_anthropic_structured(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
                depth=depth + 1,
            )

        return None

    async def _generate_ollama_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        return await self._generate_openai_structured(
            model=model,
            messages=messages,
            response_format=response_format,
            strict=strict,
            max_tokens=max_tokens,
            depth=depth,
        )

    async def _generate_custom_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        extra_body = {"enable_thinking": False} if self.disable_thinking() else None
        return await self._generate_openai_structured(
            model=model,
            messages=messages,
            response_format=response_format,
            strict=strict,
            max_tokens=max_tokens,
            extra_body=extra_body,
            depth=depth,
        )

    async def generate_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        tools: Optional[List[type[LLMTool] | LLMDynamicTool]] = None,
        max_tokens: Optional[int] = None,
    ) -> dict:
        parsed_tools = self.tool_calls_handler.parse_tools(tools)

        content = None
        match self.llm_provider:
            case LLMProvider.OPENAI:
                content = await self._generate_openai_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.CODEX:
                content = await self._generate_codex_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.GOOGLE:
                content = await self._generate_google_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.OLLAMA:
                content = await self._generate_ollama_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    max_tokens=max_tokens,
                )
            case LLMProvider.CUSTOM:
                content = await self._generate_custom_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    max_tokens=max_tokens,
                )
        if content is None:
            raise HTTPException(
                status_code=400,
                detail="LLM did not return any content",
            )
        return content

    # ? Stream Unstructured Content
    async def _stream_openai(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        extra_body: Optional[dict] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        client: AsyncOpenAI = self._client

        tool_calls: List[LLMToolCall] = []
        current_index = 0
        current_id = None
        current_name = None
        current_arguments = None
        async for event in await client.chat.completions.create(
            model=model,
            messages=[message.model_dump() for message in messages],
            max_completion_tokens=max_tokens,
            tools=tools,
            extra_body=extra_body,
            stream=True,
        ):
            event: OpenAIChatCompletionChunk = event
            if not event.choices:
                continue

            content_chunk = event.choices[0].delta.content
            if content_chunk:
                yield content_chunk

            tool_call_chunk = event.choices[0].delta.tool_calls
            if tool_call_chunk:
                tool_index = tool_call_chunk[0].index
                tool_id = tool_call_chunk[0].id
                tool_name = tool_call_chunk[0].function.name
                tool_arguments = tool_call_chunk[0].function.arguments

                if current_index != tool_index:
                    tool_calls.append(
                        OpenAIToolCall(
                            id=current_id,
                            type="function",
                            function=OpenAIToolCallFunction(
                                name=current_name,
                                arguments=current_arguments,
                            ),
                        )
                    )
                    current_index = tool_index
                    current_id = tool_id
                    current_name = tool_name
                    current_arguments = tool_arguments
                else:
                    current_name = tool_name or current_name
                    current_id = tool_id or current_id
                    if current_arguments is None:
                        current_arguments = tool_arguments
                    elif tool_arguments:
                        current_arguments += tool_arguments

        if current_id is not None:
            tool_calls.append(
                OpenAIToolCall(
                    id=current_id,
                    type="function",
                    function=OpenAIToolCallFunction(
                        name=current_name,
                        arguments=current_arguments,
                    ),
                )
            )

        if tool_calls:
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_openai(
                tool_calls
            )
            new_messages = [
                *messages,
                OpenAIAssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[each.model_dump() for each in tool_calls],
                ),
                *tool_call_messages,
            ]
            async for event in self._stream_openai(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                extra_body=extra_body,
                depth=depth + 1,
            ):
                yield event

    async def _stream_google(
        self,
        model: str,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        client: genai.Client = self._client

        google_tools = None
        if tools:
            google_tools = [GoogleTool(function_declarations=[tool]) for tool in tools]

        generated_contents = []
        tool_calls: List[GoogleToolCall] = []
        async for event in iterator_to_async(client.models.generate_content_stream)(
            model=model,
            contents=self._get_google_messages(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="text/plain",
                tools=google_tools,
                max_output_tokens=max_tokens,
            ),
        ):
            if not (
                event.candidates
                and event.candidates[0].content
                and event.candidates[0].content.parts
            ):
                continue

            generated_contents.append(event.candidates[0].content)

            for each_part in event.candidates[0].content.parts:
                if each_part.text:
                    yield each_part.text

                if each_part.function_call:
                    tool_calls.append(
                        GoogleToolCall(
                            id=each_part.function_call.id,
                            name=each_part.function_call.name,
                            arguments=each_part.function_call.args,
                        )
                    )

        if tool_calls:
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_google(
                tool_calls
            )
            new_messages = [
                *messages,
                *[
                    GoogleAssistantMessage(
                        role="assistant",
                        content=each,
                    )
                    for each in generated_contents
                ],
                *tool_call_messages,
            ]
            async for event in self._stream_google(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            ):
                yield event

    async def _stream_anthropic(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ):
        client: AsyncAnthropic = self._client

        tool_calls: List[AnthropicToolCall] = []
        async with client.messages.stream(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_anthropic_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
            tools=tools,
        ) as stream:
            async for event in stream:
                event: AnthropicMessageStreamEvent = event

                if event.type == "text":
                    yield event.text

                if (
                    event.type == "content_block_stop"
                    and event.content_block.type == "tool_use"
                ):
                    tool_calls.append(
                        AnthropicToolCall(
                            id=event.content_block.id,
                            type=event.content_block.type,
                            name=event.content_block.name,
                            input=event.content_block.input,
                        )
                    )

        if tool_calls:
            tool_call_messages = (
                await self.tool_calls_handler.handle_tool_calls_anthropic(tool_calls)
            )
            new_messages = [
                *messages,
                AnthropicAssistantMessage(
                    role="assistant",
                    content=[each.model_dump() for each in tool_calls],
                ),
                AnthropicUserMessage(
                    role="user",
                    content=[each.model_dump() for each in tool_call_messages],
                ),
            ]
            async for event in self._stream_anthropic(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                tools=tools,
                depth=depth + 1,
            ):
                yield event

    def _stream_ollama(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        return self._stream_openai(
            model=model, messages=messages, max_tokens=max_tokens, depth=depth
        )

    def _stream_custom(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        extra_body = {"enable_thinking": False} if self.disable_thinking() else None
        return self._stream_openai(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            extra_body=extra_body,
            depth=depth,
        )

    def stream(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        tools: Optional[List[type[LLMTool] | LLMDynamicTool]] = None,
    ):
        parsed_tools = self.tool_calls_handler.parse_tools(tools)

        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._stream_openai(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.CODEX:
                return self._stream_codex(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.GOOGLE:
                return self._stream_google(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=parsed_tools,
                )
            case LLMProvider.OLLAMA:
                return self._stream_ollama(
                    model=model, messages=messages, max_tokens=max_tokens
                )
            case LLMProvider.CUSTOM:
                return self._stream_custom(
                    model=model, messages=messages, max_tokens=max_tokens
                )

    # ? Stream Structured Content
    async def _stream_openai_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        extra_body: Optional[dict] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        client: AsyncOpenAI = self._client

        response_schema = response_format
        all_tools = [*tools] if tools else None

        use_tool_calls_for_structured_output = (
            self.use_tool_calls_for_structured_output()
        )
        if strict and depth == 0:
            response_schema = ensure_strict_json_schema(
                response_schema,
                path=(),
                root=response_schema,
            )

        if use_tool_calls_for_structured_output and depth == 0:
            if all_tools is None:
                all_tools = []
            all_tools.append(
                self.tool_calls_handler.parse_tool(
                    LLMDynamicTool(
                        name="ResponseSchema",
                        description="Provide response to the user",
                        parameters=response_schema,
                        handler=do_nothing_async,
                    ),
                    strict=strict,
                )
            )

        tool_calls: List[LLMToolCall] = []
        current_index = 0
        current_id = None
        current_name = None
        current_arguments = None

        has_response_schema_tool_call = False
        async for event in await client.chat.completions.create(
            model=model,
            messages=[message.model_dump() for message in messages],
            max_completion_tokens=max_tokens,
            tools=all_tools,
            response_format=(
                {
                    "type": "json_schema",
                    "json_schema": (
                        {
                            "name": "ResponseSchema",
                            "strict": strict,
                            "schema": response_schema,
                        }
                    ),
                }
                if not use_tool_calls_for_structured_output
                else None
            ),
            extra_body=extra_body,
            stream=True,
        ):
            event: OpenAIChatCompletionChunk = event
            if not event.choices:
                continue

            content_chunk = event.choices[0].delta.content
            if content_chunk and not use_tool_calls_for_structured_output:
                yield content_chunk

            tool_call_chunk = event.choices[0].delta.tool_calls
            if tool_call_chunk:
                tool_index = tool_call_chunk[0].index
                tool_id = tool_call_chunk[0].id
                tool_name = tool_call_chunk[0].function.name
                tool_arguments = tool_call_chunk[0].function.arguments

                if current_index != tool_index:
                    tool_calls.append(
                        OpenAIToolCall(
                            id=current_id,
                            type="function",
                            function=OpenAIToolCallFunction(
                                name=current_name,
                                arguments=current_arguments,
                            ),
                        )
                    )
                    current_index = tool_index
                    current_id = tool_id
                    current_name = tool_name
                    current_arguments = tool_arguments
                else:
                    current_name = tool_name or current_name
                    current_id = tool_id or current_id
                    if current_arguments is None:
                        current_arguments = tool_arguments
                    elif tool_arguments:
                        current_arguments += tool_arguments

                if current_name == "ResponseSchema":
                    if tool_arguments:
                        yield tool_arguments
                    has_response_schema_tool_call = True

        if current_id is not None:
            tool_calls.append(
                OpenAIToolCall(
                    id=current_id,
                    type="function",
                    function=OpenAIToolCallFunction(
                        name=current_name,
                        arguments=current_arguments,
                    ),
                )
            )

        if tool_calls and not has_response_schema_tool_call:
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_openai(
                tool_calls
            )
            new_messages = [
                *messages,
                OpenAIAssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[each.model_dump() for each in tool_calls],
                ),
                *tool_call_messages,
            ]
            async for event in self._stream_openai_structured(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                strict=strict,
                tools=all_tools,
                response_format=response_schema,
                extra_body=extra_body,
                depth=depth + 1,
            ):
                yield event

    async def _stream_google_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:

        client: genai.Client = self._client

        google_tools = None
        if tools:
            google_tools = [GoogleTool(function_declarations=[tool]) for tool in tools]
            google_tools.append(
                GoogleTool(
                    function_declarations=[
                        {
                            "name": "ResponseSchema",
                            "description": "Provide response to the user",
                            "parameters": remove_titles_from_schema(
                                flatten_json_schema(response_format)
                            ),
                        }
                    ]
                )
            )

        parsed_messages = self._get_google_messages(messages)

        generated_contents = []
        tool_calls: List[GoogleToolCall] = []
        has_response_schema_tool_call = False
        async for event in iterator_to_async(client.models.generate_content_stream)(
            model=model,
            contents=parsed_messages,
            config=GenerateContentConfig(
                tools=google_tools,
                tool_config=(
                    GoogleToolConfig(
                        function_calling_config=GoogleFunctionCallingConfig(
                            mode=GoogleFunctionCallingConfigMode.ANY,
                        ),
                    )
                    if tools
                    else None
                ),
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="application/json" if not tools else None,
                response_json_schema=response_format if not tools else None,
                max_output_tokens=max_tokens,
            ),
        ):
            if not (
                event.candidates
                and event.candidates[0].content
                and event.candidates[0].content.parts
            ):
                continue

            generated_contents.append(event.candidates[0].content)

            for each_part in event.candidates[0].content.parts:
                if each_part.text and not google_tools:
                    yield each_part.text

                if each_part.function_call:
                    if each_part.function_call.name == "ResponseSchema":
                        has_response_schema_tool_call = True
                        if each_part.function_call.args:
                            yield json.dumps(each_part.function_call.args)

                    tool_calls.append(
                        GoogleToolCall(
                            id=each_part.function_call.id,
                            name=each_part.function_call.name,
                            arguments=each_part.function_call.args,
                        )
                    )

        if tool_calls and not has_response_schema_tool_call:
            tool_call_messages = await self.tool_calls_handler.handle_tool_calls_google(
                tool_calls
            )
            new_messages = [
                *messages,
                *[
                    GoogleAssistantMessage(
                        role="assistant",
                        content=each,
                    )
                    for each in generated_contents
                ],
                *tool_call_messages,
            ]
            async for event in self._stream_google_structured(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
                depth=depth + 1,
            ):
                yield event

    async def _stream_anthropic_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        tools: Optional[List[dict]] = None,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        client: AsyncAnthropic = self._client

        tool_calls: List[AnthropicToolCall] = []
        has_response_schema_tool_call = False
        async with client.messages.stream(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_anthropic_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
            tools=[
                {
                    "name": "ResponseSchema",
                    "description": "A response to the user's message",
                    "input_schema": response_format,
                },
                *(tools or []),
            ],
        ) as stream:
            is_response_schema_tool_call_started = False
            async for event in stream:
                event: AnthropicMessageStreamEvent = event

                if (
                    event.type == "content_block_start"
                    and event.content_block.type == "tool_use"
                ):
                    if event.content_block.name == "ResponseSchema":
                        has_response_schema_tool_call = True
                        is_response_schema_tool_call_started = True

                if (
                    event.type == "content_block_delta"
                    and event.delta.type == "input_json_delta"
                    and is_response_schema_tool_call_started
                ):
                    yield event.delta.partial_json

                if (
                    event.type == "content_block_stop"
                    and event.content_block.type == "tool_use"
                ):
                    tool_calls.append(
                        AnthropicToolCall(
                            id=event.content_block.id,
                            type=event.content_block.type,
                            name=event.content_block.name,
                            input=event.content_block.input,
                        )
                    )

        if tool_calls and not has_response_schema_tool_call:
            tool_call_messages = (
                await self.tool_calls_handler.handle_tool_calls_anthropic(tool_calls)
            )
            new_messages = [
                *messages,
                AnthropicAssistantMessage(
                    role="assistant",
                    content=[each.model_dump() for each in tool_calls],
                ),
                AnthropicUserMessage(
                    role="user",
                    content=[each.model_dump() for each in tool_call_messages],
                ),
            ]
            async for event in self._stream_anthropic_structured(
                model=model,
                messages=new_messages,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
                depth=depth + 1,
            ):
                yield event

    def _stream_ollama_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        return self._stream_openai_structured(
            model=model,
            messages=messages,
            response_format=response_format,
            strict=strict,
            max_tokens=max_tokens,
            depth=depth,
        )

    def _stream_custom_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        extra_body = {"enable_thinking": False} if self.disable_thinking() else None
        return self._stream_openai_structured(
            model=model,
            messages=messages,
            response_format=response_format,
            strict=strict,
            max_tokens=max_tokens,
            extra_body=extra_body,
            depth=depth,
        )

    def stream_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        tools: Optional[List[type[LLMTool] | LLMDynamicTool]] = None,
        max_tokens: Optional[int] = None,
    ):
        parsed_tools = self.tool_calls_handler.parse_tools(tools)

        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._stream_openai_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.CODEX:
                return self._stream_codex_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.GOOGLE:
                return self._stream_google_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    tools=parsed_tools,
                    max_tokens=max_tokens,
                )
            case LLMProvider.OLLAMA:
                return self._stream_ollama_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    max_tokens=max_tokens,
                )
            case LLMProvider.CUSTOM:
                return self._stream_custom_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    strict=strict,
                    max_tokens=max_tokens,
                )

    # ? Web search
    async def _search_openai(self, query: str) -> str:
        client: AsyncOpenAI = self._client
        response = await client.responses.create(
            model=get_model(),
            tools=[
                {
                    "type": "web_search_preview",
                }
            ],
            input=query,
        )
        return response.output_text

    async def _search_google(self, query: str) -> str:
        client: genai.Client = self._client
        grounding_tool = GoogleTool(google_search=GoogleSearch())
        config = GenerateContentConfig(tools=[grounding_tool])

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=get_model(),
            contents=query,
            config=config,
        )
        return response.text

    async def _search_anthropic(self, query: str) -> str:
        client: AsyncAnthropic = self._client

        response = await client.messages.create(
            model=get_model(),
            max_tokens=4000,
            messages=[{"role": "user", "content": query}],
            tools=[
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
            ],
        )
        result = "\n".join(
            [each.text for each in response.content if each.type == "text"]
        )
        return result
