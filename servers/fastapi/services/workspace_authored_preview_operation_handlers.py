"""Durable workspace handlers for authored page-remake previews."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

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
    WorkspaceOperationCancelled,
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
    message: str,
    data: dict[str, Any] | None = None,
) -> AsyncPresentationGenerationTaskModel:
    async with async_session_maker() as session:
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            task = AsyncPresentationGenerationTaskModel(
                id=task_id,
                owner_id=owner_id,
                status="pending",
                message=message,
                data=data,
            )
            session.add(task)
            await session.commit()
        return task


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
            request = AuthoredPageRemakePreviewRequest(instruction=instruction)
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(
                "page_remake_preview_request_invalid"
            ) from error

        task_id = _page_task_id(claim.operation_id)
        task = await _ensure_task(
            task_id,
            owner_id=claim.owner_id,
            message=f"{slide_index + 1}페이지의 새 버전 작업을 대기열에 추가했습니다.",
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
                _run_preview_background(
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
            message=f"{len(pairs)}개 페이지의 새 버전을 만들고 있습니다.",
            data=parent_data,
        )
        if parent.status != "completed":
            for index, child_id in pairs:
                await _ensure_task(
                    child_id,
                    owner_id=claim.owner_id,
                    message=f"{index + 1}페이지의 새 버전 작업을 대기열에 추가했습니다.",
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
