"""Fence validation before publishing a staged authored scene."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.deck import DeckSceneIR
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PRESENTATION_LIFECYCLE_STAGING, PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from models.sql.authored_semantic_bundle import AuthoredDeckEmissionActivePointer
from models.sql.authored_vnext_repository import AuthoredVNextEntityHead
from services.authored_checkpoint_service import AuthoredStageError


async def _validate_staging_recovery_scene_binding(
    *,
    session: AsyncSession,
    request: GeneratePresentationRequest,
    presentation: PresentationModel,
    canonical_scene: DeckSceneIR | None,
) -> None:
    """Re-check the private candidate fence before it becomes public state."""

    binding = getattr(request, "_authored_staging_scene_binding", None)
    if binding is None:
        return
    if not isinstance(binding, dict) or canonical_scene is None:
        raise AuthoredStageError(
            slide_index=0, stage="persist",
            code="authored_staging_recovery_binding_invalid", retryable=False,
        )
    if (
        binding.get("presentation_id") != str(presentation.id)
        or binding.get("deck_id") != canonical_scene.deck_id
        or binding.get("deck_revision") != canonical_scene.revision
        or binding.get("scene_digest") != canonical_scene.scene_digest
        or binding.get("expected_public_revision") != presentation.revision
        or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_STAGING
        or presentation.revision != 0
    ):
        raise AuthoredStageError(
            slide_index=0, stage="persist",
            code="authored_staging_recovery_binding_stale", retryable=False,
        )
    try:
        operation_id = uuid.UUID(str(binding["operation_id"]))
        job_id = uuid.UUID(str(binding["job_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise AuthoredStageError(
            slide_index=0, stage="persist",
            code="authored_staging_recovery_binding_invalid", retryable=False,
        ) from error
    operation = await session.scalar(
        select(WorkspaceOperation).where(
            WorkspaceOperation.id == operation_id,
            WorkspaceOperation.presentation_id == presentation.id,
            WorkspaceOperation.owner_id == presentation.owner_id,
            WorkspaceOperation.kind.in_(("design.apply", "template.apply")),
            WorkspaceOperation.retry_of_operation_id.is_not(None),
            WorkspaceOperation.state == "running",
            WorkspaceOperation.cancel_requested_at.is_(None),
            WorkspaceOperation.lease_expires_at > datetime.now(timezone.utc),
            WorkspaceOperation.attempt_token == binding.get("attempt_token"),
            WorkspaceOperation.attempt_number == binding.get("attempt_number"),
            WorkspaceOperation.fencing_token == binding.get("fencing_token"),
        )
    )
    job = await session.scalar(
        select(PresentationGenerationJob).where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.presentation_id == presentation.id,
            PresentationGenerationJob.workspace_operation_id == operation_id,
            PresentationGenerationJob.state.in_(("running", "staging")),
            PresentationGenerationJob.attempt_token == binding.get("attempt_token"),
            PresentationGenerationJob.attempt_number == binding.get("attempt_number"),
        )
    )
    head = await session.scalar(
        select(AuthoredVNextEntityHead).where(
            AuthoredVNextEntityHead.owner_id == presentation.owner_id,
            AuthoredVNextEntityHead.entity_kind == "deck",
            AuthoredVNextEntityHead.entity_id == canonical_scene.deck_id,
            AuthoredVNextEntityHead.revision == canonical_scene.revision,
            AuthoredVNextEntityHead.digest == binding.get("deck_digest"),
        )
    )
    active_manifest = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == presentation.owner_id,
            AuthoredDeckEmissionActivePointer.presentation_id == presentation.id,
        )
    )
    if operation is None or job is None or head is None or active_manifest is not None:
        raise AuthoredStageError(
            slide_index=0, stage="persist",
            code="authored_staging_recovery_binding_stale", retryable=False,
        )
