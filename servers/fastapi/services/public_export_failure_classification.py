"""Public export retry policy: SVG requires an explicit transient failure.

Authored retains its historical deterministic exclusions. SVG defaults to a
terminal failure and retries only typed infrastructure and readiness signals.
Both retain the real code; retryable failures use presentation_export_failed
with the original reason in error_detail.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy.exc import DBAPIError, TimeoutError as DatabaseTimeoutError

from services.artifact_store import ArtifactKeyBusy, ArtifactStoreError
from services.assets.contracts import AssetGatewayError
from services.isolated_worker_process import (
    IsolatedWorkerCrashedError, IsolatedWorkerProcessError, IsolatedWorkerTransportClosedError,
)
from services.public_export_errors import PublicExportOperationError
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError

from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationExecutionError,
)

NON_RETRYABLE_PUBLIC_EXPORT_CODES = frozenset(
    {
        "public_export_admission_deadline_exhausted",
        "public_export_delivered_mode_mismatch",
        "public_export_editability_evidence_unavailable",
        "public_export_hybrid_not_editable",
        "public_export_singleflight_fence_stale",
        "public_worker_cache_budget_exceeded",
        "trusted_worker_staging_capacity_exceeded",
        "hybrid_job_wire_limit",
        # A PDF request against a source-preserving SVG deck can never be
        # satisfied by retrying the same request.
        "svg_source_export_format_unsupported",
    }
)
NON_RETRYABLE_PUBLIC_EXPORT_MARKERS = (
    "binding_invalid",
    "digest_mismatch",
    "owner_mismatch",
    "size_mismatch",
    "wire_limit",
    "bundle_too_large",
    "input_too_large",
    "payload_too_large",
    "request_too_large",
    # The worker's evidence is a pure function of the same bundle: a retry
    # would repeat the whole preparation only to fail identically.
    "evidence_too_large",
    "evidence_invalid",
    # Likewise the worker's projection and source-preservation readback of a
    # given bundle are deterministic: three attempts of the same rejection
    # only repeat the preparation (op 31214a0a, 2026-10-21).
    "projection_invalid",
    "readback_invalid",
)
RETRYABLE_PUBLIC_EXPORT_FAILURE_CODE = "presentation_export_failed"

# ``str(error)`` of an arbitrary exception can be a free-text message; only a
# code-shaped value is copied into the operation record.
_REASON_CODE_SHAPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SVG_CODE_SHAPE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")

_TRANSIENT_EXPORT_CODES = frozenset({
    "public_export_singleflight_in_flight",
    "public_export_readback_evidence_pending",
    "public_export_editability_evidence_pending",
    "public_export_worker_failed",
    "svg_export_worker_failed",
    "svg_export_asset_lock_timeout",
})
_TRANSIENT_WORKER_CODES = frozenset({
    "deadline_exceeded",
    "public_worker_job_expired",
    "svg_export_deadline_exhausted",
    "public_worker_execution_failed",
    "trusted_worker_transport_closed",
    "trusted_worker_stdio_read_failed",
    "trusted_worker_stdio_drain_incomplete",
})


def _svg_export_failure_is_transient(error: BaseException) -> bool:
    """Retry only typed infrastructure failures and explicit readiness signals."""
    from workers.public_worker_store import PublicWorkerExecutionError

    if isinstance(error, (
        TimeoutError, ConnectionError, DatabaseTimeoutError, ArtifactKeyBusy, IsolatedWorkerCrashedError,
    )):
        return True
    if isinstance(error, DBAPIError):
        original = error.orig
        state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        sqlite_code = getattr(original, "sqlite_errorcode", None)
        return bool(
            error.connection_invalidated
            or (isinstance(state, str) and (state.startswith("08") or state in {
                "40001", "40P01", "55P03", "57P01", "57P02", "57P03",
            }))
            or (isinstance(sqlite_code, int) and sqlite_code & 0xFF in {
                sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
            })
        )
    if isinstance(error, AssetGatewayError):
        return error.retryable is True and error.code == "asset_not_ready"
    if isinstance(error, PublicExportOperationError):
        return public_export_error_code(error) in _TRANSIENT_EXPORT_CODES | _TRANSIENT_WORKER_CODES
    if isinstance(error, (IsolatedWorkerProcessError, TrustedWorkerWireError, PublicWorkerExecutionError)):
        return public_export_error_code(error) in _TRANSIENT_WORKER_CODES
    return False


def public_export_error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    value = str(error)
    return value if value else "presentation_export_unexpected_failure"


def public_export_failure_is_non_retryable(code: str) -> bool:
    if code.startswith("public_export_local_fit_"):
        return True
    if code in NON_RETRYABLE_PUBLIC_EXPORT_CODES:
        return True
    return any(marker in code for marker in NON_RETRYABLE_PUBLIC_EXPORT_MARKERS)


def _is_terminal_asset_policy_error(error: BaseException) -> bool:
    """Honour the asset gateway's own typed retry decision for local 4xx refusals.

    ``AssetGatewayError`` already states whether a refusal is transient
    (``asset_not_ready``, a canonicalization conflict, ...).  A non-retryable
    client-class refusal is a pure function of the stored rows and bytes, so
    retrying the export would only repeat it.
    """

    return (
        isinstance(error, AssetGatewayError)
        and error.retryable is False
        and isinstance(error.status_code, int)
        and 400 <= error.status_code < 500
    )


def classified_public_export_error(
    error: BaseException,
    *,
    detail: Mapping[str, Any] | None = None,
    strategy: Literal["authored", "svg"] = "authored",
) -> WorkspaceOperationExecutionError:
    """Return the typed workspace error a public export failure must raise."""

    code = public_export_error_code(error)
    merged = dict(detail) if isinstance(detail, Mapping) else {}
    if strategy == "svg":
        from workers.public_svg_projection_error import SvgExportProjectionError, projection_failure_detail

        # Arbitrary exception messages may contain source text. Keep only the
        # stable code or exception type for failures without a public code.
        has_code = isinstance(getattr(error, "code", None), str) or isinstance(error, (
            PublicExportOperationError, IsolatedWorkerProcessError, TrustedWorkerWireError, ArtifactStoreError,
        ))
        # Some source validators still raise ValueError with a namespaced code.
        # Keep that code without persisting arbitrary untyped message text.
        has_code = has_code or code.startswith(("svg_", "public_", "trusted_worker_", "artifact.", "legacy_"))
        code = (code if has_code and _SVG_CODE_SHAPE.fullmatch(code)
                and not isinstance(error, (DBAPIError, DatabaseTimeoutError)) else type(error).__name__)
        merged["reason_code"] = code
        if isinstance(error, SvgExportProjectionError):
            merged.update(error.detail)
        elif code == "svg_source_export_projection_invalid" and "svgProjectionFailure" in merged:
            try:
                merged.update(projection_failure_detail(merged.pop("svgProjectionFailure")))
            except ValueError:
                merged["reason_code"] = "svg_export_projection_failure_detail_invalid"
        if not _svg_export_failure_is_transient(error):
            return NonRetryableWorkspaceOperationError(code, detail=merged)
        return RetryableWorkspaceOperationError(
            RETRYABLE_PUBLIC_EXPORT_FAILURE_CODE, detail=merged,
        )
    if isinstance(error, (IsolatedWorkerCrashedError, IsolatedWorkerTransportClosedError)):
        # Empty crash/shutdown output previously reached the decoder as a generic process
        # error. Preserve authored's exact classification and reason detail.
        merged["reason_code"] = "IsolatedWorkerProcessError"
        return RetryableWorkspaceOperationError(RETRYABLE_PUBLIC_EXPORT_FAILURE_CODE, detail=merged)
    if public_export_failure_is_non_retryable(code) or _is_terminal_asset_policy_error(error):
        return NonRetryableWorkspaceOperationError(code, detail=merged or None)
    merged["reason_code"] = (
        code if _REASON_CODE_SHAPE.match(code) else type(error).__name__
    )
    return RetryableWorkspaceOperationError(
        RETRYABLE_PUBLIC_EXPORT_FAILURE_CODE, detail=merged
    )


__all__ = [
    "NON_RETRYABLE_PUBLIC_EXPORT_CODES",
    "NON_RETRYABLE_PUBLIC_EXPORT_MARKERS",
    "RETRYABLE_PUBLIC_EXPORT_FAILURE_CODE",
    "classified_public_export_error",
    "public_export_error_code",
    "public_export_failure_is_non_retryable",
]
