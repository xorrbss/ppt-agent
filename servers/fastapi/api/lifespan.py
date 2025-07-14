from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from sqlmodel import SQLModel

from constants.supported_ollama_models import SUPPORTED_OLLAMA_MODELS
from enums.llm_provider import LLMProvider
from services import SQL_ENGINE
from utils.custom_llm_provider import list_available_custom_models
from utils.llm_provider import (
    get_llm_provider,
    is_custom_llm_selected,
    is_ollama_selected,
)
from utils.ollama import pull_ollama_model


can_change_keys = os.getenv("CAN_CHANGE_KEYS") != "false"


async def check_llm_model_availability():
    if not can_change_keys:
        if get_llm_provider() == LLMProvider.OPENAI:
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                raise Exception("OPENAI_API_KEY must be provided")

        elif get_llm_provider() == LLMProvider.GOOGLE:
            google_api_key = os.getenv("GOOGLE_API_KEY")
            if not google_api_key:
                raise Exception("GOOGLE_API_KEY must be provided")

        elif is_ollama_selected():
            ollama_model = os.getenv("OLLAMA_MODEL")
            if not ollama_model:
                raise Exception("OLLAMA_MODEL must be provided")

            if ollama_model not in SUPPORTED_OLLAMA_MODELS:
                raise Exception(f"Model {ollama_model} is not supported")

            print("-" * 50)
            print("Pulling model: ", ollama_model)
            async for event in pull_ollama_model(ollama_model):
                print(event)
            print("Pulled model: ", ollama_model)
            print("-" * 50)

        elif is_custom_llm_selected():
            custom_model = os.getenv("CUSTOM_MODEL")
            custom_llm_url = os.getenv("CUSTOM_LLM_URL")
            custom_llm_api_key = os.getenv("CUSTOM_LLM_API_KEY")
            if not custom_model:
                raise Exception("CUSTOM_MODEL must be provided")
            if not custom_llm_url:
                raise Exception("CUSTOM_LLM_URL must be provided")
            if not custom_llm_api_key:
                raise Exception("CUSTOM_LLM_API_KEY must be provided")
            print("-" * 50)
            print("Selecting model: ", custom_model)
            models = await list_available_custom_models(
                custom_llm_url, custom_llm_api_key
            )
            print("Available models: ", models)
            print("-" * 50)
            if custom_model not in models:
                raise Exception(f"Model {custom_model} is not available")


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    os.makedirs(os.getenv("APP_DATA_DIRECTORY"), exist_ok=True)
    SQLModel.metadata.create_all(SQL_ENGINE)
    await check_llm_model_availability()
    yield
