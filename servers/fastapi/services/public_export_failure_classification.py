"""Retry classification for public export failures, shared by every export path.

The workspace export handler has two execution paths -- the authored/raster
path and the SVG-source path -- and both raise the same public export failure
codes (the admission deadline, worker wire limits, cache budget, ...).  The
classification used to live only in the authored path's catch-all, so an SVG
export that ran out of its pinned admission deadline re-raised a bare
``PublicExportOperationError``; the durable runner recorded it as the generic
retryable ``workspace_operation_internal_error`` and the operation retried a
failure that cannot succeed until it went ``dead_letter``, with the real code
lost from the operation record.

This module is the one place that decides whether a public export failure is
retryable and what the operation record carries:

* non-retryable -> the real failure code is the operation's ``error_code``;
* retryable     -> the historical ``presentation_export_failed`` code, with
  the real failure code preserved as ``error_detail.reason_code``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

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
)
RETRYABLE_PUBLIC_EXPORT_FAILURE_CODE = "presentation_export_failed"

# ``str(error)`` of an arbitrary exception can be a free-text message; only a
# code-shaped value is copied into the operation record.
_REASON_CODE_SHAPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


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


def classified_public_export_error(
    error: BaseException,
    *,
    detail: Mapping[str, Any] | None = None,
) -> WorkspaceOperationExecutionError:
    """Return the typed workspace error a public export failure must raise."""

    code = public_export_error_code(error)
    merged = dict(detail) if isinstance(detail, Mapping) else {}
    if public_export_failure_is_non_retryable(code):
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
