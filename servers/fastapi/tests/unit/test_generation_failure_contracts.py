"""Explicit contracts and typed transient failures survive generation boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import anthropic
import asyncpg
from fastapi import HTTPException
import httpx
from llmai.shared.errors import LLMConnectionError, LLMRateLimitError
import pytest
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeoutError

from models.api_error_model import APIErrorModel
from services.assets.contracts import AssetGatewayError
from services.document_conversion_service import DocumentConversionError
from services.generation_failure_class import classify_generation_failure
from services.presentation_generation_attempt_timing import _generation_exception_failure, _safe_failure
from services.presentation_generation_operation_handler import _generation_failure_decision
from services.presentation_generation_pipeline import (
    _durable_generation_control_http_exception,
    _generic_generation_failure_payload,
)
from services.provider_runtime_primitives import BackpressureError, BudgetExceededError, CircuitOpenError
from services.public_generation_worker_lifecycle import durable_generation_failure
from services.workspace_service import WorkspaceServiceError


@pytest.mark.parametrize("error", [
    WorkspaceServiceError("revision_conflict", retryable=True),
    DocumentConversionError("private subprocess output", retryable=True),
    AssetGatewayError("asset_not_ready", retryable=True),
    AssetGatewayError("asset_not_ready"),
    DocumentConversionError("private subprocess output"),
])
def test_any_stable_exception_contract_survives_all_boundaries(error):
    expected = (error.code, error.retryable)
    assert _generation_exception_failure(error) == {"code": error.code, "retryable": error.retryable}
    assert durable_generation_failure(error) == expected
    mapped = _durable_generation_control_http_exception(error)
    assert durable_generation_failure(mapped) == expected
    payload = _generic_generation_failure_payload(error, mapped, request_id=None)
    assert _safe_failure(json.loads(json.dumps(payload))) == expected
    assert _safe_failure(APIErrorModel.from_exception(mapped).model_dump()) == expected
    assert "private" not in json.dumps(payload)


@pytest.mark.parametrize("error, expected_class", [
    (TimeoutError("private timeout"), "service_interrupted"),
    (ConnectionResetError("private connection"), "service_interrupted"),
    (httpx.ConnectTimeout("private endpoint"), "provider_unstable"),
    (httpx.TransportError("private endpoint"), "provider_unstable"),
    (CircuitOpenError("provider_circuit_open"), "provider_unstable"),
    (BackpressureError("provider_queue_full"), "provider_unstable"),
    (PoolTimeoutError("pool"), "service_interrupted"),
    (LLMConnectionError(503, "private endpoint"), "provider_unstable"),
    (LLMRateLimitError(429, "private response"), "provider_unstable"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://provider.invalid")), "provider_unstable"),
    (anthropic.APITimeoutError(request=httpx.Request("POST", "https://provider.invalid")), "provider_unstable"),
    (anthropic.RateLimitError("private response", response=httpx.Response(
        429, request=httpx.Request("POST", "https://provider.invalid")), body=None), "provider_unstable"),
    (anthropic.InternalServerError("private response", response=httpx.Response(
        529, request=httpx.Request("POST", "https://provider.invalid")), body=None), "provider_unstable"),
    (asyncpg.AdminShutdownError("private shutdown"), "service_interrupted"),
    (DBAPIError("private SQL", {}, asyncpg.AdminShutdownError("private shutdown")), "service_interrupted"),
])
def test_typed_transients_are_retryable_and_have_recoverable_guidance(error, expected_class):
    failure = _generation_exception_failure(error)
    assert failure["retryable"] is True
    assert classify_generation_failure(failure["code"]) == expected_class
    expected = (failure["code"], True)
    assert durable_generation_failure(error) == expected
    safe_exception = HTTPException(500, "Presentation generation failed")
    payload = _generic_generation_failure_payload(error, safe_exception, request_id=None)
    assert _safe_failure(json.loads(json.dumps(payload))) == expected
    assert durable_generation_failure(safe_exception) == expected
    assert "private" not in json.dumps(payload)


@pytest.mark.parametrize("retryable", [False, True])
def test_explicit_decision_precedes_transient_type(retryable):
    error = TimeoutError("private timeout")
    error.code = "custom_timeout"
    error.retryable = retryable
    assert durable_generation_failure(error) == ("custom_timeout", retryable)


@pytest.mark.parametrize("kind", ["actual", "estimated"])
def test_budget_failure_has_limit_guidance_without_parsing_comparison_text(kind):
    error = BudgetExceededError(kind=kind, observed=2, limit=1)
    failure = _generation_exception_failure(error)
    assert failure["retryable"] is False
    assert classify_generation_failure(failure["code"]) == "limit_reached"
    assert durable_generation_failure(error) == (failure["code"], False)


def test_empty_job_error_restores_retry_after_checking_exhausted_page_evidence():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    assert asyncio.run(_generation_failure_decision(session, uuid.uuid4(), {})) == (
        "presentation_generation_failed", True,
    )
    session.execute.assert_awaited_once()
    assert _safe_failure({"retryable": False}) == ("presentation_generation_failed", False)


@pytest.mark.parametrize("code, expected", [
    ("a" * 128, "a" * 128),
    ("a" * 129, "http_error_500"),
    ("public_generation_transition_invalid:rendering:authoring",
     "public_generation_transition_invalid:rendering:authoring"),
    ("invalid_code:a:b:c:d", "http_error_500"),
])
def test_explicit_generation_api_codes_are_bounded(code, expected):
    error = HTTPException(500, "Presentation generation failed")
    error.code = code
    assert APIErrorModel.from_exception(error).code == expected


def test_single_word_requires_an_explicit_code_attribute():
    error = ValueError("hello")
    assert _generation_exception_failure(error)["code"] == "internal_defect"
    error.code = "hello"
    assert _generation_exception_failure(error)["code"] == "hello"
