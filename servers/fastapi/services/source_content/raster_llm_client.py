"""Vision JSON calls for raster redesign through the project's LLM configuration.

Bound to one runtime-settings snapshot (the analysis operation's). ``from_runtime``
returns ``None`` when no vision-capable provider is configured, so callers keep
the heuristic result; call failures surface as exceptions the caller catches.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from typing import Any

from services.source_content.raster_stage_timing import record_call, stage, vision_stage

MAX_CONCURRENT_CALLS = 8
CALL_TIMEOUT_SECONDS = 240.0


def decode_json_object(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class VisionJsonClient:
    """Callable ``(system, parts, schema) -> dict | None``; parts are str or PNG bytes."""

    def __init__(self, settings: Any, provider: str, model: str, max_tokens: int = 16000):
        self.settings = settings
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.calls = 0
        self._deterministic = True
        self.input_tokens = 0
        self.output_tokens = 0
        self._gate = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)
        self._lock = threading.Lock()

    @classmethod
    def from_runtime(cls, settings: Any = None) -> VisionJsonClient | None:
        try:
            from constants.provider_registry import resolve_provider
            from utils.llm_provider import get_llm_provider, get_model
            from utils.runtime_settings import resolve_runtime_settings

            snapshot = resolve_runtime_settings(settings)
            provider = get_llm_provider(snapshot)
            resolve_provider(provider.value, required_capabilities=("vision",))
            model = str(get_model(snapshot) or "")
        except Exception:
            return None  # no vision-capable provider configured
        return cls(snapshot, provider.value, model) if model else None

    def __call__(self, system: str, parts: Sequence[str | bytes], schema: dict[str, Any], name: str) -> dict | None:
        from utils.llm_content_extraction import extract_text

        with stage(None, vision_stage(name)):
            requested = time.perf_counter()
            with self._gate:
                acquired = time.perf_counter()
                try:
                    response = self._generate(system, parts, schema, name)
                finally:
                    record_call(acquired - requested, time.perf_counter() - acquired, name)
        usage = getattr(response, "usage", None)
        with self._lock:
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        if isinstance(response.content, dict):  # structured output already parsed
            return response.content
        return decode_json_object(extract_text(response.content) or "")

    def _generate(self, system: str, parts: Sequence[str | bytes], schema: dict[str, Any], name: str) -> Any:
        """One provider response; called while holding the concurrency gate."""

        from llmai import get_client
        from llmai.shared import ImageContentPart, JSONSchemaResponse, SystemMessage, TextContentPart, UserMessage

        from utils.llm_config import enforce_llm_egress_policy, get_llm_config
        from utils.llm_utils import get_generate_kwargs
        from utils.provider_idempotency import provider_generate_call
        from utils.runtime_settings import runtime_settings_context

        content = [
            ImageContentPart(data=part, mime_type="image/png")
            if isinstance(part, bytes)
            else TextContentPart(text=part)
            for part in parts
        ]
        with runtime_settings_context(self.settings):
            client = get_client(config=get_llm_config(self.settings))
            kwargs = get_generate_kwargs(
                model=self.model,
                messages=[SystemMessage(content=system), UserMessage(content=content)],
                max_tokens=self.max_tokens,
                response_format=JSONSchemaResponse(name=name, json_schema=schema, strict=False),
            )
            enforce_llm_egress_policy(self.settings)
            if self._deterministic:
                kwargs["temperature"] = 0  # reproducible structure where the provider allows it
            with self._lock:
                self.calls += 1
            try:
                return provider_generate_call(
                    client,
                    provider_id=self.provider,
                    kwargs=kwargs,
                    idempotency_key=None,
                    timeout_seconds=CALL_TIMEOUT_SECONDS,
                )()
            except Exception:
                if "temperature" not in kwargs:
                    raise
                self._deterministic = False  # the provider rejects sampling controls
                kwargs.pop("temperature")
                with self._lock:
                    self.calls += 1
                return provider_generate_call(
                    client,
                    provider_id=self.provider,
                    kwargs=kwargs,
                    idempotency_key=None,
                    timeout_seconds=CALL_TIMEOUT_SECONDS,
                )()
