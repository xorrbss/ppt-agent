"""Repo-local stable-idempotency adapter for confirmed llmai/OpenAI clients."""

from __future__ import annotations

from copy import copy
import logging
from types import MethodType
from typing import Any, Callable, Mapping


LOGGER = logging.getLogger(__name__)

_SUPPORTED_PROVIDER_IDS = frozenset({"openai"})
_LLMAI_OPENAI_MODULE_PREFIX = "llmai.openai."
_LLMAI_CHATGPT_MODULE_PREFIX = "llmai.chatgpt."
_AUTHORED_OUTCOME_SCOPE = "authored"

# One provider call must not outlive its slice of the job budget.  Measured
# three-slide authoring calls land between ~170s and ~240s, so this only fires
# on a call that is genuinely stuck.  The whole-job budgets are enforced
# elsewhere and carry their own codes -- ``public_deadline_exceeded`` from
# admission and ``public_worker_job_expired`` from the isolated worker -- so a
# call that trips this limit is never confused with either.
PROVIDER_CALL_TIMEOUT_SECONDS = 360.0

_TIMEOUT_UNENFORCEABLE_PROVIDERS: set[str] = set()


def _warn_timeout_unenforceable(provider_id: str, reason: str) -> None:
    """Say once, per provider, that this transport cannot bound a call."""

    if provider_id in _TIMEOUT_UNENFORCEABLE_PROVIDERS:
        return
    _TIMEOUT_UNENFORCEABLE_PROVIDERS.add(provider_id)
    LOGGER.warning(
        "provider_call_timeout_not_enforced provider=%s reason=%s seconds=%s",
        provider_id,
        reason,
        PROVIDER_CALL_TIMEOUT_SECONDS,
    )


def _clone_raw_client(
    raw_client: Any, options: Mapping[str, Any], *, provider_id: str
) -> Any:
    """Clone the transport with a request timeout, degrading loudly if refused."""

    try:
        return raw_client.with_options(
            **options, timeout=PROVIDER_CALL_TIMEOUT_SECONDS
        )
    except TypeError:
        # This transport takes no request timeout.  Keep the existing
        # retry/idempotency contract rather than failing the call, and say so.
        _warn_timeout_unenforceable(provider_id, "with_options_rejected_timeout")
        return raw_client.with_options(**options) if options else raw_client


def _is_call_timeout(error: BaseException) -> bool:
    """Recognize a transport timeout across provider SDKs without importing them."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            return False
        visited.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        if "timeout" in type(current).__name__.casefold():
            return True
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return False


class ProviderOutcomeError(RuntimeError):
    """Content-free provider outcome that survives the authored job boundary.

    ``retryable`` deliberately describes whether the *outer operation* may be
    replayed. Authored authoring owns its one bounded in-slide recovery through
    ``recovery_allowed``; after that recovery the durable operation must retain
    the original outcome instead of degrading to attempt-budget exhaustion.
    """

    def __init__(self, code: str, *, recovery_allowed: bool) -> None:
        self.code = code
        self.retryable = False
        self.recovery_allowed = recovery_allowed
        super().__init__(code)


def _exception_status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bridge_recovery_allowed(error: BaseException) -> bool:
    status_code = _exception_status_code(error)
    return status_code is None or status_code == 429 or status_code >= 500


def _provider_outcome_cause(error: BaseException) -> ProviderOutcomeError | None:
    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            return None
        visited.add(id(current))
        if isinstance(current, ProviderOutcomeError):
            return current
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def _content_has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(
            _content_has_text(value.get(key)) for key in ("text", "content")
            if key in value
        )
    if isinstance(value, (list, tuple)):
        return any(_content_has_text(item) for item in value)
    for attribute in ("text", "content"):
        nested = getattr(value, attribute, None)
        if nested is not None and _content_has_text(nested):
            return True
    return False


class _GuardedResponsesResource:
    def __init__(self, resource: Any) -> None:
        self._resource = resource

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        stream = self._resource.create(*args, **kwargs)

        def guarded_events():
            for event in stream:
                event_type = getattr(event, "type", None)
                if event_type == "response.failed":
                    raise ProviderOutcomeError(
                        "public_provider_response_failed",
                        recovery_allowed=True,
                    )
                if event_type == "response.incomplete":
                    raise ProviderOutcomeError(
                        "public_provider_response_incomplete",
                        # The Responses API reports incomplete only after it has
                        # accepted and bounded the request (for example at its
                        # output limit). Replaying the unchanged call is not a
                        # transport recovery.
                        recovery_allowed=False,
                    )
                yield event

        return guarded_events()


class _GuardedResponsesClient:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.responses = _GuardedResponsesResource(client.responses)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def supports_provider_idempotency(client: Any, *, provider_id: str) -> bool:
    """Declare support only for the inspected llmai OpenAI SDK transport."""

    raw_client = getattr(client, "_client", None)
    return bool(
        provider_id in _SUPPORTED_PROVIDER_IDS
        and type(client).__module__.startswith(_LLMAI_OPENAI_MODULE_PREFIX)
        and callable(getattr(raw_client, "with_options", None))
    )


def provider_generate_call(
    client: Any,
    *,
    provider_id: str,
    kwargs: Mapping[str, Any],
    idempotency_key: str | None,
    outcome_scope: str | None = None,
) -> Callable[[], Any]:
    """Return one bounded provider call with optional authored outcome guards.

    The shared llmai client is never mutated, which keeps parallel slide calls
    from leaking one operation's key into another request.
    """

    if outcome_scope not in {None, _AUTHORED_OUTCOME_SCOPE}:
        raise ValueError("provider outcome scope is unsupported")
    generate_kwargs = dict(kwargs)
    is_chatgpt = type(client).__module__.startswith(_LLMAI_CHATGPT_MODULE_PREFIX)
    can_clone_raw_client = callable(
        getattr(getattr(client, "_client", None), "with_options", None)
    )
    should_adapt = supports_provider_idempotency(client, provider_id=provider_id) or (
        outcome_scope == _AUTHORED_OUTCOME_SCOPE
        and is_chatgpt
        and can_clone_raw_client
    )
    adapted_client = client
    options: dict[str, Any] = {}
    if should_adapt:
        options["max_retries"] = 0
        if idempotency_key and supports_provider_idempotency(
            client, provider_id=provider_id
        ):
            options["extra_headers"] = {"Idempotency-Key": idempotency_key}
    if can_clone_raw_client:
        # Clone for the request timeout even when nothing else needs adapting.
        # A stuck call otherwise holds one of the bounded authoring executor
        # threads for the whole job: that executor runs the provider SDK
        # synchronously, so cancelling the awaiting task frees nothing.
        raw_client = _clone_raw_client(
            client._client, options, provider_id=provider_id
        )
        adapted_client = copy(client)
        adapted_client._client = (
            _GuardedResponsesClient(raw_client)
            if outcome_scope == _AUTHORED_OUTCOME_SCOPE and is_chatgpt
            else raw_client
        )
    else:
        _warn_timeout_unenforceable(provider_id, "raw_client_not_cloneable")
    if callable(getattr(adapted_client, "_get_openai_chat_max_tokens_kwargs", None)):
        # llmai 0.3.7 normally retries a rejected output-token parameter once
        # inside `_create_chat_completion`. That hidden second network request
        # would bypass public admission. The cloned request client therefore
        # uses the already selected field exactly once; a 400/422 is surfaced
        # to the explicit, bounded public retry policy.
        def create_chat_completion_once(
            adapted_self: Any,
            *,
            model: str,
            max_tokens: int | None,
            **chat_kwargs: object,
        ) -> Any:
            token_kwargs = adapted_self._get_openai_chat_max_tokens_kwargs(
                max_tokens,
                model=model,
            )
            return adapted_self._client.chat.completions.create(
                model=model,
                **chat_kwargs,
                **token_kwargs,
            )

        adapted_client._create_chat_completion = MethodType(
            create_chat_completion_once,
            adapted_client,
        )
    def generate_once() -> Any:
        try:
            response = adapted_client.generate(**generate_kwargs)
        except Exception as error:
            provider_outcome = _provider_outcome_cause(error)
            if provider_outcome is not None:
                if provider_outcome is error:
                    raise
                raise provider_outcome from error
            if outcome_scope != _AUTHORED_OUTCOME_SCOPE:
                raise
            if _is_call_timeout(error):
                # Distinct from the generic bridge failure so the batch policy
                # can treat a stuck call as overload and the failed slides
                # still get their one recovery call.
                raise ProviderOutcomeError(
                    "public_provider_call_timeout",
                    recovery_allowed=True,
                ) from error
            raise ProviderOutcomeError(
                "public_provider_bridge_exception",
                recovery_allowed=_bridge_recovery_allowed(error),
            ) from error
        if outcome_scope == _AUTHORED_OUTCOME_SCOPE and not _content_has_text(
            getattr(response, "content", None)
        ):
            raise ProviderOutcomeError(
                "public_provider_empty_output",
                recovery_allowed=True,
            )
        return response

    return generate_once


__all__ = [
    "PROVIDER_CALL_TIMEOUT_SECONDS",
    "ProviderOutcomeError",
    "provider_generate_call",
    "supports_provider_idempotency",
]
