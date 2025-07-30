import asyncio
import json
from typing import List
from fastapi import HTTPException
from openai import AsyncOpenAI
from google import genai
from google.genai.types import GenerateContentConfig
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic import MessageStreamEvent as AnthropicMessageStreamEvent
from pydantic import BaseModel
from enums.llm_provider import LLMProvider
from models.llm_message import LLMMessage
from utils.async_iterator import iterator_to_async
from utils.get_env import (
    get_anthropic_api_key_env,
    get_custom_llm_api_key_env,
    get_custom_llm_url_env,
    get_google_api_key_env,
    get_ollama_url_env,
    get_openai_api_key_env,
)
from utils.llm_provider import get_llm_provider


class LLMClient:
    def __init__(self):
        self.llm_provider = get_llm_provider()
        self.client = self._get_client()

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
        if not (get_custom_llm_api_key_env() and get_custom_llm_url_env()):
            raise HTTPException(
                status_code=400,
                detail="Custom LLM API Key is not set",
            )
        return AsyncOpenAI(
            base_url=get_custom_llm_url_env(),
            api_key=get_custom_llm_api_key_env(),
        )

    # ? Prompts
    def _get_system_prompt(self, messages: List[LLMMessage]) -> str:
        for message in messages:
            if message.role == "system":
                return message.content
        return ""

    def _get_user_prompts(self, messages: List[LLMMessage]) -> List[str]:
        return [message.content for message in messages if message.role == "user"]

    # ? Generate Unstructured Content
    async def _generate_openai(self, model: str, messages: List[LLMMessage]):
        client: AsyncOpenAI = self.client
        response = await client.chat.completions.create(
            model=model,
            messages=[message.model_dump() for message in messages],
        )
        return response.choices[0].message.content

    async def _generate_google(self, model: str, messages: List[LLMMessage]):
        client: genai.Client = self.client
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="text/plain",
            ),
        )
        return response.text

    async def _generate_anthropic(self, model: str, messages: List[LLMMessage]):
        client: AsyncAnthropic = self.client
        response: AnthropicMessage = await client.messages.create(
            model=model,
            messages=[message.model_dump() for message in messages],
        )
        text = ""
        for content in response.content:
            if content.type == "text" and isinstance(content.text, str):
                text += content.text
        if text == "":
            return None
        return text

    async def _generate_ollama(self, model: str, messages: List[LLMMessage]):
        return await self._generate_openai(model, messages)

    async def _generate_custom(self, model: str, messages: List[LLMMessage]):
        return await self._generate_openai(model, messages)

    async def generate(self, model: str, messages: List[LLMMessage]):
        content = None
        match self.llm_provider:
            case LLMProvider.OPENAI:
                content = await self._generate_openai(model, messages)
            case LLMProvider.GOOGLE:
                content = await self._generate_google(model, messages)
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic(model, messages)
            case LLMProvider.OLLAMA:
                content = await self._generate_ollama(model, messages)
            case LLMProvider.CUSTOM:
                content = await self._generate_custom(model, messages)
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="LLM Provider must be either openai, google, anthropic, ollama, or custom",
                )
        if content is None:
            raise HTTPException(
                status_code=400,
                detail="LLM did not return any content",
            )
        return content

    # ? Generate Structured Content
    async def _generate_openai_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        client: AsyncOpenAI = self.client
        is_response_format_dict = isinstance(response_format, dict)
        if is_response_format_dict:
            response = await client.chat.completions.create(
                model=model,
                messages=[message.model_dump() for message in messages],
                response_format={
                    "type": "json_schema",
                    "json_schema": (
                        {
                            "name": "ResponseSchema",
                            "schema": response_format,
                        }
                    ),
                },
            )
            content = response.choices[0].message.content
            if content:
                return json.loads(content)
            return None
        else:
            response = await client.chat.completions.parse(
                model=model,
                messages=[message.model_dump() for message in messages],
                response_format=response_format,
            )
            content = response.choices[0].message.parsed
            if content:
                return content
            return None

    async def _generate_google_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        client: genai.Client = self.client
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="application/json",
                response_schema=response_format,
            ),
        )
        if response.text:
            return json.loads(response.text)
        return None

    async def _generate_anthropic_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        client: AsyncAnthropic = self.client
        is_response_format_dict = isinstance(response_format, dict)
        response: AnthropicMessage = await client.messages.create(
            model=model,
            messages=[message.model_dump() for message in messages],
            tools=[
                {
                    "name": "ResponseSchema",
                    "description": "A response to the user's message",
                    "input_schema": (
                        response_format
                        if is_response_format_dict
                        else response_format.model_json_schema()
                    ),
                }
            ],
        )
        content: dict | None = None
        for content_block in response.content:
            if content_block.type == "tool_use":
                content = content_block.input
        return content

    async def _generate_ollama_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        return await self._generate_openai_structured(model, messages, response_format)

    async def _generate_custom_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        return await self._generate_openai_structured(model, messages, response_format)

    async def generate_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        content = None
        match self.llm_provider:
            case LLMProvider.OPENAI:
                content = await self._generate_openai_structured(
                    model, messages, response_format
                )
            case LLMProvider.GOOGLE:
                content = await self._generate_google_structured(
                    model, messages, response_format
                )
            case LLMProvider.ANTHROPIC:
                content = await self._generate_anthropic_structured(
                    model, messages, response_format
                )
            case LLMProvider.OLLAMA:
                content = await self._generate_ollama_structured(
                    model, messages, response_format
                )
            case LLMProvider.CUSTOM:
                content = await self._generate_custom_structured(
                    model, messages, response_format
                )
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="LLM Provider must be either openai, google, anthropic, ollama, or custom",
                )
        if content is None:
            raise HTTPException(
                status_code=400,
                detail="LLM did not return any content",
            )
        return content

    # ? Stream Unstructured Content
    async def _stream_openai(self, model: str, messages: List[LLMMessage]):
        client: AsyncOpenAI = self.client
        async with client.chat.completions.stream(
            model=model,
            messages=[message.model_dump() for message in messages],
        ) as stream:
            async for event in stream:
                if event.type == "content.delta":
                    yield event.delta

    async def _stream_google(self, model: str, messages: List[LLMMessage]):
        client: genai.Client = self.client
        async for event in iterator_to_async(client.models.generate_content_stream)(
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="text/plain",
            ),
        ):
            if event.text:
                yield event.text

    async def _stream_anthropic(self, model: str, messages: List[LLMMessage]):
        client: AsyncAnthropic = self.client
        async with client.messages.stream(
            model=model,
            messages=[message.model_dump() for message in messages],
        ) as stream:
            async for event in stream:
                event: AnthropicMessageStreamEvent = event
                if event.type == "text" and isinstance(event.text, str):
                    yield event.text

    def _stream_ollama(self, model: str, messages: List[LLMMessage]):
        return self._stream_openai(model, messages)

    def _stream_custom(self, model: str, messages: List[LLMMessage]):
        return self._stream_openai(model, messages)

    async def stream(self, model: str, messages: List[LLMMessage]):
        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._stream_openai(model, messages)
            case LLMProvider.GOOGLE:
                return self._stream_google(model, messages)
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic(model, messages)
            case LLMProvider.OLLAMA:
                return self._stream_ollama(model, messages)
            case LLMProvider.CUSTOM:
                return self._stream_custom(model, messages)
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="LLM Provider must be either openai, google, anthropic, ollama, or custom",
                )

    # ? Stream Structured Content
    async def _stream_openai_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        client: AsyncOpenAI = self.client
        is_response_format_dict = isinstance(response_format, dict)
        async with client.chat.completions.stream(
            model=model,
            messages=[message.model_dump() for message in messages],
            response_format={
                "type": "json_schema",
                "json_schema": (
                    {
                        "name": "ResponseSchema",
                        "schema": response_format,
                    }
                    if is_response_format_dict
                    else response_format
                ),
            },
        ) as stream:
            async for event in stream:
                if event.type == "content.delta":
                    yield event.delta

    async def _stream_google_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        client: genai.Client = self.client
        async for event in iterator_to_async(client.models.generate_content_stream)(
            model=model,
            contents=self._get_user_prompts(messages),
            config=GenerateContentConfig(
                system_instruction=self._get_system_prompt(messages),
                response_mime_type="application/json",
                response_schema=response_format,
            ),
        ):
            if event.text:
                yield event.text

    async def _stream_anthropic_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        client: AsyncAnthropic = self.client
        is_response_format_dict = isinstance(response_format, dict)
        async with client.messages.stream(
            model=model,
            messages=[message.model_dump() for message in messages],
            tools=[
                {
                    "name": "ResponseSchema",
                    "description": "A response to the user's message",
                    "input_schema": (
                        response_format
                        if is_response_format_dict
                        else response_format.model_json_schema()
                    ),
                }
            ],
        ) as stream:
            async for event in stream:
                event: AnthropicMessageStreamEvent = event
                if event.type == "input_json" and isinstance(event.partial_json, str):
                    yield event.partial_json

    def _stream_ollama_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        return self._stream_openai_structured(model, messages, response_format)

    def _stream_custom_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        return self._stream_openai_structured(model, messages, response_format)

    async def stream_structured(
        self, model: str, messages: List[LLMMessage], response_format: BaseModel | dict
    ):
        match self.llm_provider:
            case LLMProvider.OPENAI:
                return self._stream_openai_structured(model, messages, response_format)
            case LLMProvider.GOOGLE:
                return self._stream_google_structured(model, messages, response_format)
            case LLMProvider.ANTHROPIC:
                return self._stream_anthropic_structured(
                    model, messages, response_format
                )
            case LLMProvider.OLLAMA:
                return self._stream_ollama_structured(model, messages, response_format)
            case LLMProvider.CUSTOM:
                return self._stream_custom_structured(model, messages, response_format)
            case _:
                raise HTTPException(
                    status_code=400,
                    detail="LLM Provider must be either openai, google, anthropic, ollama, or custom",
                )
