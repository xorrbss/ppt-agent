"""Repository-owned observation of Codex Responses streams before llmai parsing.

No transport retries or request settings are changed here. Unknown stream
results stay unknown; only an actual response.completed permits success.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Mapping

from llmai.chatgpt.client import ChatGPTClient
from openai import APIStatusError

from utils.provider_overload import ProviderOutcomeError, safe_provider_diagnostics
from utils.provider_stream_guard import ProviderCallAborted, close_quietly
from utils.provider_terminal_outcome import ProviderTerminalOutcomeRecorder
from utils.hashing import sha256_hex


_PROVIDER_EVENT_OBSERVER: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "svg_provider_event_observer", default=None)


@contextmanager
def observe_codex_provider_events(observer: Callable[[dict], None]):
    """Bind one SVG attempt's durable observer before its SDK client is made."""
    token = _PROVIDER_EVENT_OBSERVER.set(observer)
    try:
        yield
    finally:
        _PROVIDER_EVENT_OBSERVER.reset(token)


def _completed_text(response: Any) -> str:
    chunks = []
    for item in _get(response, "output") or []:
        if _get(item, "type") != "message":
            continue
        for part in _get(item, "content") or []:
            kind = _get(part, "type")
            value = _get(part, "text") if kind == "output_text" else (
                _get(part, "refusal") if kind == "refusal" else None)
            if value is not None:
                if not isinstance(value, str):
                    raise TypeError("provider_terminal_text_invalid")
                chunks.append(value)
    return "".join(chunks)


def _postresponse_error(error: BaseException, observation: Mapping) -> ProviderOutcomeError:
    return ProviderOutcomeError("public_provider_postresponse_failed", recovery_allowed=False,
        diagnostics=safe_provider_diagnostics(error, diagnostics={**observation,
            "statusOrigin": "local_stream", "terminalEvent": "response.completed",
            "streamStage": "post_response", "failureKind": "local_postresponse"}))


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _stream_failure_diagnostics(error: BaseException) -> dict[str, object]:
    safe = safe_provider_diagnostics(error)
    # Preserve approved SDK metadata while stream observations keep ownership
    # of HTTP/stage/terminal provenance and the existing unknown verdict.
    diagnostics = {
        key: value
        for key, value in safe.items()
        if key not in {
            "exceptionType", "httpStatus", "streamHttpStatus", "statusOrigin",
            "streamStage", "terminalEvent", "failureKind", "streamTermination", "responseId",
        }
    }
    diagnostics.update({
        "statusOrigin": "local_stream",
        "failureKind": "result_unknown",
        "streamTermination": "raised_exception",
    })
    cause_type = safe.get("exceptionType")
    if isinstance(cause_type, str):
        diagnostics["causeExceptionType"] = cause_type
    provider_error_code = safe.get("errorCode")
    if isinstance(provider_error_code, str):
        diagnostics["providerErrorCode"] = provider_error_code
    return diagnostics


class _ObservedResponses:
    def __init__(self, resource: Any, observer=None) -> None:
        self._resource = resource
        self._observer = observer
        self.observation: dict = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        # Admission/HTTP failures before a stream is returned retain their SDK
        # exception. In particular an actual HTTP 429/503 is not a stream drop.
        self.observation = {"streamStage": "before_stream"}
        try:
            if self._observer is not None:
                self._observer({"terminal_event": "dispatch_ready"})
            response = self._resource.create(*args, **kwargs)
        except Exception as error:
            try:
                error.diagnostics = safe_provider_diagnostics(error,
                    diagnostics={"streamStage": "before_stream"})
            except Exception:
                pass
            raise
        if not kwargs.get("stream"):
            return response
        self.observation = safe_provider_diagnostics(diagnostics={
            "streamStage": "stream_opened",
            "streamHttpStatus": _get(_get(response, "response"), "status_code")})
        return self._events(response, observer=self._observer, observation=self.observation)

    @staticmethod
    def _events(stream: Any, *, observer=None, observation=None) -> Iterator[Any]:
        recorder = ProviderTerminalOutcomeRecorder()
        observation = observation if observation is not None else {}
        completed = False
        try:
            for event in stream:
                recorder.observe_event(event)
                event_type = _get(event, "type")
                response = _get(event, "response")
                response_id = safe_provider_diagnostics(diagnostics={
                    "responseId": _get(response, "id")}).get("responseId") or observation.get("responseId")
                if response_id:
                    observation["responseId"] = response_id
                if event_type == "response.created":
                    observation["streamStage"] = "response_created"
                    if observer is not None and response_id:
                        observer({"terminal_event": event_type, "response_id": response_id,
                                  "diagnostics": safe_provider_diagnostics(diagnostics=observation)})
                elif event_type == "response.output_text.delta":
                    observation["streamStage"] = "output_delta"
                if event_type == "response.completed":
                    completed = True
                    observation.update(terminalEvent=event_type, streamStage="terminal_event")
                    if observer is not None:
                        raw = _completed_text(response)
                        observer({"terminal_event": event_type, "response_id": response_id,
                            "raw_response": raw, "output_digest": sha256_hex(raw.encode("utf-8")),
                            "usage": dict(recorder.diagnostics.get("usage") or {}),
                            "diagnostics": safe_provider_diagnostics(diagnostics={
                                **observation, **recorder.diagnostics})})
                elif event_type in {"response.failed", "response.incomplete", "response.cancelled", "error"}:
                    code = {
                        "response.incomplete": "public_provider_response_incomplete",
                        "response.cancelled": "public_provider_response_cancelled",
                    }.get(event_type, "public_provider_response_failed")
                    outcome = ProviderOutcomeError(
                        code,
                        recovery_allowed=event_type in {"response.failed", "error"},
                        diagnostics={**observation, **recorder.diagnostics, "streamStage": "terminal_event"},
                    )
                    outcome.terminal_outcome = recorder.outcome
                    raise outcome
                yield event
        except (ProviderOutcomeError, ProviderCallAborted, APIStatusError):
            raise
        except Exception as error:
            if observer is not None:
                from services.generation_core.drivers.svg.partial_quality_recovery import is_current_provider_control_error
                if is_current_provider_control_error(error):
                    raise
            if completed:
                if observer is not None:
                    raise _postresponse_error(error, observation) from error
                raise
            raise ProviderOutcomeError(
                "public_provider_result_unknown",
                recovery_allowed=False,
                diagnostics={**observation, **_stream_failure_diagnostics(error)},
            ) from error
        else:
            if not completed:
                raise ProviderOutcomeError(
                    "public_provider_result_unknown",
                    recovery_allowed=False,
                    diagnostics={
                        **observation,
                        "statusOrigin": "local_stream",
                        "failureKind": "result_unknown",
                        "streamTermination": "stream_exhausted_without_completion",
                    },
                )
        finally:
            close_quietly(stream)


class _ObservedClient:
    """Keep observation installed when the bounded-call layer clones the SDK."""

    def __init__(self, client: Any, *, observer=None) -> None:
        self._client = client
        self._observer = observer
        self.responses = _ObservedResponses(client.responses, observer)

    @property
    def provider_terminal_observation(self):
        return self.responses.observation

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def with_options(self, **kwargs: Any) -> _ObservedClient:
        return type(self)(self._client.with_options(**kwargs), observer=self._observer)


class RepositoryChatGPTClient(ChatGPTClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = _ObservedClient(self._client, observer=_PROVIDER_EVENT_OBSERVER.get())

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
                if getattr(self._client, "_observer", None) is not None:
                    from services.generation_core.drivers.svg.partial_quality_recovery import is_current_provider_control_error
                    if is_current_provider_control_error(cause):
                        if cause is error:
                            raise
                        raise cause from None
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
            observation = getattr(self._client, "provider_terminal_observation", {})
            if (getattr(self._client, "_observer", None) is not None
                    and observation.get("terminalEvent") == "response.completed"):
                raise _postresponse_error(error, observation) from error
            raise


__all__ = ["RepositoryChatGPTClient"]
