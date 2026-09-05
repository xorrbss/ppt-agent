import asyncio
import logging
import os
import threading
import time
import uuid
from unittest.mock import patch

import services.mem0_oss_memory as mem0_oss
import services.mem0_presentation_memory_service as mem0_service_module
from services.mem0_presentation_memory_service import Mem0PresentationMemoryService


class FakeMemoryClient:
    instances: list["FakeMemoryClient"] = []

    def __init__(self, config=None):
        self.config = config
        self.add_calls = []
        self.search_calls = []
        self.next_search_response = {"results": []}
        FakeMemoryClient.instances.append(self)

    @classmethod
    def from_config(cls, config):
        return cls(config=config)

    def add(self, *args, **kwargs):
        messages = kwargs.get("messages") if "messages" in kwargs else None
        if messages is None and args:
            messages = args[0]

        self.add_calls.append(
            {
                "messages": messages,
                "user_id": kwargs.get("user_id"),
                "infer": kwargs.get("infer"),
            }
        )
        return {"ok": True}

    def search(self, query, *args, **kwargs):
        self.search_calls.append(
            {
                "query": query,
                "filters": kwargs.get("filters"),
                "user_id": kwargs.get("user_id"),
                "top_k": kwargs.get("top_k"),
            }
        )
        return self.next_search_response


def _mem0_oss_fresh() -> None:
    mem0_oss._shared_client = None  # type: ignore[attr-defined]
    mem0_oss._init_attempted = False  # type: ignore[attr-defined]


class TestMem0PresentationMemoryService:
    def setup_method(self):
        FakeMemoryClient.instances = []
        _mem0_oss_fresh()

    def test_shared_client_defaults_to_local_llm_without_openai_key(self):
        captured = {}

        def _fake_memory_from_config(config, telemetry_base):
            captured["config"] = config
            captured["telemetry_base"] = telemetry_base
            return FakeMemoryClient.from_config(config)

        with patch.dict(
            "os.environ",
            {
                "MEM0_ENABLED": "true",
                # This test exercises the LLM-config branch, not the optional
                # spaCy NER dependency. Disable the spaCy-model guard so the
                # client initializes even when en_core_web_sm is not installed
                # (e.g. on Windows dev machines).
                "MEM0_REQUIRE_SPACY_MODEL": "false",
                "APP_DATA_DIRECTORY": "/tmp/presenton-test",
                "OLLAMA_URL": "http://ollama:11434",
                "OLLAMA_MODEL": "llama3.1:8b",
            },
            clear=False,
        ), patch(
            "services.mem0_oss_memory.memory_from_config",
            side_effect=_fake_memory_from_config,
        ):
            client = mem0_oss.get_shared_mem0_client()

        assert client is not None
        # telemetry_base is built with os.path.join, so the separator is
        # platform-dependent (backslash on Windows). Normalize before the
        # suffix check so the assertion validates the directory structure on
        # any OS.
        assert captured["telemetry_base"].replace(os.sep, "/").endswith(
            "/mem0/telemetry/oss"
        )
        assert captured["config"]["llm"]["provider"] == "openai"
        assert captured["config"]["llm"]["config"]["model"] == "llama3.1:8b"
        assert captured["config"]["llm"]["config"]["api_key"] == "ollama"
        assert (
            captured["config"]["llm"]["config"]["openai_base_url"]
            == "http://ollama:11434/v1"
        )
        assert captured["config"]["vector_store"]["provider"] == "qdrant"
        assert captured["config"]["embedder"]["provider"] == "fastembed"

    def test_store_generation_context_uses_presentation_scope(self):
        with patch.dict(
            "os.environ",
            {
                "MEM0_ENABLED": "true",
                "APP_DATA_DIRECTORY": "/tmp/presenton-test",
            },
            clear=False,
        ), patch(
            "services.mem0_presentation_memory_service.get_shared_mem0_client",
            return_value=FakeMemoryClient.from_config(
                {
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {
                            "on_disk": True,
                            "embedding_model_dims": 384,
                        },
                    },
                    "embedder": {
                        "provider": "fastembed",
                        "config": {
                            "model": "BAAI/bge-small-en-v1.5",
                            "embedding_dims": 384,
                        },
                    },
                }
            ),
        ):
            service = Mem0PresentationMemoryService()
            presentation_id = uuid.uuid4()
            asyncio.run(
                service.store_generation_context(
                    presentation_id=presentation_id,
                    system_prompt="system prompt",
                    user_prompt="user prompt",
                    extracted_document_text="doc text",
                    source_content="seed prompt",
                    instructions="be concise",
                )
            )

        assert len(FakeMemoryClient.instances) == 1
        client = FakeMemoryClient.instances[0]
        assert client.config is not None
        assert client.config["vector_store"]["provider"] == "qdrant"
        assert client.config["embedder"]["provider"] == "fastembed"
        assert (
            client.config["embedder"]["config"]["model"]
            == "BAAI/bge-small-en-v1.5"
        )
        assert client.config["embedder"]["config"]["embedding_dims"] == 384
        assert client.config["vector_store"]["config"]["on_disk"] is True
        assert client.config["vector_store"]["config"]["embedding_model_dims"] == 384
        assert len(client.add_calls) == 5

        scoped_user_id = f"presentation:{presentation_id}"
        for call in client.add_calls:
            assert call["user_id"] == scoped_user_id
            assert call["infer"] is False

        serialized_messages = "\n".join(
            str(call["messages"][0]["content"]) for call in client.add_calls
        )
        assert "[outline_system_prompt]" in serialized_messages
        assert "[outline_user_prompt]" in serialized_messages
        assert "[document_extracted_text]" in serialized_messages
        assert "[presentation_source_prompt]" in serialized_messages

    def test_retrieve_context_uses_same_scope_and_deduplicates(self):
        with patch.dict(
            "os.environ",
            {
                "MEM0_ENABLED": "true",
                "MEM0_TOP_K": "5",
                "APP_DATA_DIRECTORY": "/tmp/presenton-test",
            },
            clear=False,
        ), patch(
            "services.mem0_presentation_memory_service.get_shared_mem0_client",
            return_value=FakeMemoryClient.from_config(
                {
                    "vector_store": {"provider": "qdrant", "config": {}},
                    "embedder": {
                        "provider": "fastembed",
                        "config": {
                            "model": "BAAI/bge-small-en-v1.5",
                            "embedding_dims": 384,
                        },
                    },
                }
            ),
        ):
            service = Mem0PresentationMemoryService()
            presentation_id = uuid.uuid4()

            asyncio.run(
                service.store_generated_outlines(
                    presentation_id,
                    {"slides": [{"content": "One"}]},
                )
            )

            client = FakeMemoryClient.instances[0]
            client.next_search_response = {
                "results": [
                    {"memory": "Memory A"},
                    {"memory": "Memory A"},
                    {"memory": "Memory B"},
                ]
            }

            context = asyncio.run(
                service.retrieve_context(
                    presentation_id=presentation_id,
                    query="change the conclusion",
                )
            )

        assert "Memory A" in context
        assert "Memory B" in context
        assert context.count("Memory A") == 1

        assert len(client.search_calls) == 1
        assert client.search_calls[0]["query"] == "change the conclusion"
        assert client.search_calls[0]["filters"] == {
            "user_id": f"presentation:{presentation_id}"
        }
        assert client.search_calls[0]["top_k"] == 5

    def test_add_message_timeout_is_treated_as_a_failed_call(self, caplog):
        class SlowMemoryClient(FakeMemoryClient):
            def add(self, *args, **kwargs):
                time.sleep(0.2)
                return super().add(*args, **kwargs)

        with patch.dict(
            "os.environ",
            {"MEM0_ENABLED": "true", "APP_DATA_DIRECTORY": "/tmp/presenton-test"},
            clear=False,
        ), patch(
            "services.mem0_presentation_memory_service.get_shared_mem0_client",
            return_value=SlowMemoryClient.from_config({}),
        ):
            service = Mem0PresentationMemoryService()
            service._timeout_seconds = 0.01
            presentation_id = uuid.uuid4()

            with caplog.at_level(logging.WARNING):
                # Must not raise: a timed-out mem0 call is a non-fatal failure,
                # the same as any other mem0 runtime error.
                asyncio.run(
                    service.store_generated_outlines(
                        presentation_id, {"slides": [{"content": "One"}]}
                    )
                )

        assert any("timed out" in record.message for record in caplog.records)

    def test_retrieve_context_timeout_returns_empty_string(self, caplog):
        class SlowMemoryClient(FakeMemoryClient):
            def search(self, *args, **kwargs):
                time.sleep(0.2)
                return super().search(*args, **kwargs)

        with patch.dict(
            "os.environ",
            {"MEM0_ENABLED": "true", "APP_DATA_DIRECTORY": "/tmp/presenton-test"},
            clear=False,
        ), patch(
            "services.mem0_presentation_memory_service.get_shared_mem0_client",
            return_value=SlowMemoryClient.from_config({}),
        ):
            service = Mem0PresentationMemoryService()
            service._timeout_seconds = 0.01
            presentation_id = uuid.uuid4()

            with caplog.at_level(logging.WARNING):
                context = asyncio.run(
                    service.retrieve_context(
                        presentation_id=presentation_id, query="anything"
                    )
                )

        assert context == ""
        assert any("timed out" in record.message for record in caplog.records)

    def test_mem0_blocking_calls_run_on_a_dedicated_executor(self):
        # A timeout test only proves the *caller* stops waiting; it says
        # nothing about the worker thread, which asyncio.wait_for cannot
        # cancel. This test proves isolation directly: mem0's blocking calls
        # must land on the dedicated mem0 executor, never on the loop's
        # default (shared) executor that unrelated to_thread callers use.
        captured_thread_names: list[str] = []

        class ThreadRecordingClient(FakeMemoryClient):
            def add(self, *args, **kwargs):
                captured_thread_names.append(threading.current_thread().name)
                return super().add(*args, **kwargs)

            def search(self, *args, **kwargs):
                captured_thread_names.append(threading.current_thread().name)
                return super().search(*args, **kwargs)

        with patch.dict(
            "os.environ",
            {"MEM0_ENABLED": "true", "APP_DATA_DIRECTORY": "/tmp/presenton-test"},
            clear=False,
        ), patch(
            "services.mem0_presentation_memory_service.get_shared_mem0_client",
            return_value=ThreadRecordingClient.from_config({}),
        ):
            service = Mem0PresentationMemoryService()
            presentation_id = uuid.uuid4()

            async def _run() -> str:
                await service.store_generated_outlines(
                    presentation_id, {"slides": [{"content": "One"}]}
                )
                await service.retrieve_context(
                    presentation_id=presentation_id, query="anything"
                )
                # A plain asyncio.to_thread call uses the loop's default
                # (shared) executor -- the one every other unrelated
                # to_thread caller in the process, e.g. the Chrome discovery
                # health check, also uses.
                return await asyncio.to_thread(
                    lambda: threading.current_thread().name
                )

            default_executor_thread_name = asyncio.run(_run())

        assert captured_thread_names, "mem0 client was never called"
        for name in captured_thread_names:
            assert name.startswith("mem0-presentation-memory"), name
        assert not default_executor_thread_name.startswith(
            "mem0-presentation-memory"
        )

    def test_mem0_executor_is_distinct_and_bounded(self):
        # Guards against a regression that re-inlines the dedicated executor
        # back into asyncio.to_thread's shared default pool.
        executor = mem0_service_module._MEM0_EXECUTOR
        assert executor is not None
        assert (
            executor._max_workers  # noqa: SLF001 - test-only introspection
            == mem0_service_module.MEM0_EXECUTOR_MAX_WORKERS
        )

        async def _default_executor():
            await asyncio.to_thread(lambda: None)
            return asyncio.get_running_loop()._default_executor  # noqa: SLF001

        default_executor = asyncio.run(_default_executor())
        assert default_executor is not executor

