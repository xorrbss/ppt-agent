"""Admit legacy authored async-task APIs to the durable workspace runner."""

from __future__ import annotations

from collections.abc import Mapping
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from constants.user_messages import UserMessage, UserMessageLike, message_fields, message_text
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.workspace import WorkspaceCommandRequest
from services.workspace_operation_runner import wake_workspace_operation_runner


async def admit_svg_page_preview_operation(session, *, operation, presentation) -> bool:
    """Bind an existing SVG page preview to public generation accounting."""
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.slide import SlideModel
    from models.sql.workspace import WorkspaceOperation
    from services.authored_canvas import presentation_canvas_contract
    from services.public_operation_admission import (
        ProviderRequestPlan, PublicAdmissionRejected,
        admit_public_operation_compatible_v2, current_principal_context,
        plan_weighted_resources,
    )
    from services.workspace_operation_principal import PRINCIPAL_TENANT_PAYLOAD_KEY
    from models.presentation_from_template import AuthoredPageRemakePreviewRequest
    from services.svg_contract import source_digest

    command_context = operation.request_payload.get("context") or {}
    index = command_context.get("slide_index")
    try:
        preview_request = AuthoredPageRemakePreviewRequest.model_validate({key: command_context[key]
            for key in ("instruction", "mode", "source_digest", "svg_content") if key in command_context})
    except ValueError as error:
        raise PublicAdmissionRejected("page_remake_preview_request_invalid") from error
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise PublicAdmissionRejected("page_remake_preview_request_invalid")
    slide = await session.scalar(select(SlideModel).where(
        SlideModel.presentation == presentation.id, SlideModel.index == index,
        SlideModel.owner_id == presentation.owner_id,
    ))
    if slide is None:
        raise PublicAdmissionRejected("page_remake_preview_request_invalid")
    if not slide.is_svg():
        if preview_request.mode != "author":
            raise PublicAdmissionRejected("page_remake_preview_request_invalid")
        return False
    remeasure = preview_request.mode != "author"
    if remeasure and (operation.expected_revision != presentation.revision
                      or preview_request.source_digest != source_digest(slide.svg_content)):
        raise PublicAdmissionRejected("svg_page_remake_source_changed")
    principal = current_principal_context()
    if (operation.owner_id != principal.owner_id
            or presentation.owner_id != principal.owner_id
            or operation.request_payload.get(PRINCIPAL_TENANT_PAYLOAD_KEY)
            != principal.tenant_id):
        raise PublicAdmissionRejected("public_operation_identity_conflict")
    job = await session.scalar(select(PresentationGenerationJob).join(
        WorkspaceOperation,
        WorkspaceOperation.id == PresentationGenerationJob.workspace_operation_id,
    ).join(PublicOperationAdmission,
        PublicOperationAdmission.operation_id == WorkspaceOperation.id,
    ).where(
        PresentationGenerationJob.presentation_id == presentation.id,
        WorkspaceOperation.presentation_id == presentation.id,
        WorkspaceOperation.owner_id == principal.owner_id,
        WorkspaceOperation.kind == "presentation.generate",
        PublicOperationAdmission.owner_id == principal.owner_id,
        PublicOperationAdmission.tenant_id == principal.tenant_id,
        PublicOperationAdmission.operation_kind == "generation",
    ))
    if job is None:
        raise PublicAdmissionRejected("svg_preview_generation_job_required")

    class PagePreviewRequestPlan(ProviderRequestPlan):
        @property
        def initial_request_limit(self):
            return 0 if remeasure else 1

        @property
        def retry_request_limit(self):
            return 0

    canvas = presentation_canvas_contract(presentation.theme)
    admission = await admit_public_operation_compatible_v2(
        session, principal=principal, operation_id=operation.id,
        operation_kind="generation",
        request_plan=PagePreviewRequestPlan(
            slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0),
        resource_plan=plan_weighted_resources(
            pixel_count=int(canvas.width_px * canvas.height_px),
            device_pixel_ratio_milli=1000, dom_node_count=0, font_count=0,
            svg_complexity=0, estimated_memory_mib=128, fidelity_mode="standard"),
    )
    # A preview has one explicitly admitted author call; policy retry allowance
    # must not turn a lost response or render failure into another purchase.
    admission.retry_request_limit = 0
    operation.request_payload = {
        **operation.request_payload, "provider_usage_job_id": str(job.id),
    }
    await session.flush()
    return True


async def admit_workspace_authored_task(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    operation_kind: str,
    expected_revision: int,
    command: str | UserMessage,
    context: Mapping[str, object],
    task_id_prefix: str,
    pending_message: UserMessageLike,
    allow_local_development: bool = False,
) -> AsyncPresentationGenerationTaskModel:
    """Persist immutable work before returning the legacy polling task.

    The workspace operation/request payload is the recovery authority.  The
    compatibility task id is derived from the immutable operation id, so a
    recovered worker can recreate a missing task row without request memory or
    a Starlette background callback.
    """

    from services.workspace_service import (
        WorkspaceServiceError, canonical_payload_hash, create_workspace_command,
    )

    legacy_identity = canonical_payload_hash({
        "presentation_id": str(presentation_id), "operation_kind": operation_kind,
        "expected_revision": expected_revision, "context": dict(context),
    })

    try:
        operation, _duplicate = await create_workspace_command(
            session,
            presentation_id=presentation_id,
            request=WorkspaceCommandRequest(
                command=message_text(command),
                command_type=operation_kind,
                idempotency_key=(f"legacy-authored:{legacy_identity}"
                    if operation_kind == "slide.regenerate.preview"
                    else f"legacy-authored:{uuid.uuid4()}"),
                expected_revision=expected_revision,
                context=dict(context),
            ),
            content_message=command if isinstance(command, UserMessage) else None,
            allow_local_development=allow_local_development,
        )
    except WorkspaceServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        ) from error

    task_id = f"{task_id_prefix}-{operation.id}"
    task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
    if task is None:
        task = AsyncPresentationGenerationTaskModel(
            id=task_id,
            owner_id=operation.owner_id,
            status="pending",
            **message_fields(pending_message),
            data=None,
        )
        session.add(task)
        await session.commit()
    wake_workspace_operation_runner()
    return task


__all__ = ["admit_workspace_authored_task", "admit_svg_page_preview_operation"]
