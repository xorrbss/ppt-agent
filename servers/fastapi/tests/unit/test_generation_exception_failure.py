"""Exception identity and retry policy survive every generation boundary."""

import json

import asyncpg
from fastapi import HTTPException
import httpx
import openai
import pytest
from sqlalchemy.exc import DBAPIError, DataError, OperationalError

from models.api_error_model import APIErrorModel
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_failure_class import classify_generation_failure
from services.presentation_generation_attempt_timing import (
    _generation_exception_failure,
    _safe_failure,
)
from services.presentation_generation_pipeline import (
    _authored_vnext_generation_http_exception,
    _durable_generation_control_http_exception,
    _generic_generation_failure_payload,
)
from services.public_generation_worker_lifecycle import (
    durable_generation_failure,
    mark_exhausted_evidence_unrecorded,
)


@pytest.mark.parametrize("error, code", [
    (ValueError("svg_checkpoint_missing"), "svg_checkpoint_missing"),
    (RuntimeError("svg_asset_revision_binding_invalid"), "svg_asset_revision_binding_invalid"),
    (ValueError("public_generation_transition_invalid:checkpoint_2"),
     "public_generation_transition_invalid:checkpoint_2"),
    (ValueError("a" * 64), "a" * 64),
    (ValueError("a" * 65), "internal_defect"),
    (ValueError("free text with spaces"), "internal_defect"),
    (ValueError("svg_checkpoint_missing: private input"), "internal_defect"),
    (ValueError("svg_checkpoint_missing:abc:def"), "internal_defect"),
    (ValueError("svg_checkpoint_missing:\n"), "internal_defect"),
    (ValueError("svg_체크포인트_missing"), "internal_defect"),
    (ValueError("svg__checkpoint_missing"), "internal_defect"),
    (ValueError("SVG_CHECKPOINT_MISSING"), "internal_defect"),
    (TypeError("Object of type UUID is not JSON serializable"), "internal_defect"),
])
def test_code_and_terminal_decision_survive_serialization_and_durable_paths(error, code):
    safe_exception = HTTPException(500, "Presentation generation failed")
    payload = _generic_generation_failure_payload(error, safe_exception, request_id="request-1")
    assert _safe_failure(json.loads(json.dumps(payload))) == (code, False)
    assert payload["detail"] == {
        "code": code, "retryable": False, "exception_type": type(error).__name__,
    }
    assert payload["message"] == "Presentation generation failed"
    assert payload["request_id"] == "request-1"
    assert durable_generation_failure(error) == (code, False)
    # The synchronous HTTP boundary and the asynchronous status payload agree.
    assert _safe_failure(APIErrorModel.from_exception(safe_exception).model_dump()) == (code, False)
    assert durable_generation_failure(safe_exception) == (code, False)
    if code == "internal_defect":
        assert str(error) not in json.dumps(payload)


def test_explicit_code_wins_and_untyped_retryable_cannot_bypass_policy():
    error = RuntimeError("private message")
    error.code = "svg_checkpoint_missing"
    error.retryable = True
    mapped = _durable_generation_control_http_exception(error)
    assert mapped is not None
    assert _safe_failure(APIErrorModel.from_exception(mapped).model_dump()) == (
        "svg_checkpoint_missing", False,
    )
    assert durable_generation_failure(error) == ("svg_checkpoint_missing", False)
    assert "private message" not in json.dumps(mapped.detail)


def test_invalid_explicit_code_does_not_leak_or_truncate():
    error = ValueError("private message")
    error.code = "private account/identifier"
    error.retryable = True
    mapped = _durable_generation_control_http_exception(error)
    assert mapped.detail == {
        "code": "internal_defect", "retryable": False, "exception_type": "ValueError",
    }


@pytest.mark.parametrize("error", [
    OperationalError("private SQL", {}, ConnectionError("private connection")),
    DBAPIError("private SQL", {}, RuntimeError("private connection"), connection_invalidated=True),
    DBAPIError("private SQL", {}, asyncpg.DeadlockDetectedError("private query")),
    DBAPIError("private SQL", {}, asyncpg.SerializationError("private query")),
    asyncpg.ConnectionDoesNotExistError("connection lost"),
    asyncpg.DeadlockDetectedError("private query"),
    asyncpg.SerializationError("private query"),
    openai.APIConnectionError(request=httpx.Request("POST", "https://provider.invalid")),
    openai.RateLimitError("private response", response=httpx.Response(
        429, request=httpx.Request("POST", "https://provider.invalid")), body=None),
])
def test_only_typed_transients_retry_on_both_boundaries(error):
    failure = _generation_exception_failure(error)
    assert failure["retryable"] is True
    expected = (failure["code"], True)
    assert durable_generation_failure(error) == expected
    payload = _generic_generation_failure_payload(
        error, HTTPException(500, "Presentation generation failed"), request_id=None,
    )
    assert _safe_failure(payload) == expected
    assert "private" not in json.dumps(payload)


@pytest.mark.parametrize("error", [
    RuntimeError("connection_lost"),
    RuntimeError("deadlock_detected"),
    TypeError("not serializable"),
    DataError("private SQL", {}, asyncpg.StringDataRightTruncationError("varchar(64)")),
    DBAPIError("private SQL", {}, RuntimeError("deadlock connection lost")),
])
def test_messages_cannot_make_defects_transient(error):
    assert _generation_exception_failure(error)["retryable"] is False
    assert durable_generation_failure(error)[1] is False


def test_lost_exhausted_page_evidence_still_denies_transient_retry():
    error = OperationalError("private SQL", {}, ConnectionError("lost"))
    mark_exhausted_evidence_unrecorded(error)
    payload = _generic_generation_failure_payload(
        error, HTTPException(500, "Presentation generation failed"), request_id=None,
    )
    assert _safe_failure(payload) == ("e3q8", False)
    assert durable_generation_failure(error) == ("e3q8", False)


@pytest.mark.parametrize("retryable", [False, True])
def test_authored_stage_contract_is_unchanged(retryable):
    error = AuthoredStageError(
        slide_index=0, stage="author", code="authored_slide_author_failed", retryable=retryable,
    )
    assert durable_generation_failure(error) == (error.code, retryable)
    assert _safe_failure(error.as_safe_dict()) == (error.code, retryable)


def test_authored_vnext_mapping_is_unchanged():
    error = ValueError("authored_vnext_structural_delimiter_projection_changed_source_copy")
    mapped = _authored_vnext_generation_http_exception(error)
    assert mapped is not None
    assert _generic_generation_failure_payload(error, mapped, request_id=None) == (
        APIErrorModel.from_exception(mapped).model_dump()
    )


@pytest.mark.parametrize("status, retryable", [(400, False), (429, True), (500, True), (503, True)])
def test_http_exception_contract_is_unchanged(status, retryable):
    error = HTTPException(status, "provider_failure")
    assert durable_generation_failure(error) == ("provider_failure", retryable)
    assert _safe_failure(APIErrorModel.from_exception(error).model_dump()) == (
        "provider_failure", retryable,
    )


def test_explicit_http_control_contract_is_unchanged():
    error = HTTPException(503, "provider_failure")
    error.code = "provider_failure"
    error.retryable = True
    mapped = _durable_generation_control_http_exception(error)
    assert mapped.detail == {"code": "provider_failure", "retryable": True}
    assert APIErrorModel.from_exception(HTTPException(500, "plain_message:suffix")).code == "http_error_500"


@pytest.mark.parametrize("code", [
    "internal_defect", "svg_checkpoint_missing", "svg_asset_revision_binding_invalid",
    "public_generation_transition_invalid:checkpoint_2",
])
def test_defect_guidance_and_historical_recovery_are_distinct(code):
    assert classify_generation_failure(code) == "internal_defect"
    assert code != "http_error_500"
    assert classify_generation_failure("http_error_500") == "service_interrupted"
