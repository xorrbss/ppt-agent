"""Generation-specific glue around the shared presentation export router."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import logging
from pathlib import Path
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.image_asset import ImageAsset
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
from services.presentation_export_attempt_service import (
    sha256_canonical_json,
    terminalize_export_attempt_failed,
    terminalize_export_attempt_succeeded,
)
from services.presentation_export_capabilities import PresentationExportContractError
from services.presentation_export_router import (
    PresentationExportExecution,
    execute_presentation_export,
)
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


LOGGER = logging.getLogger(__name__)

# Identity, ownership, share state and the publication token belong to the
# durable target and must never be copied from an isolated staging deck.
_PRESENTATION_RENDER_FIELDS = (
    "content",
    "n_slides",
    "language",
    "title",
    "file_paths",
    "reference_snapshot_ids",
    "outlines",
    "layout",
    "structure",
    "instructions",
    "tone",
    "verbosity",
    "include_table_of_contents",
    "include_title_slide",
    "web_search",
    "theme",
    "deck_plan",
    "mode",
    "version",
)


def _require_exact_export_file(
    export_path: str,
    execution: PresentationExportExecution,
) -> None:
    path = Path(export_path)
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise PresentationExportContractError(
            "presentation_export_artifact_digest_mismatch"
        ) from error
    result = execution.route.result
    if len(payload) != result.artifact_size_bytes or sha256(payload).hexdigest() != (
        result.artifact_sha256
    ):
        raise PresentationExportContractError(
            "presentation_export_artifact_digest_mismatch"
        )


async def _promote_render_staging(
    sql_session: AsyncSession,
    *,
    target: PresentationModel,
    render: PresentationModel,
) -> None:
    """Stage a complete snapshot replacement in the final publication UoW."""

    if target.id == render.id:
        return
    for field_name in _PRESENTATION_RENDER_FIELDS:
        setattr(target, field_name, getattr(render, field_name))
    target.lifecycle_status = PRESENTATION_LIFECYCLE_STAGING
    sql_session.add(target)
    await sql_session.execute(
        delete(SlideModel).where(SlideModel.presentation == target.id)
    )
    await sql_session.execute(
        update(SlideModel)
        .where(SlideModel.presentation == render.id)
        .values(presentation=target.id)
        .execution_options(synchronize_session=False)
    )
    await sql_session.delete(render)


async def publish_staged_presentation(
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
    published_edit_path: str | None = None,
    prepare_artifact=prepare_workspace_generation_artifact,
    publish_artifact=publish_workspace_generation_artifact,
    compensate_artifact=compensate_workspace_artifact,
    stage_image_artifacts=stage_workspace_chat_artifacts_for_publication,
) -> str:
    """Publish once with revision and export attempt as one commit token."""

    expected_revision = presentation.revision
    if generation_job is not None and generation_job.workspace_operation_id is not None:
        if not generation_job.attempt_token:
            await sql_session.rollback()
            raise HTTPException(status_code=409, detail="operation_lease_lost")
        artifact: PreparedWorkspaceArtifact | None = None
        publication_started = False
        try:
            artifact = await prepare_artifact(
                sql_session,
                source_path=export_path,
                operation_id=generation_job.workspace_operation_id,
                presentation_id=presentation.id,
                source_revision=expected_revision + 1,
                export_attempt_id=(
                    export_execution.attempt_id if export_execution is not None else None
                ),
                expected_source_sha256=(
                    export_execution.route.result.artifact_sha256
                    if export_execution is not None
                    else None
                ),
                expected_source_size_bytes=(
                    export_execution.route.result.artifact_size_bytes
                    if export_execution is not None
                    else None
                ),
            )
            if render_presentation is not None:
                await _promote_render_staging(
                    sql_session,
                    target=presentation,
                    render=render_presentation,
                )
            if generated_assets:
                sql_session.add_all(generated_assets)
            if image_staging is not None:
                await stage_image_artifacts(
                    sql_session,
                    staging=image_staging,
                    artifacts=image_artifacts,
                )
            if async_status is not None:
                async_status.message = "Presentation generation completed"
                async_status.status = "completed"
                async_status.data = PresentationPathAndEditPath(
                    presentation_id=presentation.id,
                    path=artifact.public_path,
                    edit_path=(
                        published_edit_path
                        or f"/presentation?id={presentation.id}"
                    ),
                ).model_dump(mode="json")
                async_status.error = None
                async_status.updated_at = datetime.now(timezone.utc)
                sql_session.add(async_status)
            publication_started = True
            await publish_artifact(
                sql_session,
                artifact=artifact,
                expected_revision=expected_revision,
                attempt_token=generation_job.attempt_token,
                export_source_snapshot_sha256=(
                    export_execution.source_snapshot_sha256
                    if export_execution is not None
                    else None
                ),
                export_selected_adapter=(
                    export_execution.route.result.selected_adapter
                    if export_execution is not None
                    else None
                ),
                export_provenance=(
                    export_execution.provenance
                    if export_execution is not None
                    else None
                ),
            )
        except BaseException as error:
            if artifact is not None and not publication_started:
                try:
                    await sql_session.rollback()
                    await compensate_artifact(sql_session, artifact)
                except BaseException:
                    LOGGER.exception(
                        "Failed to compensate unpublished presentation artifact %s",
                        artifact.artifact_id,
                    )
            if isinstance(error, WorkspaceArtifactPublicationError):
                raise HTTPException(status_code=409, detail=error.code) from error
            raise
        assert artifact is not None
        presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
        presentation.revision = expected_revision + 1
        return artifact.public_path

    if export_execution is not None:
        _require_exact_export_file(export_path, export_execution)
    if async_status is not None:
        async_status.message = "Presentation generation completed"
        async_status.status = "completed"
        async_status.data = PresentationPathAndEditPath(
            presentation_id=presentation.id,
            path=export_path,
            edit_path=(published_edit_path or f"/presentation?id={presentation.id}"),
        ).model_dump(mode="json")
        async_status.error = None
        async_status.updated_at = datetime.now(timezone.utc)
        sql_session.add(async_status)
    result = await sql_session.execute(
        update(PresentationModel)
        .where(
            PresentationModel.id == presentation.id,
            PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING,
            PresentationModel.revision == expected_revision,
        )
        .values(
            lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
            revision=PresentationModel.revision + 1,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        await sql_session.rollback()
        raise HTTPException(status_code=409, detail="revision_conflict")
    if export_execution is not None:
        await terminalize_export_attempt_succeeded(
            sql_session,
            attempt_id=export_execution.attempt_id,
            presentation_id=presentation.id,
            expected_owner_id=export_execution.owner_id,
            expected_owner_scope=export_execution.owner_scope,
            expected_workspace_operation_id=export_execution.workspace_operation_id,
            source_revision=expected_revision + 1,
            source_snapshot_sha256=export_execution.source_snapshot_sha256,
            selected_adapter=export_execution.route.result.selected_adapter,
            artifact_sha256=export_execution.route.result.artifact_sha256,
            artifact_size_bytes=export_execution.route.result.artifact_size_bytes,
            provenance=export_execution.provenance,
        )
    await sql_session.commit()
    presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
    presentation.revision = expected_revision + 1
    return export_path


async def execute_generation_export(
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
    """Bind generation retries and sync export to one durable identity."""

    operation = None
    if generation_job is not None and generation_job.workspace_operation_id is not None:
        operation = await sql_session.get(
            WorkspaceOperation,
            generation_job.workspace_operation_id,
        )
        if (
            operation is None
            or operation.presentation_id != publication_target.id
            or operation.owner_scope != get_workspace_owner_scope()
        ):
            raise HTTPException(
                status_code=409,
                detail="workspace_export_operation_invalid",
            )
    request_hash = (
        operation.request_hash
        if operation is not None
        else (
            generation_job.request_sha256
            if generation_job is not None
            else sha256_canonical_json(
                {
                    "operation": "presentation.generate",
                    "request": request.model_dump(mode="json"),
                }
            )
        )
    )
    idempotency_key_hash = (
        operation.idempotency_key_hash
        if operation is not None
        else (
            generation_job.idempotency_key_hash
            if generation_job is not None and generation_job.idempotency_key_hash
            else sha256_canonical_json(
                {
                    "operation": "presentation.generate.export",
                    "presentation_id": str(publication_target.id),
                    "request_hash": request_hash,
                }
            )
        )
    )
    return await execute_export(
        sql_session,
        render_presentation_id=render_presentation.id,
        attempt_presentation_id=publication_target.id,
        export_format=request.export_as,
        expected_render_revision=render_presentation.revision,
        source_revision=publication_target.revision + 1,
        title=render_presentation.title or str(uuid.uuid4()),
        cookie_header=export_cookie_header,
        owner_id=publication_target.owner_id,
        owner_scope=(
            operation.owner_scope if operation is not None else get_workspace_owner_scope()
        ),
        workspace_operation_id=(operation.id if operation is not None else None),
        request_hash=request_hash,
        idempotency_key_hash=idempotency_key_hash,
        template_target=template_target,
    )


async def record_generation_export_failure(
    sql_session: AsyncSession,
    execution: PresentationExportExecution | None,
    error: BaseException,
    *,
    terminalize_failure=terminalize_export_attempt_failed,
) -> None:
    if execution is None:
        return
    await sql_session.rollback()
    await terminalize_failure(
        sql_session,
        attempt_id=execution.attempt_id,
        expected_owner_id=execution.owner_id,
        expected_owner_scope=execution.owner_scope,
        expected_workspace_operation_id=execution.workspace_operation_id,
        failure_code=getattr(
            error,
            "code",
            "presentation_export_unexpected_failure",
        ),
        failure_path=getattr(error, "path", None),
    )


__all__ = [
    "execute_generation_export",
    "publish_staged_presentation",
    "record_generation_export_failure",
]
