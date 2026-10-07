"""Durable workspace handlers for authored page-remake previews."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any
import uuid

from sqlalchemy import select

from constants.user_messages import UserMessageKey, UserMessageLike, message_fields, user_message
from models.authored_batch_remake import AuthoredBatchRemakePreviewRequest
from models.presentation_from_template import AuthoredPageRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from services.authored_batch_remake_service import (
    BATCH_PREVIEW_KIND,
    MAX_BATCH_SLIDES,
    _pending_entry,
    _record_child_outcome,
    _run_batch_preview_background,
)
from services.authored_page_remake_service import _run_preview_background
from services.database import async_session_maker
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)


PAGE_PREVIEW_OPERATION_KIND = "slide.regenerate.preview"
BATCH_PREVIEW_OPERATION_KIND = "slide.regenerate.batch.preview"
_PROGRESS_INTERVAL_SECONDS = 15.0
_PREVIEW_LEASE_SECONDS = 1800


def _context(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("context")
    return value if isinstance(value, Mapping) else {}


async def _await_with_progress(
    context: WorkspaceOperationExecutionContext,
    awaitable: Awaitable[None],
    *,
    stage: str,
    initial_percent: int,
) -> None:
    work = asyncio.create_task(awaitable)
    percent = initial_percent
    try:
        while True:
            done, _pending = await asyncio.wait(
                {work}, timeout=_PROGRESS_INTERVAL_SECONDS
            )
            if work in done:
                await work
                return
            await context.raise_if_cancelled()
            percent = min(95, percent + 5)
            if await context.progress(stage=stage, percent=percent) is False:
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
    finally:
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)


def _page_task_id(operation_id: object) -> str:
    return f"workspace-page-preview-{operation_id}"


def _batch_task_id(operation_id: object) -> str:
    return f"workspace-batch-preview-{operation_id}"


def _batch_child_task_id(operation_id: object, slide_index: int) -> str:
    return f"workspace-page-preview-{operation_id}-{slide_index}"


async def _ensure_task(
    task_id: str,
    *,
    owner_id: object,
    message: UserMessageLike,
    data: dict[str, Any] | None = None,
) -> AsyncPresentationGenerationTaskModel:
    async with async_session_maker() as session:
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            task = AsyncPresentationGenerationTaskModel(
                id=task_id,
                owner_id=owner_id,
                status="pending",
                **message_fields(message),
                data=data,
            )
            session.add(task)
            await session.commit()
        elif task.owner_id != owner_id:
            raise NonRetryableWorkspaceOperationError(
                "workspace_operation_principal_conflict"
            )
        return task


async def _svg_preview_binding(context):
    """Read the exact owned preview admission and its existing accounting job."""
    from models.sql.presentation import PresentationModel
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.slide import SlideModel
    from services.workspace_operation_principal import PRINCIPAL_TENANT_PAYLOAD_KEY
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    claim = context.claim
    index = _context(claim.request_payload).get("slide_index")
    async with async_session_maker() as session:
        presentation = await session.scalar(select(PresentationModel).where(
            PresentationModel.id == claim.presentation_id,
            PresentationModel.owner_id == claim.owner_id,
        ))
        slide = await session.scalar(select(SlideModel).where(
            SlideModel.presentation == claim.presentation_id, SlideModel.index == index,
            SlideModel.owner_id == claim.owner_id,
        ))
        if presentation is None or slide is None:
            raise NonRetryableWorkspaceOperationError("workspace_operation_principal_conflict")
        if not slide.is_svg():
            return None
        remeasure = _context(claim.request_payload).get("mode", "author") != "author"
        if remeasure:
            from services.svg_contract import source_digest
            if (int(presentation.revision) != claim.expected_revision
                    or _context(claim.request_payload).get("source_digest") != source_digest(slide.svg_content)):
                raise NonRetryableWorkspaceOperationError("svg_page_remake_source_changed")
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == claim.operation_id,
            PublicOperationAdmission.owner_id == claim.owner_id,
            PublicOperationAdmission.operation_kind == "generation",
        ))
        tenant = claim.request_payload.get(PRINCIPAL_TENANT_PAYLOAD_KEY)
        if (claim.owner_id is None or get_current_owner_id() != claim.owner_id
                or not tenant or get_current_tenant_id() != tenant
                or admission is None or admission.tenant_id != tenant
                or admission.state != "active" or admission.slide_count != 1
                or admission.planned_request_limit != (0 if remeasure else 1) or admission.retry_request_limit != 0):
            raise NonRetryableWorkspaceOperationError("workspace_operation_principal_conflict")
        try:
            job_id = uuid.UUID(str(claim.request_payload.get("provider_usage_job_id")))
        except (ValueError, TypeError) as error:
            raise NonRetryableWorkspaceOperationError("svg_preview_generation_job_required") from error
        job = await session.scalar(select(PresentationGenerationJob).where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.presentation_id == claim.presentation_id,
        ))
        if job is None:
            raise NonRetryableWorkspaceOperationError("svg_preview_generation_job_required")
        return admission.id, job.id


async def _assert_preview_not_dispatched(admission_id):
    from models.sql.public_admission import PublicProviderLogicalCall

    async with async_session_maker() as session:
        witness = await session.scalar(select(PublicProviderLogicalCall.id).where(
            PublicProviderLogicalCall.admission_id == admission_id,
        ).limit(1))
    if witness is not None:
        raise NonRetryableWorkspaceOperationError("svg_preview_provider_recovery_required")


async def _run_fenced_svg_preview(context, binding, slide_index, request, task_id):
    from services.provider_usage_instrumentation import (
        provider_usage_scope, public_operation_scope,
    )

    claim = context.claim
    await _assert_preview_not_dispatched(binding[0])
    if request.mode != "author":
        await _run_preview_background(claim.presentation_id, slide_index, request, task_id)
        return
    with public_operation_scope(claim.operation_id, attempt_token=claim.attempt_token,
                                fencing_token=claim.fencing_token):
        # The real attempt is fenced separately. Keep this logical author slot
        # fixed so takeover cannot create another reservation or SDK purchase.
        with provider_usage_scope(job_id=binding[1],
                                  operation=f"svg_page_preview_{claim.operation_id.hex}",
                                  stage="author", attempt_number=1,
                                  slide_index=slide_index):
            await _run_preview_background(claim.presentation_id, slide_index, request, task_id)


class WorkspacePageRemakePreviewOperationHandler:
    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        command_context = _context(claim.request_payload)
        slide_index = command_context.get("slide_index")
        instruction = command_context.get("instruction")
        if (
            isinstance(slide_index, bool)
            or not isinstance(slide_index, int)
            or slide_index < 0
            or (instruction is not None and not isinstance(instruction, str))
        ):
            raise NonRetryableWorkspaceOperationError(
                "page_remake_preview_request_invalid"
            )
        try:
            request = AuthoredPageRemakePreviewRequest(instruction=instruction,
                mode=command_context.get("mode", "author"), source_digest=command_context.get("source_digest"),
                svg_content=command_context.get("svg_content"))
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(
                "page_remake_preview_request_invalid"
            ) from error

        svg_binding = await _svg_preview_binding(context)

        task_id = _page_task_id(claim.operation_id)
        task = await _ensure_task(
            task_id,
            owner_id=claim.owner_id,
            message=user_message(UserMessageKey.REMAKE_PAGE_QUEUED, page=slide_index + 1),
        )
        if not (
            task.status == "completed"
            and isinstance(task.data, dict)
            and task.data.get("phase") == "preview_ready"
        ):
            await context.raise_if_cancelled()
            await context.extend_lease(seconds=_PREVIEW_LEASE_SECONDS)
            if (
                await context.progress(
                    stage="page_remake_previewing", percent=10
                )
                is False
            ):
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
            await _await_with_progress(
                context,
                _run_fenced_svg_preview(context, svg_binding, slide_index, request, task_id)
                if svg_binding is not None else _run_preview_background(
                    claim.presentation_id,
                    slide_index,
                    request,
                    task_id,
                ),
                stage="page_remake_previewing",
                initial_percent=10,
            )

        async with async_session_maker() as session:
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            if (
                task is not None
                and task.status == "completed"
                and isinstance(task.data, dict)
                and task.data.get("phase") == "preview_ready"
            ):
                return WorkspaceOperationResult(
                    payload={
                        "status": "completed",
                        "task_id": task_id,
                        "current": 1,
                        "total": 1,
                    },
                    result_revision=claim.expected_revision,
                )
            error = task.error if task is not None and isinstance(task.error, dict) else {}
            code = error.get("detail")
            if not isinstance(code, str) or not code:
                code = "authored_page_remake_failed"
            if svg_binding is not None:
                raise NonRetryableWorkspaceOperationError(code)
            if bool(error.get("retryable")):
                raise RetryableWorkspaceOperationError(code)
            raise NonRetryableWorkspaceOperationError(code)


class WorkspaceBatchRemakePreviewOperationHandler:
    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        command_context = _context(claim.request_payload)
        slide_indexes = command_context.get("slide_indexes")
        instruction = command_context.get("instruction")
        if (
            not isinstance(slide_indexes, Sequence)
            or isinstance(slide_indexes, (str, bytes, bytearray))
            or not slide_indexes
            or len(slide_indexes) > MAX_BATCH_SLIDES
            or (instruction is not None and not isinstance(instruction, str))
        ):
            raise NonRetryableWorkspaceOperationError(
                "batch_remake_preview_request_invalid"
            )
        try:
            request = AuthoredBatchRemakePreviewRequest(
                slide_indexes=list(slide_indexes),
                instruction=instruction,
            )
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(
                "batch_remake_preview_request_invalid"
            ) from error

        pairs = tuple(
            (index, _batch_child_task_id(claim.operation_id, index))
            for index in sorted(request.slide_indexes)
        )
        parent_id = _batch_task_id(claim.operation_id)
        parent_data = {
            "kind": BATCH_PREVIEW_KIND,
            "phase": "previewing",
            "presentation_id": str(claim.presentation_id),
            "total": len(pairs),
            "completed": 0,
            "slides": [_pending_entry(index, task_id) for index, task_id in pairs],
        }
        parent = await _ensure_task(
            parent_id,
            owner_id=claim.owner_id,
            message=user_message(UserMessageKey.REMAKE_BATCH_RUNNING, total=len(pairs)),
            data=parent_data,
        )
        if parent.status != "completed":
            for index, child_id in pairs:
                await _ensure_task(
                    child_id,
                    owner_id=claim.owner_id,
                    message=user_message(UserMessageKey.REMAKE_PAGE_QUEUED, page=index + 1),
                )

            retry_pairs: list[tuple[int, str]] = []
            for index, child_id in pairs:
                async with async_session_maker() as session:
                    child = await session.get(
                        AsyncPresentationGenerationTaskModel, child_id
                    )
                settled = child is not None and child.status in {"completed", "error"}
                nonretryable = bool(
                    settled
                    and child is not None
                    and isinstance(child.error, dict)
                    and child.error.get("retryable") is False
                )
                ready = bool(
                    child is not None
                    and child.status == "completed"
                    and isinstance(child.data, dict)
                    and child.data.get("phase") == "preview_ready"
                )
                if ready or nonretryable:
                    await _record_child_outcome(parent_id, index, child_id)
                else:
                    retry_pairs.append((index, child_id))

            if retry_pairs:
                await context.raise_if_cancelled()
                await context.extend_lease(
                    seconds=_PREVIEW_LEASE_SECONDS * len(retry_pairs)
                )
                await _await_with_progress(
                    context,
                    _run_batch_preview_background(
                        claim.presentation_id,
                        request,
                        parent_id,
                        tuple(retry_pairs),
                    ),
                    stage="batch_remake_previewing",
                    initial_percent=5,
                )

        async with async_session_maker() as session:
            parent = await session.get(
                AsyncPresentationGenerationTaskModel, parent_id
            )
            if parent is None or parent.status != "completed":
                raise RetryableWorkspaceOperationError(
                    "authored_batch_remake_preview_incomplete"
                )
            data = parent.data if isinstance(parent.data, dict) else {}
            return WorkspaceOperationResult(
                payload={
                    "status": "completed",
                    "task_id": parent_id,
                    "completed": int(data.get("completed") or 0),
                    "total": int(data.get("total") or len(pairs)),
                },
                result_revision=claim.expected_revision,
            )


def register_workspace_authored_preview_operation_handlers(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> tuple[
    WorkspacePageRemakePreviewOperationHandler,
    WorkspaceBatchRemakePreviewOperationHandler,
]:
    page = WorkspacePageRemakePreviewOperationHandler()
    batch = WorkspaceBatchRemakePreviewOperationHandler()
    registry.register(
        PAGE_PREVIEW_OPERATION_KIND,
        page,
        replace=replace,
        recovery_policy="automatic",
    )
    registry.register(
        BATCH_PREVIEW_OPERATION_KIND,
        batch,
        replace=replace,
        recovery_policy="automatic",
    )
    return page, batch


__all__ = [
    "BATCH_PREVIEW_OPERATION_KIND",
    "PAGE_PREVIEW_OPERATION_KIND",
    "WorkspaceBatchRemakePreviewOperationHandler",
    "WorkspacePageRemakePreviewOperationHandler",
    "register_workspace_authored_preview_operation_handlers",
]
