"""Atomic confirmation admission for AI-authored template applications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_from_template import RetemplatePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    get_workspace_owner_scope,
)
from models.workspace import (
    WorkspaceBootstrapRequest,
    WorkspaceTemplateApplicationRequest,
)
from services.presentation_generation_job_service import (
    idempotency_key_hash,
    request_sha256,
)
from services.workspace_service import (
    WorkspaceServiceError,
    bootstrap_workspace,
    canonical_payload_hash,
)
from utils.owner_context import get_current_owner_id


GenerationRequestBuilder = Callable[
    [uuid.UUID, RetemplatePresentationRequest, AsyncSession],
    Awaitable[GeneratePresentationRequest],
]


@dataclass(frozen=True, slots=True)
class ConfirmedTemplateApplication:
    source_presentation_id: uuid.UUID
    source_revision: int
    target_presentation_id: uuid.UUID
    conversation: WorkspaceConversation
    message: WorkspaceMessage
    operation: WorkspaceOperation
    duplicate: bool


def _confirmation_payload(
    source_presentation_id: uuid.UUID,
    request: WorkspaceTemplateApplicationRequest,
) -> dict:
    return {
        "source_presentation_id": str(source_presentation_id),
        **request.model_dump(mode="json"),
    }


async def _existing_confirmation(
    session: AsyncSession,
    *,
    key_hash: str,
    confirmation_hash: str,
) -> ConfirmedTemplateApplication | None:
    row = (
        await session.execute(
            select(
                PresentationGenerationJob,
                WorkspaceOperation,
                WorkspaceConversation,
            )
            .join(
                WorkspaceOperation,
                WorkspaceOperation.id
                == PresentationGenerationJob.workspace_operation_id,
            )
            .join(
                WorkspaceConversation,
                WorkspaceConversation.id == WorkspaceOperation.conversation_id,
            )
            .where(PresentationGenerationJob.idempotency_key_hash == key_hash)
        )
    ).one_or_none()
    if row is None:
        return None
    job, operation, conversation = row
    if (
        operation.owner_scope != get_workspace_owner_scope()
        or operation.kind != "template.apply"
    ):
        raise WorkspaceServiceError("presentation_not_found", status_code=404)
    payload = (
        operation.request_payload
        if isinstance(operation.request_payload, dict)
        else {}
    )
    if payload.get("confirmation_request_hash") != confirmation_hash:
        raise WorkspaceServiceError(
            "idempotency_key_payload_conflict", status_code=409
        )
    source_id = payload.get("source_presentation_id")
    source_revision = payload.get("source_revision")
    try:
        parsed_source_id = uuid.UUID(str(source_id))
        parsed_source_revision = int(source_revision)
        message_id = uuid.UUID(str(payload.get("user_message_id")))
    except (TypeError, ValueError) as error:
        raise WorkspaceServiceError(
            "idempotency_record_incomplete", status_code=409
        ) from error
    message = await session.get(WorkspaceMessage, message_id)
    if message is None or message.conversation_id != conversation.id:
        raise WorkspaceServiceError(
            "idempotency_record_incomplete", status_code=409
        )
    return ConfirmedTemplateApplication(
        source_presentation_id=parsed_source_id,
        source_revision=parsed_source_revision,
        target_presentation_id=job.presentation_id,
        conversation=conversation,
        message=message,
        operation=operation,
        duplicate=True,
    )


async def confirm_authored_template_application(
    session: AsyncSession,
    *,
    source_presentation_id: uuid.UUID,
    request: WorkspaceTemplateApplicationRequest,
    idempotency_key: str,
    target_presentation_id: uuid.UUID | None = None,
    generation_request_builder: GenerationRequestBuilder | None = None,
) -> ConfirmedTemplateApplication:
    """Create or reuse the complete target-owned application graph."""

    namespaced_key = (
        "workspace-template-application:"
        f"{source_presentation_id}:{idempotency_key}"
    )
    try:
        key_hash = idempotency_key_hash(namespaced_key)
    except ValueError as error:
        raise WorkspaceServiceError(str(error), status_code=400) from error
    confirmation_hash = canonical_payload_hash(
        _confirmation_payload(source_presentation_id, request)
    )
    existing = await _existing_confirmation(
        session,
        key_hash=key_hash,
        confirmation_hash=confirmation_hash,
    )
    if existing is not None:
        return existing

    owner_id = get_current_owner_id()
    owner_predicate = (
        PresentationModel.owner_id.is_(None)
        if owner_id is None
        else PresentationModel.owner_id == owner_id
    )
    locked_revision = (
        await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == source_presentation_id,
                owner_predicate,
                PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
            )
            .values(updated_at=PresentationModel.updated_at)
            .returning(PresentationModel.revision)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if locked_revision is None:
        raise WorkspaceServiceError("presentation_not_found", status_code=404)
    if int(locked_revision) != request.source_revision:
        raise WorkspaceServiceError(
            "revision_conflict", status_code=409, retryable=True
        )
    source = await session.get(PresentationModel, source_presentation_id)
    if source is None:
        raise WorkspaceServiceError("presentation_not_found", status_code=404)

    if generation_request_builder is None:
        from api.v1.ppt.endpoints.presentation import (
            build_authored_retemplate_generation_request,
        )

        generation_request_builder = build_authored_retemplate_generation_request
    generation_request = await generation_request_builder(
        source_presentation_id,
        RetemplatePresentationRequest(
            authored_style=request.authored_style,
            vision_qa=request.vision_qa,
        ),
        session,
    )

    from api.v1.ppt.endpoints.presentation_generate import (
        _durable_request_payload,
        check_if_api_request_is_valid,
    )

    await check_if_api_request_is_valid(generation_request, session)

    target = source.get_new_presentation()
    if target_presentation_id is not None:
        if target_presentation_id == source_presentation_id:
            raise WorkspaceServiceError(
                "bootstrap_concurrency_conflict", status_code=409
            )
        target.id = target_presentation_id
    target.lifecycle_status = PRESENTATION_LIFECYCLE_STAGING
    target.revision = 0
    target.share_token = None
    target.deleted_at = None
    target.deletion_operation_id = None
    generation_request.presentation_id = target.id
    durable_generation_payload = _durable_request_payload(generation_request)

    bootstrap_request = WorkspaceBootstrapRequest(
        idempotency_key=idempotency_key,
        prompt=request.prompt,
        generation_settings={
            "source_presentation_id": str(source_presentation_id),
            "source_revision": request.source_revision,
            "authored_style": request.authored_style,
            "vision_qa": request.vision_qa,
        },
        operation_type="template.apply",
        expected_revision=0,
    )
    session.add(target)
    try:
        await session.flush()
        conversation, message, operation, _ = await bootstrap_workspace(
            session,
            presentation_id=target.id,
            request=bootstrap_request,
            commit=False,
            initial_operation_state="queued",
        )
        task = AsyncPresentationGenerationTaskModel(
            status="pending",
            message="Queued for generation",
            data=None,
        )
        job = PresentationGenerationJob(
            task_id=task.id,
            presentation_id=target.id,
            workspace_operation_id=operation.id,
            idempotency_key_hash=key_hash,
            request_sha256=request_sha256(durable_generation_payload),
            request_payload=durable_generation_payload,
            export_cookie_required=False,
            state="queued",
        )
        operation.request_payload = {
            **operation.request_payload,
            "confirmation_request_hash": confirmation_hash,
            "source_presentation_id": str(source_presentation_id),
            "source_revision": request.source_revision,
            "target_presentation_id": str(target.id),
            "generation_request": durable_generation_payload,
            "task_id": task.id,
            "job_id": str(job.id),
        }
        session.add_all((task, job, operation))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _existing_confirmation(
            session,
            key_hash=key_hash,
            confirmation_hash=confirmation_hash,
        )
        if existing is None:
            raise WorkspaceServiceError(
                "bootstrap_concurrency_conflict", status_code=409
            )
        return existing

    return ConfirmedTemplateApplication(
        source_presentation_id=source_presentation_id,
        source_revision=request.source_revision,
        target_presentation_id=target.id,
        conversation=conversation,
        message=message,
        operation=operation,
        duplicate=False,
    )
