import os

from api.models import SelectedLLMProvider


def is_ollama_selected() -> bool:
    return get_selected_llm_provider() == SelectedLLMProvider.OLLAMA


def get_selected_llm_provider() -> SelectedLLMProvider:
    return SelectedLLMProvider(os.getenv("LLM"))


def get_model_base_url():
    selected_llm = get_selected_llm_provider()

    if selected_llm == SelectedLLMProvider.OLLAMA:
        return "http://localhost:11434"
    elif selected_llm == SelectedLLMProvider.OPENAI:
        return "https://api.openai.com/v1"
    else:
        return "https://generativelanguage.googleapis.com/v1beta/openai"


def get_large_model():
    selected_llm = get_selected_llm_provider()
    if selected_llm == SelectedLLMProvider.OPENAI:
        return ChatOpenAI(model="gpt-4.1")
    elif selected_llm == SelectedLLMProvider.GOOGLE:
        return ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    else:
        return ChatOllama(model=os.getenv("OLLAMA_MODEL"), temperature=0.8)


def get_small_model():
    selected_llm = os.getenv("LLM")
    if selected_llm == "openai":
        return ChatOpenAI(model="gpt-4.1-mini")
    elif selected_llm == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    else:
        return ChatOllama(model=os.getenv("OLLAMA_MODEL"), temperature=0.8)


def get_nano_model():
    selected_llm = os.getenv("LLM")
    if selected_llm == "openai":
        return ChatOpenAI(model="gpt-4.1-nano")
    elif selected_llm == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    else:
        return ChatOllama(model=os.getenv("OLLAMA_MODEL"), temperature=0.8)
