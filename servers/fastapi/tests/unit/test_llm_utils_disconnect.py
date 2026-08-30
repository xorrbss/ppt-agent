import asyncio
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from llmai.shared import ResponseStreamCompletionChunk, ResponseStreamContentChunk

from utils.llm_utils import generate_structured_with_schema_retries


class RetryClient:
    def __init__(self):
        self.calls = []
        self.responses = [None, {"result": "ok"}]

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.responses.pop(0))


class StreamingClient:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.closed = threading.Event()

    def generate(self, **kwargs):
        self.calls.append(kwargs)

        def events():
            try:
                while True:
                    self.started.set()
                    time.sleep(0.01)
                    yield ResponseStreamContentChunk(chunk='{"result":"pending"}')
            finally:
                self.closed.set()

        return events()


def test_regular_generation_keeps_existing_retry_behavior(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")
    client = RetryClient()

    with patch("utils.llm_utils.asyncio.sleep", new=AsyncMock()):
        result = asyncio.run(
            generate_structured_with_schema_retries(
                client,
                "test-model",
                messages=[],
                response_format=object(),
                json_schema={},
            )
        )

    assert result == {"result": "ok"}
    assert len(client.calls) == 2
    assert all(call["stream"] is False for call in client.calls)


def test_supported_client_receives_stable_provider_idempotency_key(monkeypatch):
    monkeypatch.setenv("LLM", "openai")
    monkeypatch.setattr(
        "utils.llm_utils.current_provider_idempotency_key",
        lambda: "ppt-stable-logical-key",
    )
    monkeypatch.setattr(
        "utils.llm_utils._provider_receipt_identity",
        lambda: "openai",
    )

    class RawClient:
        def __init__(self, headers=None):
            self.headers = headers or {}

        def with_options(self, *, max_retries, extra_headers=None):
            assert max_retries == 0
            return RawClient(extra_headers)

    class IdempotentClient:
        def __init__(self):
            self.calls = []
            self._client = RawClient()

        def generate(self, **kwargs):
            self.calls.append((kwargs, dict(self._client.headers)))
            return SimpleNamespace(content={"result": "ok"})

    IdempotentClient.__module__ = "llmai.openai.client"

    client = IdempotentClient()
    result = asyncio.run(
        generate_structured_with_schema_retries(
            client,
            "test-model",
            messages=[],
            response_format=object(),
            json_schema={},
        )
    )

    assert result == {"result": "ok"}
    assert client.calls[0][1]["Idempotency-Key"] == "ppt-stable-logical-key"


def test_parse_retry_is_separately_admitted_and_each_success_records_usage(
    monkeypatch,
):
    monkeypatch.setenv("LLM", "ollama")
    client = RetryClient()
    admitted = []
    receipts = []

    @asynccontextmanager
    async def fake_admission(*, is_retry=False, semantic_slot="primary", **_kwargs):
        admitted.append((is_retry, semantic_slot))
        yield None

    async def fake_record_provider_call(**kwargs):
        receipts.append(kwargs)

    monkeypatch.setattr(
        "utils.llm_utils.provider_call_admission",
        fake_admission,
    )
    monkeypatch.setattr(
        "utils.llm_utils.record_provider_call",
        fake_record_provider_call,
    )

    with patch("utils.llm_utils.asyncio.sleep", new=AsyncMock()):
        result = asyncio.run(
            generate_structured_with_schema_retries(
                client,
                "test-model",
                messages=[],
                response_format=object(),
                json_schema={},
            )
        )

    assert result == {"result": "ok"}
    assert admitted == [
        (False, "structured.initial.attempt-1"),
        (True, "structured.parse-repair.attempt-1"),
    ]
    assert [receipt["retry_class"] for receipt in receipts] == [
        "initial",
        "retry",
    ]
    assert all(receipt["response"] is not None for receipt in receipts)


def test_disconnect_cancels_generation_without_retrying(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")
    client = StreamingClient()

    async def run():
        async def is_disconnected():
            return client.started.is_set()

        with pytest.raises(asyncio.CancelledError):
            await generate_structured_with_schema_retries(
                client,
                "test-model",
                messages=[],
                response_format=object(),
                json_schema={},
                disconnect_checker=is_disconnected,
            )

        while not client.closed.is_set():
            await asyncio.sleep(0.001)

    asyncio.run(run())

    assert len(client.calls) == 1
    assert client.calls[0]["stream"] is True


def test_connected_request_uses_stream_completion_content(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")

    class CompletedClient:
        def __init__(self):
            self.calls = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return iter(
                [ResponseStreamCompletionChunk(content={"result": "complete"})]
            )

    client = CompletedClient()

    async def run():
        async def is_disconnected():
            return False

        return await generate_structured_with_schema_retries(
            client,
            "test-model",
            messages=[],
            response_format=object(),
            json_schema={},
            disconnect_checker=is_disconnected,
        )

    assert asyncio.run(run()) == {"result": "complete"}
    assert client.calls[0]["stream"] is True


def test_connected_request_keeps_schema_validation_retries(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")

    class ValidationRetryClient:
        def __init__(self):
            self.calls = []
            self.responses = [{"wrong": "value"}, {"result": "fixed"}]

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return iter(
                [ResponseStreamCompletionChunk(content=self.responses.pop(0))]
            )

    client = ValidationRetryClient()

    async def run():
        async def is_disconnected():
            return False

        return await generate_structured_with_schema_retries(
            client,
            "test-model",
            messages=[],
            response_format=object(),
            json_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
            validate_schema=True,
            disconnect_checker=is_disconnected,
        )

    assert asyncio.run(run()) == {"result": "fixed"}
    assert len(client.calls) == 2
    assert all(call["stream"] is True for call in client.calls)


def test_upper_bound_validation_error_is_compacted_without_llm_retry(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")

    class OverlongClient:
        def __init__(self):
            self.calls = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(content={"result": "x" * 8})

    client = OverlongClient()
    result = asyncio.run(
        generate_structured_with_schema_retries(
            client,
            "test-model",
            messages=[],
            response_format=object(),
            json_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "string", "maxLength": 5},
                },
                "required": ["result"],
            },
            validate_schema=True,
        )
    )

    assert result == {"result": "xxxx…"}
    assert len(client.calls) == 1


def test_transient_provider_errors_use_exponential_backoff_then_recover(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")

    class TransientError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    class RecoveringClient:
        def __init__(self):
            self.calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TransientError(500)
            return SimpleNamespace(content={"result": "ok"})

    client = RecoveringClient()
    sleep = AsyncMock()
    with patch("utils.llm_utils.asyncio.sleep", new=sleep):
        result = asyncio.run(
            generate_structured_with_schema_retries(
                client,
                "test-model",
                messages=[],
                response_format=object(),
                json_schema={},
            )
        )

    assert result == {"result": "ok"}
    assert client.calls == 2
    assert [call.args[0] for call in sleep.await_args_list] == [1.0]


def test_exhausted_transient_provider_errors_are_mapped_to_retryable_503(
    monkeypatch,
):
    monkeypatch.setenv("LLM", "ollama")

    class UnavailableClient:
        def __init__(self):
            self.calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            raise HTTPException(status_code=500, detail="provider overloaded")

    client = UnavailableClient()
    sleep = AsyncMock()
    with (
        patch("utils.llm_utils.asyncio.sleep", new=sleep),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(
            generate_structured_with_schema_retries(
                client,
                "test-model",
                messages=[],
                response_format=object(),
                json_schema={},
            )
        )

    assert client.calls == 2
    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "10"}
    assert "temporarily unavailable" in error.value.detail
    assert [call.args[0] for call in sleep.await_args_list] == [1.0]
