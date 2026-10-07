"""Repository-owned observation of Codex Responses streams before llmai parsing.

No transport retries or request settings are changed here. Unknown stream
results stay unknown; only an actual response.completed permits success.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from llmai.chatgpt.client import ChatGPTClient
from openai import APIStatusError

from utils.provider_overload import ProviderOutcomeError, safe_provider_diagnostics
from utils.provider_stream_guard import ProviderCallAborted, close_quietly
from utils.provider_terminal_outcome import ProviderTerminalOutcomeRecorder


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _stream_failure_diagnostics(error: BaseException) -> dict[str, str]:
    safe = safe_provider_diagnostics(error)
    diagnostics = {
        "statusOrigin": "local_stream",
        "failureKind": "result_unknown",
        "streamTermination": "raised_exception",
    }
    cause_type = safe.get("exceptionType")
    if isinstance(cause_type, str):
        diagnostics["causeExceptionType"] = cause_type
    provider_error_code = safe.get("errorCode")
    if isinstance(provider_error_code, str):
        diagnostics["providerErrorCode"] = provider_error_code
    return diagnostics


class _ObservedResponses:
    def __init__(self, resource: Any) -> None:
        self._resource = resource

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        # Admission/HTTP failures before a stream is returned retain their SDK
        # exception. In particular an actual HTTP 429/503 is not a stream drop.
        response = self._resource.create(*args, **kwargs)
        if not kwargs.get("stream"):
            return response
        return self._events(response)

    @staticmethod
    def _events(stream: Any) -> Iterator[Any]:
        recorder = ProviderTerminalOutcomeRecorder()
        completed = False
        try:
            for event in stream:
                recorder.observe_event(event)
                event_type = _get(event, "type")
                if event_type == "response.completed":
                    completed = True
                elif event_type in {"response.failed", "response.incomplete", "response.cancelled", "error"}:
                    code = {
                        "response.incomplete": "public_provider_response_incomplete",
                        "response.cancelled": "public_provider_response_cancelled",
                    }.get(event_type, "public_provider_response_failed")
                    outcome = ProviderOutcomeError(
                        code,
                        recovery_allowed=event_type in {"response.failed", "error"},
                        diagnostics=recorder.diagnostics,
                    )
                    outcome.terminal_outcome = recorder.outcome
                    raise outcome
                yield event
        except (ProviderOutcomeError, ProviderCallAborted, APIStatusError):
            raise
        except Exception as error:
            if completed:
                raise
            raise ProviderOutcomeError(
                "public_provider_result_unknown",
                recovery_allowed=False,
                diagnostics=_stream_failure_diagnostics(error),
            ) from error
        else:
            if not completed:
                raise ProviderOutcomeError(
                    "public_provider_result_unknown",
                    recovery_allowed=False,
                    diagnostics={
                        "statusOrigin": "local_stream",
                        "failureKind": "result_unknown",
                        "streamTermination": "stream_exhausted_without_completion",
                    },
                )
        finally:
            close_quietly(stream)


class _ObservedClient:
    """Keep observation installed when the bounded-call layer clones the SDK."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.responses = _ObservedResponses(client.responses)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def with_options(self, **kwargs: Any) -> _ObservedClient:
        return type(self)(self._client.with_options(**kwargs))


class RepositoryChatGPTClient(ChatGPTClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = _ObservedClient(self._client)

    def _generate_responses_stream(self, **kwargs: Any) -> Iterator[Any]:
        try:
            yield from super()._generate_responses_stream(**kwargs)
        except Exception as error:
            # llmai normalizes arbitrary parser errors to LLMError(status=500).
            # Restore our typed event outcome without assigning an HTTP status.
            pending = [error]
            seen: set[int] = set()
            while pending:
                cause = pending.pop()
                if id(cause) in seen:
                    continue
                seen.add(id(cause))
                if isinstance(cause, ProviderOutcomeError):
                    if cause is error:
                        raise
                    # Re-raising the same cause beneath llmai's wrapper creates
                    # a cause/context cycle. A fresh typed outcome retains the
                    # original exception without the synthetic HTTP 500.
                    restored = ProviderOutcomeError(
                        cause.code,
                        recovery_allowed=cause.recovery_allowed,
                        overload=cause.overload,
                        cooldown_seconds=cause.cooldown_seconds,
                        diagnostics=cause.diagnostics,
                    )
                    restored.terminal_outcome = getattr(cause, "terminal_outcome", None)
                    raise restored from cause
                pending.extend(
                    child for child in (getattr(cause, "cause", None), cause.__cause__, cause.__context__)
                    if isinstance(child, BaseException)
                )
            raise


__all__ = ["RepositoryChatGPTClient"]
