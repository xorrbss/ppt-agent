import pytest

from utils.llm_config import get_llm_config
from utils.llm_provider import get_model
from utils.runtime_settings import RuntimeSettings


@pytest.mark.parametrize(
    ("provider", "overrides", "expected_provider"),
    [
        ("openai", {"OPENAI_API_KEY": "key"}, "openai"),
        ("google", {"GOOGLE_API_KEY": "key"}, "google"),
        ("vertex", {"VERTEX_API_KEY": "key"}, "vertex"),
        (
            "azure",
            {
                "AZURE_OPENAI_API_KEY": "key",
                "AZURE_OPENAI_API_VERSION": "2024-10-21",
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            },
            "azure",
        ),
        (
            "bedrock",
            {
                "BEDROCK_AWS_ACCESS_KEY_ID": "access",
                "BEDROCK_AWS_SECRET_ACCESS_KEY": "secret",
            },
            "bedrock",
        ),
        ("openrouter", {"OPENROUTER_API_KEY": "key"}, "openrouter"),
        ("fireworks", {"FIREWORKS_API_KEY": "key"}, "fireworks"),
        ("together", {"TOGETHER_API_KEY": "key"}, "togetherai"),
        ("cerebras", {"CEREBRAS_API_KEY": "key"}, "cerebras"),
        ("anthropic", {"ANTHROPIC_API_KEY": "key"}, "anthropic"),
        ("litellm", {"LITELLM_BASE_URL": "https://gateway.example"}, "litellm"),
        ("lmstudio", {"LMSTUDIO_BASE_URL": "http://localhost:1234"}, "lmstudio"),
        ("ollama", {"OLLAMA_MODEL": "llama3.2:3b"}, "openai"),
        (
            "custom",
            {"CUSTOM_LLM_URL": "https://custom.example/v1", "CUSTOM_MODEL": "m"},
            "openai",
        ),
        (
            "codex",
            {"CODEX_ACCESS_TOKEN": "token", "CODEX_ACCOUNT_ID": "account"},
            "chatgpt",
        ),
    ],
)
def test_provider_configs_resolve_from_explicit_snapshot(
    provider: str,
    overrides: dict[str, object],
    expected_provider: str,
):
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": provider, **overrides},
    )

    config = get_llm_config(settings)

    assert config.provider == expected_provider
    assert get_model(settings)
