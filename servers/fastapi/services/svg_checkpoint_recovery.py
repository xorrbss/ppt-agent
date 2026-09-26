"""Explicit recovery for the corrected SVG reference-image policy."""

from sqlalchemy import select
import re

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob

SVG_IMAGE_POLICY_RECOVERY = "svg-reference-image-policy/v2"
SVG_COMMENT_RECOVERY = "svg-comment-normalization/v1"
SVG_PROVIDER_RECOVERY = "svg-provider-overload-recovery/v1"
RASTER_CAPTURE_RECOVERY = "raster-approved-capture/v1"


async def verified_svg_recovery_checkpoints(session, operation, request):
    if (
        request.action != "retry"
        or request.recovery_revision
        not in {SVG_IMAGE_POLICY_RECOVERY, SVG_COMMENT_RECOVERY, SVG_PROVIDER_RECOVERY, RASTER_CAPTURE_RECOVERY}
        or operation.kind != "presentation.generate"
        or operation.state != "failed"
        or operation.error_code
        != (
            "public_provider_overload"
            if request.recovery_revision == SVG_PROVIDER_RECOVERY
            else "svg_validation_failed"
        )
    ):
        return None
    job = await session.scalar(
        select(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.workspace_operation_id == operation.id,
            PresentationGenerationJob.presentation_id == operation.presentation_id,
        )
        .with_for_update()
    )
    if job is None or job.state == "published" or job.request_payload.get("strategy") != "svg":
        return None
    rows = list(
        await session.scalars(
            select(AuthoredSlideCheckpoint)
            .where(
                AuthoredSlideCheckpoint.job_id == job.id,
            )
            .with_for_update()
        )
    )
    if request.recovery_revision == RASTER_CAPTURE_RECOVERY:
        candidates = [row for row in rows if (row.svg_checkpoint or {}).get("quality", {}).get("passed") is not True]
        witnesses = [row.svg_checkpoint or {} for row in candidates]
        if not any(
            payload.get("rasterSourcePageNumber")
            in (payload.get("rasterApprovalPin") or {}).get("full_page_preserve_pages", ())
            and any(
                attempt.get("validation_error") in {"svg_contract_size_limit", "svg_contract_visible_text_required"}
                for attempt in payload.get("attempts", [])
            )
            for payload in witnesses
        ) or any(payload.get("recovery_revision") == RASTER_CAPTURE_RECOVERY for payload in witnesses):
            return None
        return candidates
    expected_error = (
        "svg_required_source_image_missing"
        if request.recovery_revision == SVG_IMAGE_POLICY_RECOVERY
        else "svg_contract_declaration_forbidden"
    )
    if request.recovery_revision != SVG_PROVIDER_RECOVERY and not any(
        any(a.get("validation_error") == expected_error for a in (row.svg_checkpoint or {}).get("attempts", []))
        for row in rows
    ):
        return None
    if request.recovery_revision != SVG_PROVIDER_RECOVERY and any(
        (row.svg_checkpoint or {}).get("recovery_revision") == request.recovery_revision for row in rows
    ):
        return None
    if request.recovery_revision == SVG_COMMENT_RECOVERY:
        from services.svg_contract import normalize_svg_response

        for row in rows:
            for attempt in (row.svg_checkpoint or {}).get("attempts", []):
                if attempt.get("validation_error") == expected_error:
                    raw = attempt.get("raw_response", "")
                    if "<!--" not in raw or re.search(r"<!|<\?", normalize_svg_response(raw)):
                        return None
    if request.recovery_revision != SVG_IMAGE_POLICY_RECOVERY:
        rows = [row for row in rows if not (row.svg_checkpoint or {}).get("quality")]
    return rows


def reset_svg_recovery_checkpoints(session, rows, *, operation_id, now, recovery_revision):
    for row in rows:
        previous = row.svg_checkpoint
        payload = {
            "schema_version": "svg-slide-checkpoint/v2",
            "recovery_revision": recovery_revision,
            "recovery_operation_id": str(operation_id),
            "previous_checkpoint": previous,
        }
        if recovery_revision not in {SVG_IMAGE_POLICY_RECOVERY, RASTER_CAPTURE_RECOVERY}:
            from copy import deepcopy
            from services.svg_contract import normalize_svg_response

            payload = {**deepcopy(previous or {}), **payload, "inflight": None}
            attempts = payload.get("attempts", [])
            if attempts:
                payload["svg_content"] = normalize_svg_response(attempts[-1]["raw_response"])
                attempts[-1]["recovered_validation_error"] = attempts[-1].pop("validation_error", None)
            if recovery_revision == SVG_PROVIDER_RECOVERY:
                payload["provider_failures"] = []
                if (
                    attempts
                    and not attempts[-1].get("recovered_validation_error")
                    and not attempts[-1].get("receipt", {}).get("validation_error")
                ):
                    payload["repair_unavailable"] = True
                elif attempts:
                    payload["attempts"] = []
                    payload["svg_content"] = None
        row.svg_checkpoint = payload
        row.author_status = "completed" if payload.get("svg_content") else "pending"
        row.illustrate_status = "pending"
        row.render_status = "pending"
        row.persist_status = "pending"
        row.failed_stage = None
        row.error = None
        row.failed_at = None
        row.author_completed_at = None
        row.render_completed_at = None
        row.updated_at = now
        session.add(row)
