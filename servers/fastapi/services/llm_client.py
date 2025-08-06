import asyncio
import json
from typing import List, Optional
from fastapi import HTTPException
from openai import AsyncOpenAI
from google import genai
from google.genai.types import GenerateContentConfig
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic import MessageStreamEvent as AnthropicMessageStreamEvent
from enums.llm_provider import LLMProvider
from models.llm_message import LLMMessage
from utils.async_iterator import iterator_to_async
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
from utils.schema_utils import ensure_strict_json_schema


class LLMClient:
    def __init__(self):
        self.llm_provider = get_llm_provider()
        self._client = self._get_client()

    # ? Use tool calls
    def use_tool_calls(self) -> bool:
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
            if message.role == "system":
                return message.content
        return ""

    def _get_user_prompts(self, messages: List[LLMMessage]) -> List[str]:
        return [message.content for message in messages if message.role == "user"]

    def _get_user_llm_messages(self, messages: List[LLMMessage]) -> List[LLMMessage]:
        return [message for message in messages if message.role == "user"]

    # ? Generate Unstructured Content
    async def _generate_openai(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ):
        client: AsyncOpenAI = self._client
        response = await client.chat.completions.create(
            model=model,
            messages=[message.model_dump() for message in messages],
            max_completion_tokens=max_tokens,
            extra_body=extra_body,
        )
        return response.choices[0].message.content

    async def _generate_google(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
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
        self, model: str, messages: List[LLMMessage], max_tokens: Optional[int] = None
    ):
        return await self._generate_openai(model, messages, max_tokens)

    async def _generate_custom(
        self, model: str, messages: List[LLMMessage], max_tokens: Optional[int] = None
    ):
        extra_body = {"enable_thinking": not self.disable_thinking()}
        return await self._generate_openai(
            model, messages, max_tokens, extra_body=extra_body
        )

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
    ):
        content = None
        match self.llm_provider:
            case LLMProvider.OPENAI:
                content = await self._generate_openai(model, messages, max_tokens)
            case LLMProvider.GOOGLE:
                content = await self._generate_google(model, messages, max_tokens)
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic(model, messages, max_tokens)
            case LLMProvider.OLLAMA:
                content = await self._generate_ollama(model, messages, max_tokens)
            case LLMProvider.CUSTOM:
                content = await self._generate_custom(model, messages, max_tokens)
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
        extra_body: Optional[dict] = None,
    ):
        client: AsyncOpenAI = self._client
        use_tool_calls = self.use_tool_calls()
        response_schema = response_format
        if strict:
            response_schema = ensure_strict_json_schema(
                response_schema,
                path=(),
                root=response_schema,
            )
        if not use_tool_calls:
            response = await client.chat.completions.create(
                model=model,
                messages=[message.model_dump() for message in messages],
                response_format={
                    "type": "json_schema",
                    "json_schema": (
                        {
                            "name": "ResponseSchema",
                            "strict": strict,
                            "schema": response_schema,
                        }
                    ),
                },
                max_completion_tokens=max_tokens,
                extra_body=extra_body,
            )
            content = response.choices[0].message.content
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=[message.model_dump() for message in messages],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "ResponseSchema",
                            "description": "A response to the user's message",
                            "strict": strict,
                            "parameters": response_format,
                        },
                    }
                ],
                tool_choice="required",
                max_completion_tokens=max_tokens,
                extra_body=extra_body,
            )
            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                content = tool_calls[0].function.arguments

        if content:
            return json.loads(content)
        return None

    async def _generate_google_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
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
    ):
        return await self._generate_openai_structured(
            model, messages, response_format, strict, max_tokens
        )

    async def _generate_custom_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
    ):
        extra_body = {"enable_thinking": not self.disable_thinking()}
        return await self._generate_openai_structured(
            model, messages, response_format, strict, max_tokens, extra_body
        )

    async def generate_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
    ) -> dict:
        content = None
        match self.llm_provider:
            case LLMProvider.OPENAI:
                content = await self._generate_openai_structured(
                    model, messages, response_format, strict, max_tokens
                )
            case LLMProvider.GOOGLE:
                content = await self._generate_google_structured(
                    model, messages, response_format, max_tokens
                )
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic_structured(
                    model, messages, response_format, max_tokens
                )
            case LLMProvider.OLLAMA:
                content = await self._generate_ollama_structured(
                    model, messages, response_format, strict, max_tokens
                )
            case LLMProvider.CUSTOM:
                content = await self._generate_custom_structured(
                    model, messages, response_format, strict, max_tokens
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
        extra_body: Optional[dict] = None,
    ):
        client: AsyncOpenAI = self._client
        async with client.chat.completions.stream(
            model=model,
            messages=[message.model_dump() for message in messages],
            max_completion_tokens=max_tokens,
            extra_body=extra_body,
        ) as stream:
            async for event in stream:
                if event.type == "content.delta":
                    yield event.delta

    async def _stream_google(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
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
    ):
        return self._stream_openai(model, messages, max_tokens)

    def _stream_custom(
        self,
        model: str,
        messages: List[LLMMessage],
        max_tokens: Optional[int] = None,
    ):
        extra_body = {"enable_thinking": not self.disable_thinking()}
        return self._stream_openai(model, messages, max_tokens, extra_body)

    def stream(
        self, model: str, messages: List[LLMMessage], max_tokens: Optional[int] = None
    ):
        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._stream_openai(model, messages, max_tokens)
            case LLMProvider.GOOGLE:
                return self._stream_google(model, messages, max_tokens)
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic(model, messages, max_tokens)
            case LLMProvider.OLLAMA:
                return self._stream_ollama(model, messages, max_tokens)
            case LLMProvider.CUSTOM:
                return self._stream_custom(model, messages, max_tokens)

    # ? Stream Structured Content
    async def _stream_openai_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ):
        client: AsyncOpenAI = self._client
        use_tool_calls = self.use_tool_calls()
        response_schema = response_format
        if strict:
            response_schema = ensure_strict_json_schema(
                response_schema,
                path=(),
                root=response_schema,
            )
        if not use_tool_calls:
            async with client.chat.completions.stream(
                model=model,
                messages=[message.model_dump() for message in messages],
                max_completion_tokens=max_tokens,
                response_format=(
                    {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "ResponseSchema",
                            "strict": strict,
                            "schema": response_schema,
                        },
                    }
                ),
                extra_body=extra_body,
            ) as stream:
                async for event in stream:
                    if event.type == "content.delta":
                        yield event.delta
        else:
            async with client.chat.completions.stream(
                model=model,
                messages=[message.model_dump() for message in messages],
                max_completion_tokens=max_tokens,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "ResponseSchema",
                            "description": "A response to the user's message",
                            "strict": strict,
                            "parameters": response_format,
                        },
                    }
                ],
                tool_choice="required",
                extra_body=extra_body,
            ) as stream:
                async for event in stream:
                    if event.type == "tool_calls.function.arguments.delta":
                        yield event.arguments_delta

    async def _stream_google_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        max_tokens: Optional[int] = None,
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
    ):
        return self._stream_openai_structured(
            model, messages, response_format, strict, max_tokens
        )

    def _stream_custom_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
    ):
        extra_body = {"enable_thinking": not self.disable_thinking()}
        return self._stream_openai_structured(
            model, messages, response_format, strict, max_tokens, extra_body
        )

    def stream_structured(
        self,
        model: str,
        messages: List[LLMMessage],
        response_format: dict,
        strict: bool = False,
        max_tokens: Optional[int] = None,
    ):
        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._stream_openai_structured(
                    model, messages, response_format, strict, max_tokens
                )
            case LLMProvider.GOOGLE:
                return self._stream_google_structured(
                    model, messages, response_format, max_tokens
                )
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic_structured(
                    model, messages, response_format, max_tokens
                )
            case LLMProvider.OLLAMA:
                return self._stream_ollama_structured(
                    model, messages, response_format, strict, max_tokens
                )
            case LLMProvider.CUSTOM:
                return self._stream_custom_structured(
                    model, messages, response_format, strict, max_tokens
                )
