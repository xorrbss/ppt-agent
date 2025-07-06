import os

from openai import AsyncOpenAI

from api.models import SelectedLLMProvider


def is_ollama_selected() -> bool:
    return get_selected_llm_provider() == SelectedLLMProvider.OLLAMA


def get_llm_provider_url_or():
    llm_provider_url = os.getenv("LLM_PROVIDER_URL") or "http://localhost:11434"
    if llm_provider_url.endswith("/"):
        return llm_provider_url[:-1]
    return llm_provider_url


def get_ollama_request_headers():
    if os.getenv("LLM_API_KEY"):
        return {
            "Authorization": f"Bearer {os.getenv('LLM_API_KEY')}",
        }
    return {}


def get_selected_llm_provider() -> SelectedLLMProvider:
    return SelectedLLMProvider(os.getenv("LLM"))


def get_model_base_url():
    selected_llm = get_selected_llm_provider()

    if selected_llm == SelectedLLMProvider.OPENAI:
        return "https://api.openai.com/v1"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "https://generativelanguage.googleapis.com/v1beta/openai"
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return os.path.join(get_llm_provider_url_or(), "v1")
    else:
        raise ValueError(f"Invalid LLM provider")


def get_llm_api_key():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return os.getenv("OPENAI_API_KEY")
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return os.getenv("GOOGLE_API_KEY")
    elif selected_llm == SelectedLLMProvider.OLLAMA:
        return os.getenv("LLM_API_KEY") or "ollama"
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
    else:
        return os.getenv("MODEL")


def get_small_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1-mini"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    else:
        return os.getenv("MODEL")


def get_nano_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return "gpt-4.1-nano"
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return "gemini-2.0-flash"
    else:
        return os.getenv("MODEL")
