import pytest
from types import SimpleNamespace

from utils.llm_config import get_extra_body, get_llm_config
from utils.llm_provider import get_model
from utils.runtime_settings import RuntimeSettings


class _CodexTestStream:
    def __init__(self, events, failure=None):
        self.events = events
        self.failure = failure
        self.closed = False

    def __iter__(self):
        yield from self.events
        if self.failure is not None:
            raise self.failure

    def close(self):
        self.closed = True


def _observed_codex_client(monkeypatch, terminal=None, failure=None, http_failure=None):
    from utils.llm_config import get_client
    from llmai.shared import ChatGPTClientConfig
    import llmai.chatgpt.client as sdk

    events = [
        SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_fixture")),
        SimpleNamespace(type="response.output_text.delta", item_id="item", output_index=0,
                        content_index=0, delta="partial"),
    ]
    if terminal is not None:
        events.append(SimpleNamespace(
            type=terminal, code="server_error", message="private-provider-message",
            response=SimpleNamespace(
                id="resp_fixture", status=terminal.removeprefix("response."), output=[],
                error=SimpleNamespace(code="server_error", message="private-provider-message"),
                incomplete_details=(SimpleNamespace(reason="max_output_tokens")
                                    if terminal == "response.incomplete" else None),
                usage=SimpleNamespace(input_tokens=3, output_tokens=4, total_tokens=7),
            ),
        ))
    stream = _CodexTestStream(events, failure=failure)
    calls = []
    clones = []

    def create(**kwargs):
        calls.append(kwargs)
        if http_failure is not None:
            raise http_failure
        return stream

    class RawClient:
        responses = SimpleNamespace(create=create)

        def with_options(self, **kwargs):
            clones.append(kwargs)
            return self

    monkeypatch.setattr(sdk, "OpenAI", lambda **kwargs: RawClient())
    return get_client(config=ChatGPTClientConfig(access_token="fixture-token")), stream, calls, clones


@pytest.mark.parametrize("streamed", [False, True])
def test_codex_factory_requires_observed_completion(monkeypatch, streamed):
    client, stream, calls, clones = _observed_codex_client(monkeypatch, "response.completed")
    client._client = client._client.with_options(timeout=17, max_retries=0)
    result = client.generate(model="gpt-6-sol", messages=[], stream=streamed)
    if streamed:
        result = next(chunk for chunk in result if chunk.type == "completion")
    assert len(result.content) == 1 and result.content[0].text == "partial"
    assert result.usage.total_tokens == 7
    assert len(calls) == 1 and stream.closed
    assert clones == [{"timeout": 17, "max_retries": 0}]


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("terminal,code,kind", [
    ("response.failed", "public_provider_response_failed", "terminal_failed"),
    ("response.incomplete", "public_provider_response_incomplete", "incomplete"),
    ("response.cancelled", "public_provider_response_cancelled", "terminal_cancelled"),
    ("error", "public_provider_response_failed", "terminal_error"),
])
def test_codex_factory_preserves_typed_terminal_failure(monkeypatch, streamed, terminal, code, kind):
    from utils.provider_overload import ProviderOutcomeError

    client, stream, calls, _ = _observed_codex_client(monkeypatch, terminal)
    with pytest.raises(ProviderOutcomeError) as raised:
        result = client.generate(model="gpt-6-sol", messages=[], stream=streamed)
        if streamed:
            list(result)
    outcome = raised.value
    assert outcome.code == code and not outcome.overload and not outcome.retryable
    assert outcome.diagnostics["terminalEvent"] == terminal
    assert outcome.diagnostics["failureKind"] == kind
    assert outcome.diagnostics["errorCode"] == "server_error"
    assert outcome.diagnostics["usage"]["total_tokens"] == 7
    assert "private-provider-message" not in str(outcome.diagnostics)
    assert not hasattr(outcome, "status_code")
    assert len(calls) == 1 and stream.closed
    if terminal == "response.incomplete":
        assert outcome.diagnostics["incompleteReason"] == "max_output_tokens"
        assert outcome.terminal_outcome.output_truncated
        assert not outcome.recovery_allowed


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("drop", [False, True])
def test_codex_missing_terminal_keeps_result_unknown(monkeypatch, streamed, drop):
    from utils.provider_overload import ProviderOutcomeError

    original = ConnectionError("private-transport-message") if drop else None
    client, stream, calls, _ = _observed_codex_client(monkeypatch, failure=original)
    with pytest.raises(ProviderOutcomeError) as raised:
        result = client.generate(model="gpt-6-sol", messages=[], stream=streamed)
        if streamed:
            list(result)
    outcome = raised.value
    assert outcome.code == "public_provider_result_unknown"
    assert not outcome.overload and not outcome.recovery_allowed and not outcome.retryable
    assert outcome.diagnostics["statusOrigin"] == "local_stream"
    assert outcome.diagnostics["failureKind"] == "result_unknown"
    assert "httpStatus" not in outcome.diagnostics
    assert len(calls) == 1 and stream.closed
    if drop:
        assert outcome.__cause__.__cause__ is original


@pytest.mark.parametrize("status", [429, 503])
def test_codex_actual_http_errors_keep_remote_classification(monkeypatch, status):
    import httpx
    from openai import APIStatusError
    from llmai.shared.errors import LLMError
    from utils.provider_overload import is_provider_overload

    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    original = APIStatusError("remote error", response=response, body={})
    client, _, calls, _ = _observed_codex_client(monkeypatch, http_failure=original)
    with pytest.raises(LLMError) as raised:
        client.generate(model="gpt-6-sol", messages=[])
    assert raised.value.status_code == status
    assert is_provider_overload(raised.value)
    assert len(calls) == 1


def test_repository_factory_delegates_other_providers(monkeypatch):
    import llmai
    from llmai.shared import OpenAIClientConfig
    from utils.llm_config import get_client

    sentinel = object()
    captured = []
    monkeypatch.setattr(llmai, "get_client", lambda **kwargs: captured.append(kwargs) or sentinel)
    config = OpenAIClientConfig(api_key="fixture-key")
    assert get_client(config=config) is sentinel
    assert captured == [{"config": config, "logger": None}]


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


def test_codex_reasoning_defaults_low_but_preserves_explicit_override():
    from utils.llm_calls.author_slide_effort import (
        normalized_authoring_reasoning_effort,
        resolve_authoring_reasoning_level,
    )

    default = RuntimeSettings.from_sources(
        environ={}, user_config={"LLM": "codex", "CODEX_ACCESS_TOKEN": "token"}
    )
    explicit = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "codex",
            "CODEX_ACCESS_TOKEN": "token",
            "CODEX_REASONING_EFFORT": "high",
        },
    )

    assert get_extra_body(default) == {"reasoning": {"effort": "low"}}
    assert get_extra_body(explicit) == {"reasoning": {"effort": "high"}}

    for model in ("gpt-6.1-sol", "gpt-6.1-sol-2026-10-02"):
        for effort, expected in (
            ("minimal", "low"), ("none", "low"), ("ultra", "max"),
            ("low", "low"), ("medium", "medium"), ("high", "high"),
            ("xhigh", "xhigh"), ("max", "max"),
        ):
            # Exercise the request builder independently of the unchanged public enum.
            settings = RuntimeSettings(
                {"LLM": "codex", "CODEX_MODEL": model, "CODEX_REASONING_EFFORT": effort}
            )
            assert get_extra_body(settings) == {"reasoning": {"effort": expected}}
            assert normalized_authoring_reasoning_effort(model, effort) == expected
            assert resolve_authoring_reasoning_level(effort, model=model) == expected

    for effort in ("minimal", "none", "medium", "max", "ultra"):
        legacy = RuntimeSettings(
            {"LLM": "codex", "CODEX_MODEL": "gpt-6-sol", "CODEX_REASONING_EFFORT": effort}
        )
        expected = None if effort in ("max", "ultra") else {"reasoning": {"effort": effort}}
        assert get_extra_body(legacy) == expected
    assert normalized_authoring_reasoning_effort("gpt-6-sol", "ultra") == "ultra"
    assert resolve_authoring_reasoning_level("max", model="gpt-6-sol") is None
    assert resolve_authoring_reasoning_level("ultra", model="gpt-6-sol") is None


def test_openai_reasoning_effort_unset_sends_nothing():
    settings = RuntimeSettings.from_sources(
        environ={}, user_config={"LLM": "openai", "OPENAI_API_KEY": "key"}
    )
    assert get_extra_body(settings) is None


@pytest.mark.parametrize(
    "effort", ["minimal", "none", "low", "medium", "high", "xhigh"]
)
def test_openai_reasoning_effort_uses_the_flat_chat_completions_field(effort):
    """OPENAI always resolves api_type=COMPLETIONS (chat completions), never
    the Responses API CODEX/AZURE use. llmai's OpenAI client forwards
    extra_body straight into chat.completions.create(), which takes a flat
    `reasoning_effort` string, not the nested `{"reasoning": {"effort": ..}}`
    object the Responses API expects."""

    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "openai",
            "OPENAI_API_KEY": "key",
            "OPENAI_REASONING_EFFORT": effort,
        },
    )
    assert get_extra_body(settings) == {"reasoning_effort": effort}


def test_warn_if_openai_model_lacks_reasoning_dedupes_per_model(monkeypatch, caplog):
    from utils import llm_config

    monkeypatch.setattr(llm_config, "_OPENAI_REASONING_WARNING_LOGGED", set())

    non_reasoning = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "openai",
            "OPENAI_API_KEY": "key",
            "OPENAI_MODEL": "gpt-4.1",
        },
    )
    with caplog.at_level("WARNING", logger="utils.llm_config"):
        get_llm_config(non_reasoning)
    assert "non-reasoning model" in caplog.text
    assert "gpt-4.1" in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING", logger="utils.llm_config"):
        get_llm_config(non_reasoning)
    assert caplog.text == ""  # already warned once for this model


def test_warn_if_openai_model_lacks_reasoning_is_silent_for_reasoning_families(
    monkeypatch, caplog
):
    from utils import llm_config

    monkeypatch.setattr(llm_config, "_OPENAI_REASONING_WARNING_LOGGED", set())

    reasoning = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "openai",
            "OPENAI_API_KEY": "key",
            "OPENAI_MODEL": "gpt-6-astra",
        },
    )
    with caplog.at_level("WARNING", logger="utils.llm_config"):
        get_llm_config(reasoning)
    assert caplog.text == ""
