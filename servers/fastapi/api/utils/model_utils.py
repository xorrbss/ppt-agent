import os

from openai import AsyncOpenAI

from api.models import SelectedLLMProvider


def is_ollama_selected() -> bool:
    return get_selected_llm_provider() == SelectedLLMProvider.OLLAMA


def get_selected_llm_provider() -> SelectedLLMProvider:
    return SelectedLLMProvider(os.getenv("LLM"))


def get_model_base_url():
    selected_llm = get_selected_llm_provider()

    if selected_llm == SelectedLLMProvider.OLLAMA:
        return "http://localhost:11434/v1"
    elif selected_llm == SelectedLLMProvider.OPENAI:
        return "https://api.openai.com/v1"
    else:
        return "https://generativelanguage.googleapis.com/v1beta/openai"


def get_llm_api_key():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return os.getenv("OPENAI_API_KEY")
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return os.getenv("GOOGLE_API_KEY")
    else:
        return "ollama"


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
    else:
        return os.getenv("OLLAMA_MODEL")


def get_small_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1-mini"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    else:
        return os.getenv("OLLAMA_MODEL")


def get_nano_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1-nano"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    else:
        return os.getenv("OLLAMA_MODEL")
