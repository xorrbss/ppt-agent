"""Generation request persistence and isolated-export staging helpers."""

import re
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from services.canvas_bench_speed import INTERNAL_BENCH_SPEED_ARM_KEY, resolve_speed_arm, validate_speed_arm
from services.svg_authoring_profile import (
    INTERNAL_SVG_AUTHORING_PROFILE_KEY, request_authoring_profile,
    validate_authoring_profile, validate_profile_agreement,
)
from models.authored_vnext.deck import AuthoredCompositionPin, MasterDigestPin
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
from utils.authored_styles import AuthoredStyle
from services.database import sql_engine
from services.authored_local_fit_policy import POLICY_REVISION, font_policy
from services.generation_core.admission.strategy import (
    resolve_generation_strategy,
    recorded_generation_strategy,
)
from services.authored_canvas import (
    request_canvas_metadata,
    restore_request_canvas_metadata,
)
from services.presentation_generation_export_orchestration import (
    publish_staged_presentation,
)
from services.workspace_artifact_publication_service import (
    PreparedWorkspaceArtifact,
    PreparedWorkspaceChatArtifactStaging,
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_generation_artifact,
    publish_workspace_generation_artifact,
    stage_workspace_chat_artifacts_for_publication,
)
from services.authored_rollout_profile import (
    ROLLOUT_PROFILE_PAYLOAD_KEY,
    resolve_rollout_profile,
    rollout_profile_from_payload,
)
from services.workspace_runtime_profile import UnsupportedWorkspaceRuntimeError, require_supported_workspace_runtime

INTERNAL_EXACT_SNAPSHOT_KEY = "_internal_authored_exact_snapshot_id"
INTERNAL_ADMISSION_RETRY_KEY = "_internal_admission_retry_required"
INTERNAL_COMPOSITION_PIN_KEY = "_internal_authored_composition_pin"
INTERNAL_DESIGN_REPLAY_KEY = "_internal_authored_design_replay"
INTERNAL_RESOLVED_MASTER_PIN_KEY = "_internal_authored_resolved_master_pin"
INTERNAL_STYLE_SNAPSHOT_KEY = "_internal_authored_style_snapshot"
INTERNAL_DECK_METADATA_KEY = "_internal_authored_deck_metadata"
INTERNAL_SOURCE_SEMANTIC_SNAPSHOT_KEY = "_internal_authored_source_semantic_snapshot"
INTERNAL_LOCAL_FIT_POLICY_REVISION_KEY = "_internal_authored_local_fit_policy_revision"
INTERNAL_ROLLOUT_PROFILE_KEY = ROLLOUT_PROFILE_PAYLOAD_KEY
INTERNAL_CANVAS_KEY = "_internal_authored_canvas"
_MISSING_CANVAS_PAYLOAD = object()


def _durable_style_snapshot(style: AuthoredStyle) -> dict[str, Any]:
    return {
        "id": style.id,
        "name": style.name,
        "description": style.description,
        "preview_bg": style.preview_bg,
        "preview_accent": style.preview_accent,
        "brief": style.brief,
        "category": style.category,
        "tags": list(style.tags),
        "use_cases": list(style.use_cases),
        "preview_palette": list(style.preview_palette or []) or None,
        "preview_variant": style.preview_variant,
        "primary_color": style.primary_color,
        "background_color": style.background_color,
        "heading_font": style.heading_font,
        "body_font": style.body_font,
        "mono_font": style.mono_font,
        "illustration_prompt": style.illustration_prompt,
        "reference_mode": style.reference_mode,
        "composer_profile": dict(style.composer_profile) if style.composer_profile is not None else None,
    }

def _durable_request_payload(
    request: GeneratePresentationRequest, *, admission_retry: bool = False
) -> dict:
    """Persist canonical identities only, never admission-time raw paths."""

    payload = request.model_dump(mode="json")
    speed_arm = resolve_speed_arm(request)
    if speed_arm is not None:
        payload[INTERNAL_BENCH_SPEED_ARM_KEY] = speed_arm
    from services.generation_core.drivers.svg.collect import INTERNAL_SVG_STAGE_PLAN_KEY, validate_svg_stage_plan
    stage_plan = validate_svg_stage_plan(getattr(request, "_svg_stage_plan", None))
    if stage_plan is not None:
        payload[INTERNAL_SVG_STAGE_PLAN_KEY] = stage_plan
    resolved_strategy, strategy_reason = recorded_generation_strategy(request)
    # Persist the resolved value, never the raw (possibly omitted) field: every
    # downstream consumer that reads this durable payload's "strategy" key as a
    # plain dict (checkpoint recovery, job listing, workspace projections) does
    # a literal "== svg"/"== authored" comparison and has no resolver access.
    payload["strategy"] = resolved_strategy
    # Observability for the silent default-svg-rejected-to-authored case: an
    # operator reading this job's payload can tell "explicit"/"default" apart
    # from a real svg admission violation the caller never saw as a 422.
    payload["_internal_generation_strategy_reason"] = strategy_reason
    incomplete_source_retry = (
        admission_retry
        and resolved_strategy == "svg"
        and request.source_mode == "source_content_restyle"
        and (
            getattr(request, "_document_canvas", None) is None
            or getattr(request, "_svg_source_binding", None) is None
        )
    )
    if admission_retry:
        payload[INTERNAL_ADMISSION_RETRY_KEY] = True
    if resolved_strategy == "svg":
        if not incomplete_source_retry:
            from services.svg_generation_admission import pin_svg_style

            payload["_internal_svg_style_snapshot"] = pin_svg_style(request)
        if request.source_mode == "source_content_restyle" and request._svg_source_binding is not None:
            payload["_internal_svg_source_binding"] = dict(request._svg_source_binding)
    authoring_profile = request_authoring_profile(request)
    if authoring_profile is not None:
        payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] = authoring_profile
    # ``files`` is a compatibility-only admission field. Validation has already
    # converted it to an immutable uploaded-document snapshot. Preserve the
    # optional field's wire shape for old workers while ensuring its value can
    # never contain a locator.
    payload["files"] = None
    if request.strategy == "authored" or str(request.template or "").lower() == "authored":
        if not incomplete_source_retry:
            payload[INTERNAL_CANVAS_KEY] = request_canvas_metadata(request)
            if getattr(request, "_document_canvas", None) is not None:
                from services.document_canvas import canvas_digest

                payload["_internal_canvas_digest"] = canvas_digest(payload[INTERNAL_CANVAS_KEY])
        policy_revision = (
            request._authored_local_fit_policy_revision or POLICY_REVISION
        )
        font_policy(policy_revision)
        request._authored_local_fit_policy_revision = policy_revision
        payload[INTERNAL_LOCAL_FIT_POLICY_REVISION_KEY] = policy_revision
        # Freeze the rollout switches with the job, so every attempt of it --
        # including one resumed after an operator flipped a knob -- authors,
        # normalizes, substitutes and repairs the same way.
        rollout_profile = (
            request._authored_rollout_profile or resolve_rollout_profile().as_dict()
        )
        request._authored_rollout_profile = rollout_profile
        payload[INTERNAL_ROLLOUT_PROFILE_KEY] = rollout_profile
    if request._authored_exact_snapshot_id is not None:
        payload[INTERNAL_EXACT_SNAPSHOT_KEY] = request._authored_exact_snapshot_id
    if request._authored_composition_pin is not None:
        payload[INTERNAL_COMPOSITION_PIN_KEY] = (
            request._authored_composition_pin.model_dump(
                mode="json", by_alias=True
            )
        )
    if request._authored_master_pin is not None:
        payload[INTERNAL_RESOLVED_MASTER_PIN_KEY] = (
            request._authored_master_pin.model_dump(mode="json", by_alias=True)
        )
    if request._authored_style_snapshot is not None:
        payload[INTERNAL_STYLE_SNAPSHOT_KEY] = _durable_style_snapshot(
            request._authored_style_snapshot
        )
    if request._authored_design_replay:
        payload[INTERNAL_DESIGN_REPLAY_KEY] = True
    if request._authored_deck_metadata is not None:
        payload[INTERNAL_DECK_METADATA_KEY] = request._authored_deck_metadata.model_dump(
            mode="json", by_alias=True
        )
    if request._authored_source_semantic_snapshot is not None:
        payload[INTERNAL_SOURCE_SEMANTIC_SNAPSHOT_KEY] = (
            request._authored_source_semantic_snapshot
        )
    return payload


def _chat_first_generation_enabled() -> bool:
    """Fail closed when the SQL runtime cannot host the operation store."""

    try:
        require_supported_workspace_runtime(sql_engine.url)
    except UnsupportedWorkspaceRuntimeError as error:
        raise HTTPException(status_code=501, detail=error.code) from error
    return True


def _uses_isolated_render_staging(
    generation_job: PresentationGenerationJob | None,
) -> bool:
    """Keep exporter checkpoints away from the durable/public deck row."""

    return bool(
        generation_job is not None
        and generation_job.workspace_operation_id is not None
        and generation_job.attempt_token
    )


def _render_staging_id(generation_job: PresentationGenerationJob) -> uuid.UUID:
    """Return an attempt-fenced, unguessable-enough internal render identity."""

    if generation_job.workspace_operation_id is None or not generation_job.attempt_token:
        raise RuntimeError("workspace_render_staging_requires_attempt")
    return uuid.uuid5(
        generation_job.workspace_operation_id,
        f"template-render:{generation_job.attempt_token}",
    )


def _replace_workspace_image_paths(
    value: Any,
    replacements: Mapping[str, str],
) -> Any:
    """Replace provider staging paths throughout persisted JSON values."""

    if isinstance(value, str):
        old_paths = sorted(replacements, key=len, reverse=True)
        if not old_paths:
            return value
        pattern = re.compile("|".join(re.escape(path) for path in old_paths))
        return pattern.sub(lambda match: replacements[match.group(0)], value)
    if isinstance(value, list):
        return [_replace_workspace_image_paths(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(
            _replace_workspace_image_paths(item, replacements) for item in value
        )
    if isinstance(value, dict):
        return {
            key: _replace_workspace_image_paths(item, replacements)
            for key, item in value.items()
        }
    return value


async def _workspace_operation_fencing_token(
    sql_session: AsyncSession,
    *,
    generation_job: PresentationGenerationJob,
    presentation_id: uuid.UUID,
) -> int:
    """Read the exact running attempt fence before allowing provider writes."""

    operation_id = generation_job.workspace_operation_id
    attempt_token = generation_job.attempt_token
    if operation_id is None or not attempt_token:
        raise WorkspaceArtifactPublicationError("operation_lease_lost")
    fencing_token = await sql_session.scalar(
        select(WorkspaceOperation.fencing_token).where(
            WorkspaceOperation.id == operation_id,
            WorkspaceOperation.presentation_id == presentation_id,
            WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == attempt_token,
        )
    )
    if fencing_token is None:
        raise WorkspaceArtifactPublicationError("operation_lease_lost")
    return int(fencing_token)


async def _discard_render_staging(
    sql_session: AsyncSession,
    render_presentation_id: uuid.UUID | None,
    *,
    target_presentation_id: uuid.UUID,
) -> None:
    """Best-effort compensation for an isolated exporter snapshot."""

    if render_presentation_id is None or render_presentation_id == target_presentation_id:
        return
    await sql_session.rollback()
    await sql_session.execute(
        delete(SlideModel).where(SlideModel.presentation == render_presentation_id)
    )
    await sql_session.execute(
        delete(PresentationModel).where(PresentationModel.id == render_presentation_id)
    )
    await sql_session.commit()


async def _publish_staged_presentation(
    sql_session: AsyncSession,
    presentation: PresentationModel,
    export_path: str,
    generation_job: PresentationGenerationJob | None = None,
    async_status: AsyncPresentationGenerationTaskModel | None = None,
    render_presentation: PresentationModel | None = None,
    generated_assets: tuple[ImageAsset, ...] = (),
    image_staging: PreparedWorkspaceChatArtifactStaging | None = None,
    image_artifacts: tuple[PreparedWorkspaceArtifact, ...] = (),
    *,
    prepare_artifact=prepare_workspace_generation_artifact,
    publish_artifact=publish_workspace_generation_artifact,
    compensate_artifact=compensate_workspace_artifact,
    stage_image_artifacts=stage_workspace_chat_artifacts_for_publication,
) -> str:
    return await publish_staged_presentation(
        sql_session,
        presentation,
        export_path,
        generation_job,
        async_status,
        render_presentation,
        generated_assets,
        image_staging,
        image_artifacts,
        prepare_artifact=prepare_artifact,
        publish_artifact=publish_artifact,
        compensate_artifact=compensate_artifact,
        stage_image_artifacts=stage_image_artifacts,
    )


def _request_from_durable_payload(payload: dict) -> GeneratePresentationRequest:
    """Restore private worker state from an internal durable-job payload."""

    values = dict(payload)
    values.pop("_svg_partial_recovery", None)
    speed_arm = validate_speed_arm(values.pop(INTERNAL_BENCH_SPEED_ARM_KEY, None))
    from services.generation_core.drivers.svg.collect import INTERNAL_SVG_STAGE_PLAN_KEY, validate_svg_stage_plan
    stage_plan = validate_svg_stage_plan(values.pop(INTERNAL_SVG_STAGE_PLAN_KEY, None))
    authoring_profile = validate_authoring_profile(values.pop(INTERNAL_SVG_AUTHORING_PROFILE_KEY, None))
    snapshot_id = values.pop(INTERNAL_EXACT_SNAPSHOT_KEY, None)
    composition_payload = values.pop(INTERNAL_COMPOSITION_PIN_KEY, None)
    resolved_master_payload = values.pop(INTERNAL_RESOLVED_MASTER_PIN_KEY, None)
    style_snapshot_payload = values.pop(INTERNAL_STYLE_SNAPSHOT_KEY, None)
    svg_style_snapshot = values.pop("_internal_svg_style_snapshot", None)
    svg_source_binding = values.pop("_internal_svg_source_binding", None)
    design_replay = values.pop(INTERNAL_DESIGN_REPLAY_KEY, False)
    deck_metadata_payload = values.pop(INTERNAL_DECK_METADATA_KEY, None)
    source_semantic_snapshot = values.pop(
        INTERNAL_SOURCE_SEMANTIC_SNAPSHOT_KEY, None
    )
    local_fit_policy_revision = values.pop(
        INTERNAL_LOCAL_FIT_POLICY_REVISION_KEY, None
    )
    rollout_profile_payload = values.pop(INTERNAL_ROLLOUT_PROFILE_KEY, None)
    canvas_payload = values.pop(INTERNAL_CANVAS_KEY, _MISSING_CANVAS_PAYLOAD)
    frozen_canvas_digest = values.pop("_internal_canvas_digest", None)
    # Runner metadata stays on the durable job, outside Pydantic caller input.
    values.pop(INTERNAL_ADMISSION_RETRY_KEY, None)
    strategy_reason = values.pop("_internal_generation_strategy_reason", None)
    if strategy_reason is None and values.get("strategy") is None:
        # A payload durably saved before the strategy resolver existed never
        # pinned this key: its omitted ``strategy`` meant the then-default
        # (authored), not today's default (svg). Re-resolving it now would
        # silently switch a queued or retried pre-deployment authored job to
        # svg the first time it is rehydrated after the upgrade.
        values["strategy"] = "authored"
    from services.public_generation_route import LEGACY_RESTYLE_FIT_POLICY_KEY

    values.pop(LEGACY_RESTYLE_FIT_POLICY_KEY, None)
    request = GeneratePresentationRequest.model_validate(values)
    request._bench_speed_arm = speed_arm
    request._svg_authoring_profile = authoring_profile
    request._svg_stage_plan = stage_plan
    request._generation_strategy_reason = strategy_reason
    request._svg_style_snapshot = svg_style_snapshot
    if svg_style_snapshot is not None:
        validate_profile_agreement(request, svg_style_snapshot)
    request._svg_source_binding = svg_source_binding
    if resolve_generation_strategy(request) == "svg" and request.source_mode == "source_content_restyle":
        if not isinstance(svg_source_binding, dict) and not payload.get(INTERNAL_ADMISSION_RETRY_KEY):
            raise ValueError("svg_source_input_binding_required")
    restore_request_canvas_metadata(
        request,
        canvas_payload,
        present=canvas_payload is not _MISSING_CANVAS_PAYLOAD,
    )
    if getattr(request, "_document_canvas", None) is not None:
        from services.document_canvas import canvas_digest

        if frozen_canvas_digest != canvas_digest(canvas_payload):
            raise ValueError("document_canvas_digest_mismatch")
    if snapshot_id is not None:
        request._authored_exact_snapshot_id = str(snapshot_id)
    if composition_payload is not None:
        request._authored_composition_pin = AuthoredCompositionPin.model_validate(
            composition_payload
        )
    if resolved_master_payload is not None:
        request._authored_master_pin = MasterDigestPin.model_validate(
            resolved_master_payload
        )
    if style_snapshot_payload is not None:
        request._authored_style_snapshot = AuthoredStyle(**style_snapshot_payload)
    if deck_metadata_payload is not None:
        from models.authored_vnext.deck import DeckMetadataSnapshot

        request._authored_deck_metadata = DeckMetadataSnapshot.model_validate(
            deck_metadata_payload
        )
    request._authored_source_semantic_snapshot = source_semantic_snapshot
    request._authored_design_replay = design_replay is True
    if local_fit_policy_revision is not None:
        local_fit_policy_revision = str(local_fit_policy_revision)
        font_policy(local_fit_policy_revision)
        request._authored_local_fit_policy_revision = local_fit_policy_revision
    if rollout_profile_payload is not None:
        # Validate here, at restore, so a job whose pinned profile this build can
        # no longer honour fails with its own code instead of silently running
        # the live switches over slides produced under different ones.
        request._authored_rollout_profile = rollout_profile_from_payload(
            rollout_profile_payload
        ).as_dict()
    return request
