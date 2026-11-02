"""Anthropic structured calls use output_config.format, never a forced tool.

Claude Opus 5.5, Claude Sonnet 5.5 and Claude Fable 5.1 reject forced
``tool_choice`` with HTTP 400; llmai's Anthropic client forces its response
tool for every ``response_format`` call. These tests drive the real llmai
client and Anthropic SDK against a stubbed HTTP transport and assert the wire
request body.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from anthropic import Anthropic
from llmai.anthropic.client import AnthropicClient
from llmai.shared import (
    AnthropicClientConfig,
    JSONObjectResponse,
    JSONSchemaResponse,
    OpenAIClientConfig,
    SystemMessage,
    UserMessage,
)
from llmai.shared.reasoning import ReasoningConfig
from llmai.shared.responses import (
    ResponseStreamChunk,
    ResponseStreamCompletionChunk,
    ResponseStreamContentChunk,
)

import utils.llm_config  # noqa: F401  (installs the client into llmai.get_client)
from enums.llm_provider import LLMProvider
from utils.anthropic_structured_output import (
    NativeSchemaUnsupported,
    RepositoryAnthropicClient,
    install_anthropic_structured_output_client,
    merge_output_config,
    native_output_schema,
)

NEW_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "bullets"],
    "properties": {
        "title": {"type": "string", "maxLength": 40},
        "bullets": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
}
WIRE_SCHEMA = {
    "type": "object",
    "required": ["title", "bullets"],
    "properties": {
        "title": {"type": "string", "description": "{maxLength: 40}"},
        "bullets": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "additionalProperties": False,
}
ANSWER = {"title": "Quarterly plan", "bullets": ["Grow", "Hire"]}


def _message_json(text: str, *, model: str = "claude-sonnet-5-5", stop: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


def _sse(text_parts: list[str]) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {**_message_json(""), "content": [], "stop_reason": None}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        *(
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": part}})
            for part in text_parts
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class _Recorder:
    def __init__(self, response_text: str = json.dumps(ANSWER), *, stream_parts: list[str] | None = None, stop: str = "end_turn"):
        self.bodies: list[dict] = []
        self._text = response_text
        self._stream_parts = stream_parts
        self._stop = stop

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if body.get("stream"):
            return httpx.Response(200, content=_sse(self._stream_parts or [self._text]), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=_message_json(self._text, model=body["model"], stop=self._stop))


def _client(recorder: _Recorder, cls=RepositoryAnthropicClient):
    client = cls(config=AnthropicClientConfig(api_key="test-key"))
    client._client = Anthropic(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(recorder)))
    return client


def _messages():
    return [SystemMessage(content="Return JSON."), UserMessage(content="Plan the quarter.")]


def _structured(name: str = "plan", schema: dict = SCHEMA, strict: bool = False) -> JSONSchemaResponse:
    return JSONSchemaResponse(name=name, json_schema=schema, strict=strict)


def _assert_no_forced_tool_or_budget(body: dict) -> None:
    assert "tool_choice" not in body
    assert "tools" not in body
    assert "budget_tokens" not in json.dumps(body.get("thinking") or {})


def test_llmai_get_client_builds_the_repository_client_only_for_anthropic():
    from llmai import get_client

    assert type(get_client(config=AnthropicClientConfig(api_key="k"))) is RepositoryAnthropicClient
    assert type(utils.llm_config.get_client(config=AnthropicClientConfig(api_key="k"))) is RepositoryAnthropicClient
    openai_client = get_client(config=OpenAIClientConfig(api_key="k"))
    assert type(openai_client).__module__.startswith("llmai.openai")
    install_anthropic_structured_output_client()  # idempotent
    assert type(get_client(config=AnthropicClientConfig(api_key="k"))) is RepositoryAnthropicClient


@pytest.mark.parametrize("model", NEW_MODELS)
def test_structured_call_sends_output_config_format_and_parses_the_object(model):
    recorder = _Recorder()
    response = _client(recorder).generate(model=model, messages=_messages(), response_format=_structured(), max_tokens=2048)

    (body,) = recorder.bodies
    assert body["model"] == model
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": WIRE_SCHEMA}}
    _assert_no_forced_tool_or_budget(body)
    assert "thinking" not in body
    assert response.content == ANSWER
    # The JSON stays in the conversation history as the assistant's text.
    assert response.messages[-1].content[0].text == json.dumps(ANSWER)


def test_strict_flag_makes_no_difference_to_the_native_request():
    recorder = _Recorder()
    _client(recorder).generate(model="claude-sonnet-5-5", messages=_messages(), response_format=_structured(strict=True))
    assert recorder.bodies[0]["output_config"]["format"]["schema"] == WIRE_SCHEMA


def test_authoring_effort_in_extra_body_is_merged_with_the_format(monkeypatch):
    from services.generation_core.authoring import author_slide

    monkeypatch.setattr(author_slide, "get_llm_provider", lambda *a, **k: LLMProvider.ANTHROPIC)
    kwargs = author_slide._apply_anthropic_authoring_reasoning(
        {"model": "claude-sonnet-5-5", "messages": _messages(), "response_format": _structured()},
        "medium",
        model="claude-sonnet-5-5",
    )
    assert kwargs["extra_body"] == {"output_config": {"effort": "medium"}}
    recorder = _Recorder()
    response = _client(recorder).generate(**kwargs)

    body = recorder.bodies[0]
    assert body["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": WIRE_SCHEMA}}
    _assert_no_forced_tool_or_budget(body)
    assert "thinking" not in body
    assert response.content == ANSWER
    # The caller's extra_body is not mutated.
    assert kwargs["extra_body"] == {"output_config": {"effort": "medium"}}


@pytest.mark.parametrize("model", NEW_MODELS)
def test_llmai_reasoning_effort_is_kept_next_to_the_format_without_budget_tokens(model):
    # llmai turns an effort into budget_tokens only for models whose bundled
    # metadata lists a thinking budget; the 5.5 / Fable 5.1 ids are unknown to
    # it, so the request must carry adaptive thinking and the effort only.
    recorder = _Recorder()
    _client(recorder).generate(
        model=model,
        messages=_messages(),
        response_format=_structured(),
        reasoning=ReasoningConfig(effort="high"),
    )
    body = recorder.bodies[0]
    assert body["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": WIRE_SCHEMA}}
    assert body["thinking"] == {"type": "adaptive"}
    _assert_no_forced_tool_or_budget(body)


def test_extra_body_effort_still_wins_over_llmai_effort():
    recorder = _Recorder()
    _client(recorder).generate(
        model="claude-opus-5-5",
        messages=_messages(),
        response_format=_structured(),
        reasoning=ReasoningConfig(effort="high"),
        extra_body={"output_config": {"effort": "low"}, "metadata": {"user_id": "u"}},
    )
    body = recorder.bodies[0]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["metadata"] == {"user_id": "u"}


def test_stream_hides_json_text_and_returns_the_object_on_completion():
    recorder = _Recorder(stream_parts=['{"title": "Quarterly plan", ', '"bullets": ["Grow", "Hire"]}'])
    events = list(
        _client(recorder).generate(
            model="claude-sonnet-5-5",
            messages=_messages(),
            response_format=_structured(),
            extra_body={"output_config": {"effort": "medium"}},
            stream=True,
        )
    )

    body = recorder.bodies[0]
    assert body["stream"] is True
    assert body["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": WIRE_SCHEMA}}
    _assert_no_forced_tool_or_budget(body)
    assert not [e for e in events if isinstance(e, ResponseStreamContentChunk)]
    assert not [e for e in events if isinstance(e, ResponseStreamChunk) and e.chunk_type == "content"]
    completion = events[-1]
    assert isinstance(completion, ResponseStreamCompletionChunk)
    assert completion.content == ANSWER


def test_unstructured_requests_are_identical_to_llmai():
    plain, base = _Recorder("hello"), _Recorder("hello")
    kwargs = dict(model="claude-sonnet-5-5", messages=_messages(), max_tokens=100, extra_body={"output_config": {"effort": "low"}})
    ours = _client(plain).generate(**kwargs)
    theirs = _client(base, cls=AnthropicClient).generate(**kwargs)
    assert plain.bodies == base.bodies
    assert ours.content == theirs.content


def test_malformed_json_stays_text_for_the_callers_parse_retry():
    recorder = _Recorder('{"title": "Quarterly', stop="max_tokens")
    response = _client(recorder).generate(model="claude-sonnet-5-5", messages=_messages(), response_format=_structured())
    assert response.content[0].text == '{"title": "Quarterly'


def test_inexpressible_schema_keeps_llmais_forced_tool_with_a_warning(caplog):
    free_map = {
        "type": "object",
        "required": ["scores"],
        "properties": {"scores": {"type": "object", "additionalProperties": {"type": "number"}}},
    }
    recorder = _Recorder()
    with caplog.at_level(logging.WARNING, logger="utils.anthropic_structured_output"):
        _client(recorder).generate(model="claude-sonnet-4-6", messages=_messages(), response_format=_structured("scores", free_map), max_tokens=1024)
    body = recorder.bodies[0]
    assert "format" not in (body.get("output_config") or {})
    assert body["tool_choice"] == {"type": "tool", "name": "scores"}
    assert "anthropic_native_structured_output_unavailable" in caplog.text


def test_json_object_response_keeps_llmais_path():
    recorder = _Recorder()
    _client(recorder).generate(model="claude-sonnet-4-6", messages=_messages(), response_format=JSONObjectResponse(), max_tokens=1024)
    assert recorder.bodies[0]["tool_choice"] == {"type": "tool", "name": "response"}


def test_schema_transform_restates_unsupported_constraints_in_descriptions():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "evidence": {"type": "string", "description": "Visible text.", "minLength": 1, "maxLength": 160, "pattern": "^[a-z]+$"},
            "index": {"type": "integer", "minimum": 1, "maximum": 3},
            "pages": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3, "uniqueItems": True},
            "kind": {"oneOf": [{"type": "string", "const": "a"}, {"type": "null"}]},
            "when": {"type": "string", "format": "date"},
            "slug": {"type": "string", "format": "slug"},
            "ref": {"$ref": "#/$defs/item"},
        },
        "required": ["evidence"],
        "$defs": {"item": {"type": "object", "properties": {"n": {"type": "number", "default": 0}}, "required": ["n"]}},
    }
    wire = native_output_schema(schema)
    props = wire["properties"]
    assert "$schema" not in wire and wire["additionalProperties"] is False
    assert props["evidence"] == {
        "type": "string",
        "description": 'Visible text.\n\n{minLength: 1, maxLength: 160, pattern: "^[a-z]+$"}',
    }
    assert props["index"] == {"type": "integer", "description": "{minimum: 1, maximum: 3}"}
    assert props["pages"] == {"type": "array", "items": {"type": "integer"}, "description": "{minItems: 3, maxItems: 3, uniqueItems: true}"}
    assert props["kind"] == {"anyOf": [{"type": "string", "const": "a"}, {"type": "null"}]}
    assert props["when"] == {"type": "string", "format": "date"}
    assert props["slug"] == {"type": "string", "description": '{format: "slug"}'}
    assert props["ref"] == {"$ref": "#/$defs/item"}
    assert wire["$defs"]["item"]["additionalProperties"] is False
    assert wire["$defs"]["item"]["properties"]["n"] == {"type": "number", "default": 0}
    # The caller's schema is not modified.
    assert schema["properties"]["evidence"]["maxLength"] == 160


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {"m": {"type": "object", "patternProperties": {"^x": {"type": "string"}}, "properties": {}}}},
        {"type": "object", "properties": {"t": {"type": "array", "items": [{"type": "string"}]}}},
    ],
)
def test_schema_transform_refuses_what_it_cannot_express(schema):
    with pytest.raises(NativeSchemaUnsupported):
        native_output_schema(schema)


def test_vision_advisory_schemas_transform_without_length_or_range_keywords():
    from services.generation_core.quality import vision_advisory_prompt as prompt

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                if key not in ("properties", "$defs"):
                    yield from keys(value)
                else:
                    for sub in value.values():
                        yield from keys(sub)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for schema in (prompt.advisory_json_schema(), prompt.advisory_json_schema(slide_count=3)):
        wire = native_output_schema(schema)
        assert not {"maxLength", "minLength", "maximum", "minimum", "maxItems", "pattern"} & set(keys(wire))


def test_merge_output_config_rejects_a_conflicting_format():
    with pytest.raises(ValueError):
        merge_output_config({"output_config": {"format": {"type": "json_schema", "schema": {}}}}, None, {"type": "json_schema", "schema": WIRE_SCHEMA})
