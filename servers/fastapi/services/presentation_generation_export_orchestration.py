"""Publish a staged generation as one durable, fenced unit of work."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

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
    await sql_session.commit()
    presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
    presentation.revision = expected_revision + 1
    return export_path


__all__ = ["publish_staged_presentation"]
