"""SVG retries require typed transient failures; authored retains its policy."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError, OperationalError, TimeoutError as DatabaseTimeoutError

from services.artifact_store import ArtifactKeyBusy
from services.assets.contracts import AssetGatewayError
from services.isolated_worker_process import IsolatedWorkerProcessError
from services.public_export_errors import PublicExportOperationError
from services.public_export_failure_classification import (
    NON_RETRYABLE_PUBLIC_EXPORT_CODES,
    NON_RETRYABLE_PUBLIC_EXPORT_MARKERS,
    classified_public_export_error,
)
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError
from workers.public_worker_store import PublicWorkerExecutionError


DETERMINISTIC_CODES = (
    "svg_export_manifest_presentation_mismatch",
    "svg_export_slot_not_offered",
    "svg_converter_integrity_failed",
    "svg_export_valueerror",
    "svg_source_export_template_contract_required",
    "legacy_image_path_forbidden",
    "svg_source_export_projection_invalid",
    "svg_source_export_readback_invalid",
    "public_worker_unexpected_failure",
    "public_export_admission_deadline_exhausted",
    "an_unanticipated_future_invariant",
    "artifact.ref_not_found",
    "artifact.metadata_deduplication_failed",
    "trusted_worker_svg_asset_bundle_invalid",
    "NameError",
    "ValidationError",
)


@pytest.mark.parametrize("code", DETERMINISTIC_CODES)
@pytest.mark.parametrize("error_type", [
    PublicExportOperationError, IsolatedWorkerProcessError, PublicWorkerExecutionError,
])
def test_svg_deterministic_codes_do_not_retry_and_preserve_the_reason(code, error_type):
    result = classified_public_export_error(error_type(code), strategy="svg")
    assert result.retryable is False
    assert result.code == code
    assert result.detail["reason_code"] == code


@pytest.mark.parametrize("code", [code for code in DETERMINISTIC_CODES if code.startswith((
    "svg_", "public_", "artifact.", "trusted_worker_", "legacy_",
))])
def test_untyped_source_validator_preserves_its_namespaced_failure_code(code):
    result = classified_public_export_error(ValueError(code), strategy="svg")
    assert result.retryable is False
    assert result.code == code
    assert result.detail["reason_code"] == code


def _postgres_error(state, *, db_type=OperationalError):
    original = Exception("private query and parameters")
    original.sqlstate = state
    return db_type("private SQL", {}, original)


def _sqlite_error(code):
    original = sqlite3.OperationalError("private database")
    original.sqlite_errorcode = code
    return OperationalError("private SQL", {}, original)


@pytest.mark.parametrize("error", [
    _postgres_error("08006"),
    _postgres_error("40001"),
    _postgres_error("40P01"),
    _postgres_error("55P03"),
    _postgres_error("57P01"),
    _postgres_error("08003", db_type=DBAPIError),
    DBAPIError(None, None, Exception(), connection_invalidated=True),
    _sqlite_error(sqlite3.SQLITE_BUSY),
    _sqlite_error(sqlite3.SQLITE_LOCKED | (1 << 8)),
    DatabaseTimeoutError("pool timeout"),
    ConnectionResetError("private host"),
    TimeoutError("private worker"),
    ArtifactKeyBusy(),
    PublicExportOperationError("public_export_singleflight_in_flight"),
    PublicExportOperationError("public_export_readback_evidence_pending"),
    PublicExportOperationError("public_export_editability_evidence_pending"),
    PublicExportOperationError("public_export_worker_failed"),
    PublicExportOperationError("svg_export_worker_failed"),
    PublicExportOperationError("svg_export_asset_lock_timeout"),
    IsolatedWorkerProcessError("deadline_exceeded"),
    IsolatedWorkerProcessError("public_worker_execution_failed"),
    TrustedWorkerWireError("trusted_worker_transport_closed"),
    PublicWorkerExecutionError("public_worker_job_expired"),
    IsolatedWorkerProcessError("svg_export_deadline_exhausted"),
    PublicExportOperationError("public_worker_job_expired"),
    AssetGatewayError("asset_not_ready", status_code=409, retryable=True),
])
def test_svg_only_retries_explicit_infrastructure_and_readiness_types(error):
    result = classified_public_export_error(error, strategy="svg")
    assert result.retryable is True
    assert result.code == "presentation_export_failed"
    assert "reason_code" in result.detail
    assert "private" not in str(result.detail)


@pytest.mark.parametrize("error", [
    NameError("private source"),
    ValueError("private source"),
    TypeError("private source"),
    KeyError("private source"),
    IndexError("private source"),
    RuntimeError("private source"),
    ValueError("private_source_text"),
    RuntimeError("public_export_singleflight_in_flight"),
    ValueError("deadline_exceeded"),
    ValueError("svg_export_asset_lock_timeout"),
    _postgres_error("42P01"),
    _postgres_error("28P01"),
    _sqlite_error(sqlite3.SQLITE_ERROR),
    AssetGatewayError("asset_not_ready", status_code=409, retryable=False),
    AssetGatewayError("unrecognized_asset_error", status_code=500, retryable=True),
])
def test_unknown_or_deterministic_types_cannot_bypass_svg_retry_policy(error):
    result = classified_public_export_error(error, strategy="svg")
    assert result.retryable is False
    assert "private" not in str(result.detail)
    assert "private" not in result.code


def test_pydantic_validation_failure_is_terminal_without_source_text():
    class Page(BaseModel):
        index: int

    with pytest.raises(ValidationError) as raised:
        Page(index="private source")
    result = classified_public_export_error(raised.value, strategy="svg")
    assert result.retryable is False
    assert result.code == "ValidationError"
    assert result.detail == {"reason_code": "ValidationError"}


@pytest.mark.parametrize("code", sorted(NON_RETRYABLE_PUBLIC_EXPORT_CODES) + [
    "prefix_" + marker for marker in NON_RETRYABLE_PUBLIC_EXPORT_MARKERS
] + ["public_export_local_fit_example"])
def test_authored_deterministic_policy_stays_identical(code):
    result = classified_public_export_error(PublicExportOperationError(code), detail={"pages": 2})
    assert result.retryable is False
    assert result.code == code
    assert result.detail == {"pages": 2}


@pytest.mark.parametrize("error", [
    ValueError("free text failure"),
    PublicExportOperationError("unrecognized_failure"),
    PublicExportOperationError("public_export_worker_failed"),
    PublicExportOperationError("public_export_readback_evidence_pending"),
    AssetGatewayError("asset_not_ready", status_code=409, retryable=True),
])
def test_authored_default_retry_policy_stays_identical(error):
    result = classified_public_export_error(error, detail={"pages": 2})
    assert result.retryable is True
    assert result.code == "presentation_export_failed"
    assert result.detail["pages"] == 2


def test_authored_terminal_asset_policy_stays_identical():
    result = classified_public_export_error(AssetGatewayError("asset_not_found", status_code=404))
    assert result.retryable is False
    assert result.code == "asset_not_found"
    assert result.detail == {}


@pytest.mark.parametrize("error", [
    _postgres_error("55P03"), _postgres_error("42P01"),
    DBAPIError(None, None, Exception(), connection_invalidated=True),
    DatabaseTimeoutError("private SQL", code="e3q8"),
])
def test_svg_database_reasons_use_types_without_changing_authored_reasons(error):
    svg = classified_public_export_error(error, strategy="svg")
    assert svg.detail["reason_code"] == type(error).__name__
    authored = classified_public_export_error(error)
    assert authored.retryable is True
    assert authored.code == "presentation_export_failed"
    assert authored.detail == {"reason_code": error.code}
