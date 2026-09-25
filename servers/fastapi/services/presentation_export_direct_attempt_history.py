"""Derived Hybrid export attempt history projected from immutable attempts.

Attempts are append-only, so active/resolved/superseded resolution and evidence
completeness are derived here at read time rather than stored on the row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.workspace import WorkspaceOperation
from models.sql.workspace import get_workspace_owner_scope
from services.presentation_export_attempt_service import sha256_canonical_json
from services.presentation_export_hybrid_attempt import provenance_is_hybrid_attempt
from utils.owner_context import get_current_owner_id


HYBRID_ATTEMPT_RETENTION_DAYS = 180
HYBRID_EVIDENCE_RETENTION_DAYS = 30


def _projected_value(value: dict[str, Any], snake: str, camel: str) -> Any:
    return value.get(snake) if snake in value else value.get(camel)


async def list_direct_hybrid_export_attempt_history(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
) -> dict[str, Any]:
    """Project immutable attempts into derived active/resolved/superseded history."""

    owner_id = get_current_owner_id()
    owner_scope = get_workspace_owner_scope()
    attempts = (
        await session.execute(
            select(PresentationExportAttempt)
            .where(
                PresentationExportAttempt.presentation_id == presentation_id,
                PresentationExportAttempt.owner_id == owner_id,
                PresentationExportAttempt.owner_scope == owner_scope,
                PresentationExportAttempt.format == "pptx",
                PresentationExportAttempt.state.in_(("succeeded", "failed")),
            )
            .order_by(
                PresentationExportAttempt.created_at.desc(),
                PresentationExportAttempt.id.desc(),
            )
        )
    ).scalars().all()
    attempts = [
        attempt
        for attempt in attempts
        if provenance_is_hybrid_attempt(attempt.provenance)
    ]
    newest_success: dict[tuple[int, str], PresentationExportAttempt] = {}
    for attempt in attempts:
        key = (attempt.source_revision, attempt.format)
        if attempt.state == "succeeded" and key not in newest_success:
            newest_success[key] = attempt

    projected: list[dict[str, Any]] = []
    for attempt in attempts:
        successor = newest_success.get((attempt.source_revision, attempt.format))
        successor_is_later = bool(
            successor is not None
            and successor.id != attempt.id
            and successor.created_at > attempt.created_at
        )
        if successor_is_later:
            resolution = "resolved" if attempt.state == "failed" else "superseded"
        else:
            resolution = "active"
        provenance = attempt.provenance or {}
        operation = (
            await session.get(WorkspaceOperation, attempt.workspace_operation_id)
            if attempt.workspace_operation_id is not None else None
        )
        operation_result = operation.result if operation is not None else {}
        operation_result = operation_result if isinstance(operation_result, dict) else {}
        freshness = provenance.get("freshness") or {}
        parity = provenance.get("visual_parity") or {}
        binding = parity.get("binding") or {}
        qa = parity.get("powerpoint_qa") or provenance.get("powerpoint_qa") or {}
        rendered = qa.get("rendered_png_sha256") or []
        coverage = provenance.get("coverage_manifest") or {}
        quality = provenance.get("quality_sidecar") or {}
        capability = provenance.get("capability_evidence") or {}
        worker_evidence = (
            capability.get("hybrid_export_evidence")
            if isinstance(capability, dict) else {}
        )
        worker_evidence = worker_evidence if isinstance(worker_evidence, dict) else {}
        hybrid_attempt_evidence = operation_result.get("hybrid_attempt_evidence")
        hybrid_attempt_evidence = (
            hybrid_attempt_evidence
            if isinstance(hybrid_attempt_evidence, dict) else {}
        )
        expires_at = (
            attempt.completed_at + timedelta(days=HYBRID_EVIDENCE_RETENTION_DAYS)
            if attempt.completed_at
            else None
        )
        expired = bool(
            expires_at
            and datetime.now(timezone.utc)
            >= expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc)
        )
        evidence_complete = (
            attempt.state == "succeeded"
            and bool(parity)
            and binding.get("presentation_id") == str(presentation_id)
            and binding.get("export_attempt_id") == str(attempt.id)
            and binding.get("presentation_revision") == attempt.source_revision
            and binding.get("canonical_source_sha256")
            == attempt.source_snapshot_sha256
            and binding.get("renderer_source_sha256")
            == freshness.get("renderer_source_sha256")
            and binding.get("pptx_sha256") == attempt.artifact_sha256
        )
        projected.append(
            {
                "exportAttemptId": str(attempt.id),
                "state": attempt.state,
                "resolution": resolution,
                **(
                    {"supersededByAttemptId": str(successor.id)}
                    if successor_is_later and successor is not None
                    else {}
                ),
                "presentationRevision": attempt.source_revision,
                "format": attempt.format,
                "createdAt": attempt.created_at.isoformat(),
                "completedAt": (
                    attempt.completed_at.isoformat() if attempt.completed_at else None
                ),
                "failureCode": attempt.failure_code,
                "evidence": {
                    "status": (
                        "expired"
                        if evidence_complete and expired
                        else "complete" if evidence_complete else "incomplete"
                    ),
                    "attemptId": str(attempt.id),
                    "presentationRevision": attempt.source_revision,
                    "canonicalSourceDigest": attempt.source_snapshot_sha256,
                    "rendererSourceDigest": freshness.get("renderer_source_sha256"),
                    "pptxSha256": attempt.artifact_sha256,
                    "pptxSizeBytes": attempt.artifact_size_bytes,
                    "coverageManifestSha256": parity.get(
                        "coverage_sidecar_sha256"
                    ),
                    "renderManifestSha256": (
                        sha256_canonical_json(rendered) if rendered else None
                    ),
                    "reportManifestSha256": parity.get(
                        "quality_sidecar_sha256"
                    ),
                    "bundleManifestSha256": (
                        sha256_canonical_json(parity) if parity else None
                    ),
                    "slidePngSha256": rendered,
                    "visualParity": parity.get(
                        "status", "failed" if attempt.state == "failed" else "not-verified"
                    ),
                    "nativeText": _projected_value(
                        coverage, "native_text", "nativeText"
                    ),
                    "nativeImage": _projected_value(
                        coverage, "native_image", "nativeImage"
                    ),
                    "nativeShape": _projected_value(
                        coverage, "native_shape", "nativeShape"
                    ),
                    "nativeTable": _projected_value(
                        coverage, "native_table", "nativeTable"
                    ),
                    "nativeChart": _projected_value(
                        coverage, "native_chart", "nativeChart"
                    ),
                    "rasterRemaining": _projected_value(
                        coverage, "raster_remaining", "rasterRemaining"
                    ),
                    "typographyIssues": _projected_value(
                        coverage, "typography_issues", "typographyIssues"
                    )
                    or [],
                    "fallbackReasonCounts": _projected_value(
                        quality, "fallback_reason_counts", "fallbackReasonCounts"
                    )
                    or {},
                    "localFitFallbackPages": _projected_value(
                        hybrid_attempt_evidence or worker_evidence,
                        "local_fit_fallback_pages", "localFitFallbackPages"
                    )
                    or [],
                    "expiresAt": expires_at.isoformat() if expires_at else None,
                },
            }
        )
    return {
        "schemaVersion": "presenton.authored-hybrid-export-history/v1",
        "retention": {
            "attemptRetentionDays": HYBRID_ATTEMPT_RETENTION_DAYS,
            "evidenceRetentionDays": HYBRID_EVIDENCE_RETENTION_DAYS,
            "expiryAction": "mark_expired_retain_metadata",
            "deletionAuthorized": False,
        },
        "attempts": projected,
    }


__all__ = [
    "HYBRID_ATTEMPT_RETENTION_DAYS",
    "HYBRID_EVIDENCE_RETENTION_DAYS",
    "list_direct_hybrid_export_attempt_history",
]
