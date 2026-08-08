"""Durable chat-first publication for an approved one-page remake preview."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select

from models.sql.presentation import PresentationModel
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    publish_authored_page_remake,
)
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload

OPERATION_KIND = "slide.regenerate"


def _command_context(payload: Mapping[str, object]) -> Mapping[str, object]:
    value = payload.get("context")
    return value if isinstance(value, Mapping) else {}


class WorkspacePageRemakeOperationHandler:
    """Publish a candidate made by the explicit `페이지 다시 만들기` action."""

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )
        command_context = _command_context(claim.request_payload)
        slide_index = command_context.get("slide_index")
        preview_task_id = command_context.get("preview_task_id")
        if (
            isinstance(slide_index, bool)
            or not isinstance(slide_index, int)
            or slide_index < 0
            or not isinstance(preview_task_id, str)
            or not preview_task_id
        ):
            raise NonRetryableWorkspaceOperationError("page_remake_request_invalid")

        await context.raise_if_cancelled()
        if await context.progress(stage="page_remake_validating", percent=10) is False:
            raise WorkspaceOperationLeaseLost("operation_lease_lost")
        async with async_session_maker() as session:
            revision = await session.scalar(
                select(PresentationModel.revision).where(
                    PresentationModel.id == claim.presentation_id
                )
            )
            if revision is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if int(revision) != int(claim.expected_revision):
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision",
                    detail={"current_revision": int(revision)},
                )
            try:
                if (
                    await context.progress(stage="page_remake_publishing", percent=70)
                    is False
                ):
                    raise WorkspaceOperationLeaseLost("operation_lease_lost")
                result = await publish_authored_page_remake(
                    session=session,
                    presentation_id=claim.presentation_id,
                    slide_index=slide_index,
                    preview_task_id=preview_task_id,
                )
                await context.raise_if_cancelled()
                safe_result = sanitize_workspace_event_payload(
                    {
                        **result,
                        "result_revision": int(revision) + 1,
                    },
                    operation_kind=OPERATION_KIND,
                )
                published_revision = await publish_presentation_revision(
                    session,
                    presentation_id=claim.presentation_id,
                    expected_revision=int(revision),
                    operation_id=claim.operation_id,
                    attempt_token=claim.attempt_token,
                    result=safe_result,
                    operation_kind=OPERATION_KIND,
                )
                await session.commit()
            except WorkspaceOperationLeaseLost:
                await session.rollback()
                raise
            except AuthoredPageRemakeError as error:
                await session.rollback()
                exception = (
                    RetryableWorkspaceOperationError
                    if error.retryable
                    else NonRetryableWorkspaceOperationError
                )
                raise exception(error.code, detail={"stage": error.stage}) from error
            except PresentationPublicationConflict as error:
                await session.rollback()
                if str(error) in {
                    "stale_operation_fence",
                    "stale_operation_event_fence",
                }:
                    raise WorkspaceOperationLeaseLost("operation_lease_lost") from error
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision"
                ) from error
            except Exception as error:
                await session.rollback()
                raise RetryableWorkspaceOperationError(
                    "page_remake_publish_failed"
                ) from error

        await context.progress(stage="page_remake_completed", percent=100)
        return WorkspaceOperationResult(
            payload={**safe_result, "status": "published"},
            result_revision=published_revision,
        )


def register_workspace_page_remake_operation_handler(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> WorkspacePageRemakeOperationHandler:
    handler = WorkspacePageRemakeOperationHandler()
    registry.register(
        OPERATION_KIND,
        handler,
        replace=replace,
        recovery_policy="automatic",
    )
    return handler
