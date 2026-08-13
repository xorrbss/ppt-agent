"""Generation request persistence and isolated-export staging helpers."""

import re
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
from services.database import sql_engine
from services.presentation_generation_export_orchestration import (
    execute_generation_export,
    publish_staged_presentation,
    record_generation_export_failure,
)
from services.presentation_export_attempt_service import terminalize_export_attempt_failed
from services.presentation_export_router import PresentationExportExecution, execute_presentation_export
from services.template_v2_generation_service import TemplateV2GenerationTarget
from services.workspace_artifact_publication_service import (
    PreparedWorkspaceArtifact,
    PreparedWorkspaceChatArtifactStaging,
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_generation_artifact,
    publish_workspace_generation_artifact,
    stage_workspace_chat_artifacts_for_publication,
)
from services.workspace_runtime_profile import UnsupportedWorkspaceRuntimeError, require_supported_workspace_runtime

INTERNAL_EXACT_SNAPSHOT_KEY = "_internal_authored_exact_snapshot_id"
INTERNAL_ADMISSION_RETRY_KEY = "_internal_admission_retry_required"

def _durable_request_payload(request: GeneratePresentationRequest) -> dict:
    """Persist canonical identities only, never admission-time raw paths."""

    payload = request.model_dump(mode="json")
    # ``files`` is a compatibility-only admission field. Validation has already
    # converted it to an immutable uploaded-document snapshot. Preserve the
    # optional field's wire shape for old workers while ensuring its value can
    # never contain a locator.
    payload["files"] = None
    if request._authored_exact_snapshot_id is not None:
        payload[INTERNAL_EXACT_SNAPSHOT_KEY] = request._authored_exact_snapshot_id
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
        f"adaptive-template-render:{generation_job.attempt_token}",
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
    export_execution: PresentationExportExecution | None = None,
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
        export_execution,
        prepare_artifact=prepare_artifact,
        publish_artifact=publish_artifact,
        compensate_artifact=compensate_artifact,
        stage_image_artifacts=stage_image_artifacts,
    )


async def _execute_generation_export(
    sql_session: AsyncSession,
    *,
    publication_target: PresentationModel,
    render_presentation: PresentationModel,
    request: GeneratePresentationRequest,
    export_cookie_header: str | None,
    generation_job: PresentationGenerationJob | None,
    template_target: TemplateV2GenerationTarget | None = None,
    execute_export=execute_presentation_export,
) -> PresentationExportExecution:
    return await execute_generation_export(
        sql_session,
        publication_target=publication_target,
        render_presentation=render_presentation,
        request=request,
        export_cookie_header=export_cookie_header,
        generation_job=generation_job,
        template_target=template_target,
        execute_export=execute_export,
    )


async def _record_generation_export_failure(
    sql_session: AsyncSession,
    execution: PresentationExportExecution | None,
    error: BaseException,
) -> None:
    await record_generation_export_failure(
        sql_session,
        execution,
        error,
        terminalize_failure=terminalize_export_attempt_failed,
    )


def _request_from_durable_payload(payload: dict) -> GeneratePresentationRequest:
    """Restore private worker state from an internal durable-job payload."""

    values = dict(payload)
    snapshot_id = values.pop(INTERNAL_EXACT_SNAPSHOT_KEY, None)
    # Internal runner metadata is deliberately outside the public request
    # schema. It remains on the durable job across operation rebinds, but must
    # never be passed to Pydantic as caller input.
    values.pop(INTERNAL_ADMISSION_RETRY_KEY, None)
    request = GeneratePresentationRequest.model_validate(values)
    if snapshot_id is not None:
        request._authored_exact_snapshot_id = str(snapshot_id)
    return request

