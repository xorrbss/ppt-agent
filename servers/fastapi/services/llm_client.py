import asyncio
import json
from typing import AsyncGenerator, List, Optional
from fastapi import HTTPException
from openai import AsyncOpenAI
from openai.types.chat.chat_completion_chunk import (
    ChatCompletionChunk as OpenAIChatCompletionChunk,
)
from google import genai
from google.genai.types import GenerateContentConfig
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic import MessageStreamEvent as AnthropicMessageStreamEvent
from enums.llm_provider import LLMProvider
from models.llm_message import (
    LLMAssistantMessage,
    LLMMessage,
    LLMSystemMessage,
    LLMUserMessage,
)
from models.llm_tool_call import LLMToolCall, OpenAIToolCall, OpenAIToolCallFunction
from models.llm_tools import LLMDynamicTool, LLMTool
from services.llm_tool_calls_handler import LLMToolCallsHandler
from utils.async_iterator import iterator_to_async
from utils.dummy_functions import do_nothing_async
from utils.get_env import (
    get_anthropic_api_key_env,
    get_custom_llm_api_key_env,
    get_custom_llm_url_env,
    get_disable_thinking_env,
    get_google_api_key_env,
    get_ollama_url_env,
    get_openai_api_key_env,
    get_tool_calls_env,
)
from utils.llm_provider import get_llm_provider
from utils.parsers import parse_bool_or_none
from utils.randomizers import get_random_uuid
from utils.schema_utils import ensure_strict_json_schema


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
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="LLM Provider must be either openai, google, anthropic, ollama, or custom",
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

    # ? Prompts
    def _get_system_prompt(self, messages: List[LLMMessage]) -> str:
        for message in messages:
            if isinstance(message, LLMSystemMessage):
                return message.content
        return ""

    def _get_user_prompts(self, messages: List[LLMMessage]) -> List[str]:
        return [
            message.content
            for message in messages
            if isinstance(message, LLMUserMessage)
        ]

    def _get_user_llm_messages(self, messages: List[LLMMessage]) -> List[LLMMessage]:
        return [message for message in messages if isinstance(message, LLMUserMessage)]

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
            assistant_message = LLMAssistantMessage(
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
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: genai.Client = self._client
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="text/plain",
                max_output_tokens=max_tokens,
            ),
        )
        return response.text

    async def _generate_anthropic(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: AsyncAnthropic = self._client
        response: AnthropicMessage = await client.messages.create(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_user_llm_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
        )
        text = ""
        for content in response.content:
            if content.type == "text" and isinstance(content.text, str):
                text += content.text
        if text == "":
            return None
        return text

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
        extra_body = {"enable_thinking": not self.disable_thinking()}
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
            case LLMProvider.GOOGLE:
                content = await self._generate_google(
                    model=model, messages=messages, max_tokens=max_tokens
                )
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic(
                    model=model, messages=messages, max_tokens=max_tokens
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
                        strict=strict,
                        parameters=response_schema,
                        handler=do_nothing_async,
                    )
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
                    LLMAssistantMessage(
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
                return json.loads(content)
            return content
        return None

    async def _generate_google_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: genai.Client = self._client
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="application/json",
                response_json_schema=response_format,
                max_output_tokens=max_tokens,
            ),
        )
        content = None
        if response.text:
            content = json.loads(response.text)

        return content

    async def _generate_anthropic_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: AsyncAnthropic = self._client
        response: AnthropicMessage = await client.messages.create(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_user_llm_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
            tools=[
                {
                    "name": "ResponseSchema",
                    "description": "A response to the user's message",
                    "input_schema": response_format,
                }
            ],
            tool_choice={
                "type": "tool",
                "name": "ResponseSchema",
            },
        )
        content: dict | None = None
        for content_block in response.content:
            if content_block.type == "tool_use":
                content = content_block.input

        return content

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
        extra_body = {"enable_thinking": not self.disable_thinking()}
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
            case LLMProvider.GOOGLE:
                content = await self._generate_google_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=max_tokens,
                )
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
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
                    else:
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
                LLMAssistantMessage(
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
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: genai.Client = self._client
        async for event in iterator_to_async(client.models.generate_content_stream)(
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="text/plain",
                max_output_tokens=max_tokens,
            ),
        ):
            if event.text:
                yield event.text

    async def _stream_anthropic(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: AsyncAnthropic = self._client
        async with client.messages.stream(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_user_llm_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
        ) as stream:
            async for event in stream:
                event: AnthropicMessageStreamEvent = event
                if event.type == "text" and isinstance(event.text, str):
                    yield event.text

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
        extra_body = {"enable_thinking": not self.disable_thinking()}
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
            case LLMProvider.GOOGLE:
                return self._stream_google(
                    model=model, messages=messages, max_tokens=max_tokens
                )
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic(
                    model=model, messages=messages, max_tokens=max_tokens
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
                        strict=strict,
                        parameters=response_schema,
                        handler=do_nothing_async,
                    )
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
                    else:
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
                LLMAssistantMessage(
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
        depth: int = 0,
    ):
        client: genai.Client = self._client
        async for event in iterator_to_async(client.models.generate_content_stream)(
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="application/json",
                response_json_schema=response_format,
                max_output_tokens=max_tokens,
            ),
        ):
            if event.text:
                yield event.text

    async def _stream_anthropic_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
        depth: int = 0,
    ):
        client: AsyncAnthropic = self._client
        async with client.messages.stream(
            model=model,
            system=self._get_system_prompt(messages),
            messages=[
                message.model_dump()
                for message in self._get_user_llm_messages(messages)
            ],
            max_tokens=max_tokens or 4000,
            tools=[
                {
                    "name": "ResponseSchema",
                    "description": "A response to the user's message",
                    "input_schema": response_format,
                }
            ],
            tool_choice={
                "type": "tool",
                "name": "ResponseSchema",
            },
        ) as stream:
            async for event in stream:
                event: AnthropicMessageStreamEvent = event
                if event.type == "input_json" and isinstance(event.partial_json, str):
                    yield event.partial_json

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
        extra_body = {"enable_thinking": not self.disable_thinking()}
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
            case LLMProvider.GOOGLE:
                return self._stream_google_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=max_tokens,
                )
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic_structured(
                    model=model,
                    messages=messages,
                    response_format=response_format,
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
    def _search_openai(self, query: str) -> str:
        client: AsyncOpenAI = self._client
        response = client.responses.create(
            model="o4-mini",
            tools=[
                {
                    "type": "web_search_preview",
                    "user_location": {
                        "type": "approximate",
                        "country": "GB",
                        "city": "London",
                        "region": "London",
                    },
                }
            ],
            input="What are the best restaurants around Granary Square?",
        )
