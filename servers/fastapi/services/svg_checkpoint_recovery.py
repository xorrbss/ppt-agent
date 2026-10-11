"""Explicit recovery for the corrected SVG reference-image policy."""

from sqlalchemy import select
import re

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob

SVG_IMAGE_POLICY_RECOVERY = "svg-reference-image-policy/v2"
SVG_COMMENT_RECOVERY = "svg-comment-normalization/v1"
SVG_PROVIDER_RECOVERY = "svg-provider-overload-recovery/v1"
RASTER_CAPTURE_RECOVERY = "raster-approved-capture/v1"
SVG_TIMEOUT_REPURCHASE = "svg-provider-timeout-repurchase/v1"
SVG_TIMEOUT_REPURCHASE_V2 = "svg-provider-timeout-repurchase/v2"
SVG_TIMEOUT_REPURCHASE_V3 = "svg-provider-timeout-repurchase/v3"
SVG_CLASSIFICATION_REMEASUREMENT = "svg-source-remeasure/v1"
SVG_SOURCE_REMEASUREMENT_DISPATCH = "svg-source-remeasure-dispatch/v1"
SVG_CACHED_AUTHOR_RENDER = "svg-cached-author-render/v1"
SVG_CACHED_AUTHOR_RENDER_V2 = "svg-cached-author-render/v2"
SVG_CACHED_AUTHOR_RENDER_V3 = "svg-cached-author-render/v3"


async def verified_svg_recovery_checkpoints(session, operation, request, *, presentation_revision=None):
    if request.recovery_revision == "svg-partial-recovery/v1":
        from services.svg_partial_recovery import verify_action

        return await verify_action(session, operation, request,
                                   presentation_revision=presentation_revision)
    if request.recovery_revision in {SVG_CACHED_AUTHOR_RENDER, SVG_CACHED_AUTHOR_RENDER_V2,
                                     SVG_CACHED_AUTHOR_RENDER_V3}:
        from services.svg_cached_author_render_recovery import verify_action

        return await verify_action(session, operation, request,
                                   presentation_revision=presentation_revision)
    if request.recovery_revision in {SVG_CLASSIFICATION_REMEASUREMENT, SVG_SOURCE_REMEASUREMENT_DISPATCH}:
        from services.svg_classification_remeasurement import verify_classification_remeasurement

        return await verify_classification_remeasurement(session, operation, request,
                                                         presentation_revision=presentation_revision)
    if request.recovery_revision in {SVG_TIMEOUT_REPURCHASE, SVG_TIMEOUT_REPURCHASE_V2, SVG_TIMEOUT_REPURCHASE_V3}:
        from services.svg_timeout_repurchase import verify_timeout_repurchase

        return await verify_timeout_repurchase(session, operation, request,
                                                presentation_revision=presentation_revision)
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
    if recovery_revision == "svg-partial-recovery/v1":
        raise ValueError("svg_partial_recovery_requires_archived_episode")
    if recovery_revision in {SVG_CACHED_AUTHOR_RENDER, SVG_CACHED_AUTHOR_RENDER_V2,
                             SVG_CACHED_AUTHOR_RENDER_V3}:
        raise ValueError("svg_cached_author_render_requires_append_only_child")
    if recovery_revision in {SVG_CLASSIFICATION_REMEASUREMENT, SVG_SOURCE_REMEASUREMENT_DISPATCH}:
        from services.svg_classification_remeasurement import reset_classification_remeasurement_checkpoints

        reset_classification_remeasurement_checkpoints(session, rows, operation_id=operation_id, now=now)
        return
    if recovery_revision in {SVG_TIMEOUT_REPURCHASE, SVG_TIMEOUT_REPURCHASE_V2, SVG_TIMEOUT_REPURCHASE_V3}:
        from services.svg_timeout_repurchase import reset_timeout_repurchase_checkpoints

        reset_timeout_repurchase_checkpoints(session, rows, operation_id=operation_id, now=now)
        return
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


def append_only_svg_checkpoint_payload(original, witness, lineage, *, cached=False, replacement=False, stage_binding=None):
    from copy import deepcopy
    from utils.hashing import canonical_json_sha256
    from services.svg_timeout_repurchase import TimeoutRepurchaseRejected
    if canonical_json_sha256(original.model_dump(mode="json")) != witness["row_digest"]:
        raise TimeoutRepurchaseRejected("svg_timeout_repurchase_manifest_conflict")
    if cached:
        saved = deepcopy(original.svg_checkpoint or {})
        previous_author = saved.pop("append_only_unstarted", None)
        if previous_author is not None:
            saved["consumed_author_lineage"] = previous_author
        previous_replacement = saved.pop("append_only_replacement", None)
        if previous_replacement is not None:
            saved["consumed_replacement_lineage"] = previous_replacement
        saved["append_only_cache"] = {"witness": deepcopy(witness), "lineage": deepcopy(lineage)}
    elif replacement:
        saved = {"schema_version": "svg-slide-checkpoint/v2", "source_format": "svg",
            "input_digest": witness["input_digest"],
            **{k: deepcopy(v) for k, v in (original.svg_checkpoint or {}).items()
               if k in {"source_manifest_ref", "source_manifest_digest", "visual_intent", "nativeRequirements"}},
            "attempts": [], "inflight": None,
            "append_only_replacement": {"witness": deepcopy(witness), "lineage": deepcopy(lineage)}}
    else:
        saved = deepcopy(original.svg_checkpoint or {})
        saved["append_only_unstarted"] = {"witness": deepcopy(witness), "lineage": deepcopy(lineage)}
    if stage_binding and original.slide_index == 0:
        progress = saved.get("trusted_stage_execution") or {}
        if canonical_json_sha256(progress) != stage_binding["source_progress_digest"]:
            raise TimeoutRepurchaseRejected("svg_timeout_repurchase_manifest_conflict")
        accepted = stage_binding["accepted"]
        if [entry["ordinal"] for entry in accepted] != list(range(len(accepted))):
            raise TimeoutRepurchaseRejected("svg_timeout_repurchase_manifest_conflict")
        saved["inherited_stage_execution"] = {"binding": deepcopy(stage_binding), "original_progress": deepcopy(progress)}
        saved["trusted_stage_execution"] = {**deepcopy(progress), "operation_id": lineage["child_operation_id"],
            "stages": [deepcopy(entry["stage"]) for entry in accepted]}
    return saved
