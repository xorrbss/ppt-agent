"""Repo-local stable-idempotency adapter for confirmed llmai/OpenAI clients."""

from __future__ import annotations

from copy import copy
from functools import partial
from types import MethodType
from typing import Any, Callable, Mapping


_SUPPORTED_PROVIDER_IDS = frozenset({"openai"})
_LLMAI_OPENAI_MODULE_PREFIX = "llmai.openai."


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
) -> Callable[[], Any]:
    """Return a call whose cloned raw SDK client carries ``extra_headers``.

    The shared llmai client is never mutated, which keeps parallel slide calls
    from leaking one operation's key into another request.
    """

    generate_kwargs = dict(kwargs)
    if not supports_provider_idempotency(client, provider_id=provider_id):
        return partial(client.generate, **generate_kwargs)
    options: dict[str, Any] = {"max_retries": 0}
    if idempotency_key:
        options["extra_headers"] = {"Idempotency-Key": idempotency_key}
    raw_client = client._client.with_options(**options)
    adapted_client = copy(client)
    adapted_client._client = raw_client
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
    return partial(adapted_client.generate, **generate_kwargs)


__all__ = ["provider_generate_call", "supports_provider_idempotency"]
