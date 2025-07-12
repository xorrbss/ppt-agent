import json
import os
from typing import AsyncGenerator, Optional

import aiohttp
from fastapi import HTTPException
from openai import AsyncOpenAI
import openai

from api.models import SelectedLLMProvider
from api.routers.presentation.models import OllamaModelStatusResponse


def is_ollama_selected() -> bool:
    return get_selected_llm_provider() == SelectedLLMProvider.OLLAMA


def is_custom_llm_selected() -> bool:
    return get_selected_llm_provider() == SelectedLLMProvider.CUSTOM


def get_llm_provider_url_or():
    llm_provider_url = (
        os.getenv("OLLAMA_URL") if is_ollama_selected() else os.getenv("CUSTOM_LLM_URL")
    )
    llm_provider_url = llm_provider_url or "http://localhost:11434"
    if llm_provider_url.endswith("/"):
        return llm_provider_url[:-1]
    return llm_provider_url


def get_selected_llm_provider() -> SelectedLLMProvider:
    return SelectedLLMProvider(os.getenv("LLM"))


async def list_available_custom_models(
    url: Optional[str] = None, api_key: Optional[str] = None
) -> list[str]:
    if not url:
        client = get_llm_client()
    else:
        client = openai.AsyncOpenAI(api_key=api_key or "null", base_url=url)
    models = []
    async for model in client.models.list():
        print(model)
        models.append(model.id)
    return models


def get_model_base_url():
    selected_llm = get_selected_llm_provider()

    if selected_llm == SelectedLLMProvider.OPENAI:
        return "https://api.openai.com/v1"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "https://generativelanguage.googleapis.com/v1beta/openai"
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return os.path.join(get_llm_provider_url_or(), "v1")
    elif selected_llm == SelectedLLMProvider.CUSTOM:
        return get_llm_provider_url_or()
    else:
        raise ValueError(f"Invalid LLM provider")


def get_llm_api_key():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return os.getenv("OPENAI_API_KEY")
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return os.getenv("GOOGLE_API_KEY")
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return "ollama"
    elif selected_llm == SelectedLLMProvider.CUSTOM:
        return os.getenv("CUSTOM_LLM_API_KEY") or "null"
    else:
        raise ValueError(f"Invalid LLM API key")


def get_llm_client():
    client = AsyncOpenAI(
        base_url=get_model_base_url(),
        api_key=get_llm_api_key(),
    )
    return client


def get_large_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return os.getenv("OLLAMA_MODEL")
    elif selected_llm == SelectedLLMProvider.CUSTOM:
        return os.getenv("CUSTOM_MODEL")
    else:
        raise ValueError(f"Invalid LLM model")


def get_small_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1-mini"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return os.getenv("OLLAMA_MODEL")
    elif selected_llm == SelectedLLMProvider.CUSTOM:
        return os.getenv("CUSTOM_MODEL")
    else:
        raise ValueError(f"Invalid LLM model")


def get_nano_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1-nano"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return os.getenv("OLLAMA_MODEL")
    elif selected_llm == SelectedLLMProvider.CUSTOM:
        return os.getenv("CUSTOM_MODEL")
    else:
        raise ValueError(f"Invalid LLM model")


async def list_pulled_ollama_models() -> list[OllamaModelStatusResponse]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{get_llm_provider_url_or()}/api/tags",
        ) as response:
            if response.status == 200:
                pulled_models = await response.json()
                return [
                    OllamaModelStatusResponse(
                        name=m["model"],
                        size=m["size"],
                        status="pulled",
                        downloaded=m["size"],
                        done=True,
                    )
                    for m in pulled_models["models"]
                ]
            elif response.status == 403:
                raise HTTPException(
                    status_code=403,
                    detail="Forbidden: Please check your Ollama Configuration",
                )
            else:
                raise HTTPException(
                    status_code=response.status,
                    detail=f"Failed to list Ollama models: {response.status}",
                )


async def pull_ollama_model(model: str) -> AsyncGenerator[dict, None]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{get_llm_provider_url_or()}/api/pull",
            json={"model": model},
        ) as response:
            if response.status != 200:
                raise HTTPException(
                    status_code=response.status,
                    detail=f"Failed to pull model: {await response.text()}",
                )

            async for line in response.content:
                if not line.strip():
                    continue

                try:
                    event = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                yield event
