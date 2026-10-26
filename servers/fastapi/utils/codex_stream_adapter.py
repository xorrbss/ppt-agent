"""Repository-owned observation of Codex Responses streams before llmai parsing.

No transport retries or request settings are changed here. Unknown stream
results stay unknown; only an actual response.completed permits success.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time
from typing import Any, Callable, Iterator, Mapping

from llmai.chatgpt.client import ChatGPTClient
from openai import APIStatusError

from utils.provider_overload import ProviderOutcomeError, safe_provider_diagnostics
from utils.provider_stream_guard import ProviderCallAborted, close_quietly
from utils.provider_terminal_outcome import ProviderTerminalOutcomeRecorder
from utils.hashing import sha256_hex


@dataclass(frozen=True)
class _ProviderEventObserver:
    callback: Callable[[dict], None]
    is_control_error: Callable[[BaseException], bool]

    def __call__(self, event: dict) -> None:
        self.callback(event)


_PROVIDER_EVENT_OBSERVER: ContextVar[_ProviderEventObserver | None] = ContextVar(
    "svg_provider_event_observer", default=None)


@contextmanager
def observe_codex_provider_events(
    observer: Callable[[dict], None],
    *,
    is_control_error: Callable[[BaseException], bool],
):
    """Bind one SVG attempt's durable observer before its SDK client is made."""
    token = _PROVIDER_EVENT_OBSERVER.set(_ProviderEventObserver(observer, is_control_error))
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


def _canonical_completed_text(response: Any, *, streamed_text: str) -> tuple[str, str]:
    body = _completed_text(response)
    if body:
        return body, "completed_body"
    if streamed_text:
        return streamed_text, "completed_stream_deltas"
    return body, "completed_empty"


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
        started_at = time.monotonic()
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
        return self._events(response, observer=self._observer, observation=self.observation, started_at=started_at)

    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    def _events(stream: Any, *, observer=None, observation=None, started_at: float | None = None) -> Iterator[Any]:
        recorder = ProviderTerminalOutcomeRecorder()
        observation = observation if observation is not None else {}
        completed = False
        streamed_parts = []
        created_response_id = None
        streamed_items = set()
        # Timing diagnostics: create() start -> first event, largest inter-event gap, event count.
        started_at = time.monotonic() if started_at is None else started_at
        last_event_at = None
        try:
            for event in stream:
                now = time.monotonic()
                if last_event_at is None:
                    observation['firstEventMs'] = max(0, int((now - started_at) * 1000))
                    observation['maxEventGapMs'] = 0
                else:
                    gap_ms = int((now - last_event_at) * 1000)
                    observation['maxEventGapMs'] = max(observation.get('maxEventGapMs', 0), gap_ms)
                last_event_at = now
                observation['eventCount'] = observation.get('eventCount', 0) + 1
                recorder.observe_event(event)
                event_type = _get(event, 'type')
                response = _get(event, 'response')
                response_id = safe_provider_diagnostics(diagnostics={'responseId': _get(response, 'id')}).get('responseId') or observation.get('responseId')
                if response_id:
                    observation['responseId'] = response_id
                if event_type == 'response.created':
                    if created_response_id is not None and response_id != created_response_id:
                        raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False)
                    created_response_id = _get(response, 'id')
                    observation['streamStage'] = 'response_created'
                    if observer is not None and response_id:
                        observer({'terminal_event': event_type, 'response_id': response_id, 'diagnostics': safe_provider_diagnostics(diagnostics=observation)})
                elif event_type == 'response.output_text.delta':
                    observation['streamStage'] = 'output_delta'
                    delta_text = _get(event, 'delta')
                    delta_response_id = _get(event, 'response_id')
                    if delta_response_id is not None and created_response_id is not None and (delta_response_id != created_response_id):
                        raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False)
                    if isinstance(delta_text, str):
                        streamed_parts.append(delta_text)
                        streamed_items.add((_get(event, 'item_id'), _get(event, 'output_index'), _get(event, 'content_index')))
                if event_type == 'response.completed':
                    if created_response_id is not None and response_id != created_response_id:
                        raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False)
                    raw, canonical_text_source = _canonical_completed_text(response, streamed_text=''.join(streamed_parts))
                    if canonical_text_source == 'completed_stream_deltas':
                        completed_response_id = _get(response, 'id')
                        if not created_response_id or completed_response_id != created_response_id or len(streamed_items) != 1:
                            raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False)
                        item_id, output_index, content_index = next(iter(streamed_items))
                        if (not isinstance(item_id, str) or not item_id or type(output_index) is not int or (type(content_index) is not int)) or (output_index < 0 or content_index < 0):
                            raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False)
                    observation.update(canonicalTextSource=canonical_text_source, canonicalTextFallback=canonical_text_source == 'completed_stream_deltas', canonicalTextLength=len(raw))
                    completed = True
                    observation.update(terminalEvent=event_type, streamStage='terminal_event')
                    if observer is not None:
                        observer({'terminal_event': event_type, 'response_id': response_id, 'raw_response': raw, 'output_digest': sha256_hex(raw.encode('utf-8')), 'usage': dict(recorder.diagnostics.get('usage') or {}), 'diagnostics': safe_provider_diagnostics(diagnostics={**observation, **recorder.diagnostics}), 'canonical_text_source': canonical_text_source})
                elif event_type in {'response.failed', 'response.incomplete', 'response.cancelled', 'error'}:
                    code = {'response.incomplete': 'public_provider_response_incomplete', 'response.cancelled': 'public_provider_response_cancelled'}.get(event_type, 'public_provider_response_failed')
                    outcome = ProviderOutcomeError(code, recovery_allowed=event_type in {'response.failed', 'error'}, diagnostics={**observation, **recorder.diagnostics, 'streamStage': 'terminal_event'})
                    outcome.terminal_outcome = recorder.outcome
                    raise outcome
                yield event
        except (ProviderOutcomeError, ProviderCallAborted, APIStatusError):
            raise
        except Exception as error:
            if isinstance(observer, _ProviderEventObserver) and observer.is_control_error(error):
                raise
            if completed:
                if observer is not None:
                    raise _postresponse_error(error, observation) from error
                raise
            raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False, diagnostics={**observation, **_stream_failure_diagnostics(error)}) from error
        else:
            if not completed:
                raise ProviderOutcomeError('public_provider_result_unknown', recovery_allowed=False, diagnostics={**observation, 'statusOrigin': 'local_stream', 'failureKind': 'result_unknown', 'streamTermination': 'stream_exhausted_without_completion'})
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
                observer = getattr(self._client, "_observer", None)
                if isinstance(observer, _ProviderEventObserver) and observer.is_control_error(cause):
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
