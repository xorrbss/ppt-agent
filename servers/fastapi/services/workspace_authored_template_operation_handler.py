"""Chat-first AI-authored template change operation.

The source presentation owns the durable operation timeline.  The existing
authored generator creates a separate presentation, so the source is preserved
and the completed operation can expose an explicit link to the new deck.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from api.v1.auth.internal import authenticated_internal_request_headers
from fastapi import BackgroundTasks, HTTPException
from models.presentation_from_template import RetemplatePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.authored_checkpoint_service import progress_for_task
from services.database import async_session_maker
from services.presentation_generation_job_service import (
    cancel_generation_job,
    launch_generation_job,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)


TEMPLATE_APPLY_OPERATION_KIND = "template.apply"
InternalHeaderProvider = Callable[[], Awaitable[Mapping[str, str]]]
Sleep = Callable[[float], Awaitable[None]]


def _command_context(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("context")
    return value if isinstance(value, Mapping) else {}


def _safe_generation_failure(task: AsyncPresentationGenerationTaskModel) -> tuple[str, bool]:
    payload = task.error if isinstance(task.error, Mapping) else {}
    detail = payload.get("detail")
    nested = detail if isinstance(detail, Mapping) else {}
    code = payload.get("code") or nested.get("code")
    if not code and isinstance(detail, str):
        code = detail
    code = str(code or "authored_template_generation_failed")[:128]
    explicit_retryable = payload.get("retryable", nested.get("retryable"))
    if isinstance(explicit_retryable, bool):
        return code, explicit_retryable
    try:
        status_code = int(payload.get("status_code", 500))
    except (TypeError, ValueError):
        status_code = 500
    return code, status_code == 429 or status_code >= 500


class WorkspaceAuthoredTemplateOperationHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        internal_header_provider: InternalHeaderProvider = authenticated_internal_request_headers,
        sleep: Sleep = asyncio.sleep,
        poll_seconds: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._internal_header_provider = internal_header_provider
        self._sleep = sleep
        self._poll_seconds = max(0.05, poll_seconds)

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )
        command_context = _command_context(claim.request_payload)
        raw_style = command_context.get("authored_style")
        authored_style = raw_style.strip() if isinstance(raw_style, str) else ""
        vision_qa = command_context.get("vision_qa", False)
        if not authored_style or not isinstance(vision_qa, bool):
            raise NonRetryableWorkspaceOperationError(
                "authored_template_request_invalid"
            )

        await context.raise_if_cancelled()
        await context.progress(stage="template_source_check", percent=5)
        try:
            # Lazy imports keep startup and worker registration independent of
            # the large presentation endpoint modules.
            from api.v1.ppt.endpoints.presentation import (
                build_authored_retemplate_generation_request,
            )
            from api.v1.ppt.endpoints.presentation_generate import (
                queue_presentation_generation,
            )

            async with self._session_factory() as session:
                source_revision = await session.scalar(
                    select(PresentationModel.revision).where(
                        PresentationModel.id == claim.presentation_id
                    )
                )
                if source_revision is None:
                    raise NonRetryableWorkspaceOperationError(
                        "presentation_not_found"
                    )
                if int(source_revision) != claim.expected_revision:
                    raise NonRetryableWorkspaceOperationError(
                        "stale_presentation_revision",
                        detail={"current_revision": int(source_revision)},
                    )
                request = await build_authored_retemplate_generation_request(
                    claim.presentation_id,
                    RetemplatePresentationRequest(
                        authored_style=authored_style,
                        vision_qa=vision_qa,
                    ),
                    session,
                )
                headers = await self._internal_header_provider()
                cookie_header = headers.get("Cookie") or None
                task = await queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    export_cookie_header=cookie_header,
                    idempotency_key=(
                        f"workspace-template-apply:{claim.operation_id}"
                    ),
                    force_legacy_dispatch=True,
                )
                job_id = await session.scalar(
                    select(PresentationGenerationJob.id).where(
                        PresentationGenerationJob.task_id == task.id
                    )
                )
                if job_id is None:
                    raise RetryableWorkspaceOperationError(
                        "authored_template_generation_job_missing"
                    )
        except (NonRetryableWorkspaceOperationError, RetryableWorkspaceOperationError):
            raise
        except HTTPException as error:
            code = error.detail if isinstance(error.detail, str) else "authored_template_request_rejected"
            error_type = (
                RetryableWorkspaceOperationError
                if error.status_code == 429 or error.status_code >= 500
                else NonRetryableWorkspaceOperationError
            )
            raise error_type(str(code)[:128]) from error
        except Exception as error:
            raise RetryableWorkspaceOperationError(
                "authored_template_generation_enqueue_failed"
            ) from error

        await context.progress(stage="template_generation_queued", percent=10)
        await launch_generation_job(job_id, cookie_header)
        try:
            while True:
                await context.raise_if_cancelled()
                async with self._session_factory() as session:
                    current_task = await session.get(
                        AsyncPresentationGenerationTaskModel, task.id
                    )
                    if current_task is None:
                        raise RetryableWorkspaceOperationError(
                            "authored_template_generation_task_missing"
                        )
                    if current_task.status == "completed":
                        data = current_task.data if isinstance(current_task.data, Mapping) else {}
                        presentation_id = data.get("presentation_id")
                        if not presentation_id:
                            presentation_id = await session.scalar(
                                select(PresentationGenerationJob.presentation_id).where(
                                    PresentationGenerationJob.id == job_id
                                )
                            )
                        if not presentation_id:
                            raise RetryableWorkspaceOperationError(
                                "authored_template_result_missing"
                            )
                        break
                    if current_task.status == "error":
                        code, retryable = _safe_generation_failure(current_task)
                        error_type = (
                            RetryableWorkspaceOperationError
                            if retryable
                            else NonRetryableWorkspaceOperationError
                        )
                        raise error_type(code)
                    progress, _ = await progress_for_task(task.id, session)

                if progress:
                    percent = max(10, min(99, int(progress.get("percent") or 10)))
                    current = progress.get("completed_slides")
                    total = progress.get("total_slides")
                    stage = str(progress.get("current_stage") or "template_generating")
                    await context.progress(
                        stage=f"template_{stage}",
                        current=int(current) if isinstance(current, int) else None,
                        total=int(total) if isinstance(total, int) else None,
                        percent=percent,
                    )
                await self._sleep(self._poll_seconds)
        except WorkspaceOperationCancelled:
            await cancel_generation_job(task.id)
            raise
        except WorkspaceOperationLeaseLost:
            raise

        await context.progress(stage="template_generation_completed", percent=100)
        return WorkspaceOperationResult(
            payload={
                "kind": TEMPLATE_APPLY_OPERATION_KIND,
                "message": "새 AI 저작 자료가 준비되었습니다.",
                "presentation_id": str(presentation_id),
                "authored_style": authored_style,
            },
            # The operation is anchored to an unchanged source deck.
            result_revision=claim.expected_revision,
        )


def register_workspace_authored_template_operation_handler(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> WorkspaceAuthoredTemplateOperationHandler:
    handler = WorkspaceAuthoredTemplateOperationHandler()
    registry.register(
        TEMPLATE_APPLY_OPERATION_KIND,
        handler,
        replace=replace,
        recovery_policy="automatic",
    )
    return handler
