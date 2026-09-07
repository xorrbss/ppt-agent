"""Text-only fallback policy for a failed multimodal author call.

``author_slide_html`` attaches reference and source images to its call.  When
that call fails for a reason the images may have caused, one retry without
them is worth the extra provider call; when the failure is a durable verdict
about the operation, the retry would only spend budget and hide the cause.
The predicate below draws that line in one place.
"""

from __future__ import annotations

from typing import NoReturn

from services.provider_runtime_primitives import RuntimeControlError
from services.provider_usage_instrumentation import ProviderSpendFenceRejected
from services.public_operation_admission import PublicAdmissionRejected
from utils.llm_calls.author_slide_stream import (
    AuthoredStreamAborted,
    output_token_limit_in_cause_chain,
)
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.provider_idempotency import ProviderOutcomeError

BRIDGE_EXCEPTION_CODE = "public_provider_bridge_exception"

# Raised by admission or the runtime control, never by the transport, so the
# images cannot be the cause.  Checked both on the error itself and along its
# ``__cause__`` chain, where the authored outcome scope may have wrapped it.
_ADMISSION_DECISIONS = (
    PublicAdmissionRejected,
    ProviderSpendFenceRejected,
    RuntimeControlError,
)
# Outcomes that survive the authored job boundary unchanged.  A stream abort
# is the authored reader's own verdict on the response, not a client error:
# reclassifying it for HTTP would bury the reason the attempt loop and the
# author-stage rescue read.
_DURABLE_OUTCOMES = (PublicAdmissionRejected, ProviderOutcomeError, AuthoredStreamAborted)


def _admission_decision_in_cause_chain(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            return False
        visited.add(id(current))
        if isinstance(current, _ADMISSION_DECISIONS):
            return True
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return False


def text_only_fallback_applies(error: BaseException) -> bool:
    """Whether a failed multimodal author call may be retried without images.

    Only a client error qualifies: raw (the streaming path wraps nothing), or
    wrapped by the authored outcome scope as
    ``ProviderOutcomeError("public_provider_bridge_exception")`` with the
    ordinary client exception as its ``__cause__``.  ``recovery_allowed`` is
    deliberately not consulted: it says whether the *unchanged* call may be
    replayed, and a 4xx image rejection is exactly what dropping the images
    fixes.

    Everything below propagates instead, because the images did not cause it:

    - ``PublicAdmissionRejected`` (durable admission decisions and budget
      refusals such as ``public_provider_retry_limit_exceeded``,
      ``public_provider_request_limit_exceeded``,
      ``public_provider_estimated_cost_limit_exceeded``,
      ``public_provider_actual_cost_limit_exceeded``,
      ``public_provider_budget_cas_rejected``), ``ProviderSpendFenceRejected``
      (the worker lost the operation fence) and every ``RuntimeControlError``
      (in-process budget, retry-amplification, circuit and cancellation
      contracts), whether raw or anywhere in the ``__cause__`` chain;
    - ``public_provider_call_timeout``: a stuck call classified as overload,
      which must slow the job rather than double its calls;
    - ``public_provider_empty_output``: the provider accepted the call and
      answered with nothing;
    - ``public_provider_response_failed`` and
      ``public_provider_response_incomplete``: terminal Responses API verdicts
      on a request the provider had already accepted;
    - the ``output_token_limit`` stream abort: the provider accepted the call
      and ran out of output budget, which dropping the input images does not
      change, and the retry that helps is the one carrying the size correction.
    """

    if _admission_decision_in_cause_chain(error):
        return False
    if output_token_limit_in_cause_chain(error):
        return False
    if isinstance(error, ProviderOutcomeError):
        return error.code == BRIDGE_EXCEPTION_CODE and isinstance(
            error.__cause__, Exception
        )
    return True


def reraise_author_call_error(error: Exception) -> NoReturn:
    """Re-raise a durable outcome untouched; reclassify anything else for HTTP.

    Public admission limits and provider outcomes are content-free control
    decisions whose ``code``/``retryable``/``recovery_allowed`` contract the
    authored pipeline reads; wrapping them as HTTP errors would hide it.
    """

    if isinstance(error, _DURABLE_OUTCOMES):
        raise error
    raise handle_llm_client_exceptions(error)


__all__ = [
    "BRIDGE_EXCEPTION_CODE",
    "reraise_author_call_error",
    "text_only_fallback_applies",
]
