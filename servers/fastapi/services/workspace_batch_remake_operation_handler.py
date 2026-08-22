"""Durable chat-first publication for an approved multi-page remake batch.

The whole batch is one transaction and one revision bump.  A slide rejected by
its own validation is rolled back to a savepoint and reported as data; it never
aborts the pages that already applied, and it never leaves a half-written deck.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import asyncio
from typing import Any

from sqlalchemy import select

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_quality_review_service import _ANALYZE_AND_FIX_LOCKS
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    publish_authored_page_remake,
)
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.authored_batch_remake_service import (
    MAX_BATCH_SLIDES,
    snapshot_slides_for_batch,
)
from services.authored_semantic_candidate import compile_semantic_components
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.remake import (
    republish_authored_semantic_bundle_graph_for_remake,
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
from services.workspace_operation_runner import sanitize_workspace_event_payload

OPERATION_KIND = "slide.regenerate.batch"
# The batch publishes inside one write transaction, so its lease must cover the
# whole run.  Observed authored republication is well under two minutes a page;
# double that so a slow provider does not cost the deck its attempt.
_LEASE_BASE_SECONDS = 300
_LEASE_PER_PAGE_SECONDS = 240

_REQUEST_INVALID = "authored_batch_remake_request_invalid"
_MASTER_PINNED = "authored_batch_remake_master_pinned_unsupported"
_ALL_FAILED = "authored_batch_remake_all_slides_failed"


def _command_context(payload: Mapping[str, object]) -> Mapping[str, object]:
    value = payload.get("context")
    return value if isinstance(value, Mapping) else {}


def _requested_previews(
    payload: Mapping[str, object],
) -> list[tuple[int, str]]:
    """Parse the approved (slide_index, preview_task_id) pairs or fail closed."""

    previews = _command_context(payload).get("previews")
    if (
        not isinstance(previews, Sequence)
        or isinstance(previews, (str, bytes, bytearray))
        or not previews
        or len(previews) > MAX_BATCH_SLIDES
    ):
        raise NonRetryableWorkspaceOperationError(_REQUEST_INVALID)
    parsed: list[tuple[int, str]] = []
    seen: set[int] = set()
    for entry in previews:
        if not isinstance(entry, Mapping):
            raise NonRetryableWorkspaceOperationError(_REQUEST_INVALID)
        slide_index = entry.get("slide_index")
        preview_task_id = entry.get("preview_task_id")
        if (
            isinstance(slide_index, bool)
            or not isinstance(slide_index, int)
            or slide_index < 0
            or slide_index in seen
            or not isinstance(preview_task_id, str)
            or not preview_task_id
        ):
            raise NonRetryableWorkspaceOperationError(_REQUEST_INVALID)
        seen.add(slide_index)
        parsed.append((slide_index, preview_task_id))
    parsed.sort(key=lambda item: item[0])
    return parsed


def _batch_payload(
    presentation_id: object,
    slide_results: list[dict[str, Any]],
    requested_count: int,
) -> dict[str, Any]:
    published = [
        item["slide_index"] for item in slide_results if item["status"] == "published"
    ]
    failed = [
        item["slide_index"] for item in slide_results if item["status"] == "failed"
    ]
    return {
        "kind": "authored_batch_remake",
        "status": "partial" if failed else "published",
        "presentation_id": str(presentation_id),
        "requested_count": requested_count,
        "published_count": len(published),
        "failed_count": len(failed),
        "published_slide_indices": published,
        "failed_slide_indices": failed,
        "fixed_slide_indices": published,
        "slide_results": slide_results,
        "canvas_refresh_required": True,
        "rollback_available": True,
    }


class WorkspaceBatchRemakeOperationHandler:
    """Publish every candidate approved together in the batch preview dialog."""

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )
        previews = _requested_previews(claim.request_payload)
        await context.raise_if_cancelled()
        if (
            await context.progress(stage="batch_remake_validating", percent=5)
            is False
        ):
            raise WorkspaceOperationLeaseLost("operation_lease_lost")

        async with async_session_maker() as session:
            try:
                presentation = await session.scalar(
                    select(PresentationModel).where(
                        PresentationModel.id == claim.presentation_id
                    )
                )
                if presentation is None:
                    raise NonRetryableWorkspaceOperationError("presentation_not_found")
                revision = presentation.revision
                if int(revision) != int(claim.expected_revision):
                    raise NonRetryableWorkspaceOperationError(
                        "stale_presentation_revision",
                        detail={"current_revision": int(revision)},
                    )
                theme = (
                    presentation.theme if isinstance(presentation.theme, dict) else {}
                )
                if theme.get("authored_vnext") is not None:
                    # A pinned master advances the deck revision per page, so a
                    # batch cannot share this operation's single revision bump.
                    raise NonRetryableWorkspaceOperationError(_MASTER_PINNED)

                safe_result, published_revision = await self._publish_batch(
                    context, session, previews, int(revision)
                )
                await session.commit()
            except (WorkspaceOperationLeaseLost, WorkspaceOperationCancelled):
                await session.rollback()
                raise
            except NonRetryableWorkspaceOperationError:
                await session.rollback()
                raise
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

        await context.progress(stage="batch_remake_completed", percent=100)
        status = safe_result.get("status", "published")
        return WorkspaceOperationResult(
            payload={**safe_result, "status": status},
            result_revision=published_revision,
        )

    async def _publish_batch(
        self,
        context: WorkspaceOperationExecutionContext,
        session: Any,
        previews: list[tuple[int, str]],
        revision: int,
    ) -> tuple[dict[str, Any], int]:
        claim = context.claim
        total = len(previews)
        slide_results: list[dict[str, Any]] = []
        # The deck's own mutex, shared with analyze-fix and single-page
        # publication: a batch rewrite and a quality review must never interleave.
        # Previews deliberately do not take it, so this does not block the
        # four-wide preview stage; it only makes deck-wide rewrites take turns.
        lock = _ANALYZE_AND_FIX_LOCKS.setdefault(
            claim.presentation_id, asyncio.Lock()
        )
        # Everything below is a single write transaction, from the snapshot to
        # the revision bump.  A single-writer backend hands that transaction the
        # write lock for its whole duration, which locks out the heartbeat too,
        # so the lease has to be widened here -- afterwards it cannot be.
        await context.extend_lease(
            seconds=_LEASE_BASE_SECONDS + _LEASE_PER_PAGE_SECONDS * total
        )
        async with lock:
            # One undo point for the whole batch, taken before anything moves.
            # The batch is a single transaction with a single revision bump, so a
            # single restore point is what actually matches it.
            await snapshot_slides_for_batch(session, claim.presentation_id)
            for done, (slide_index, preview_task_id) in enumerate(previews, start=1):
                await context.raise_if_cancelled()
                try:
                    # The savepoint is what makes partial failure survivable: one
                    # rejected page rolls back alone, its siblings stay staged.
                    async with session.begin_nested():
                        await publish_authored_page_remake(
                            session=session,
                            presentation_id=claim.presentation_id,
                            slide_index=slide_index,
                            preview_task_id=preview_task_id,
                            snapshot=False,
                        )
                except AuthoredPageRemakeError as error:
                    slide_results.append(
                        {
                            "slide_index": slide_index,
                            "status": "failed",
                            "preview_task_id": preview_task_id,
                            "reason_code": error.code,
                        }
                    )
                else:
                    slide_results.append(
                        {
                            "slide_index": slide_index,
                            "status": "published",
                            "preview_task_id": preview_task_id,
                        }
                    )
                if (
                    await context.progress(
                        stage="batch_remake_publishing",
                        current=done,
                        total=total,
                        percent=5 + round(85 * done / total),
                    )
                    is False
                ):
                    raise WorkspaceOperationLeaseLost("operation_lease_lost")

            payload = _batch_payload(claim.presentation_id, slide_results, total)
            if payload["published_count"] == 0:
                await session.rollback()
                raise NonRetryableWorkspaceOperationError(_ALL_FAILED)
            active_semantic = await get_active_authored_emission(
                session,
                presentation_id=claim.presentation_id,
                owner_id=claim.owner_id,
            )
            if active_semantic.canonical_mode == "bundleGraph":
                slide_rows = await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == claim.presentation_id)
                    .order_by(SlideModel.index)
                    .execution_options(populate_existing=True)
                )
                ordered_slides = list(slide_rows.scalars().all())
                htmls = [str(item.html_content or "") for item in ordered_slides]
                await republish_authored_semantic_bundle_graph_for_remake(
                    session,
                    presentation_id=claim.presentation_id,
                    owner_id=claim.owner_id,
                    expected_presentation_revision=revision,
                    operation_id=claim.operation_id,
                    generation_attempt_id=None,
                    ordered_slides=ordered_slides,
                    htmls=htmls,
                    semantic_drafts=compile_semantic_components(htmls),
                )
            safe_result = sanitize_workspace_event_payload(
                {**payload, "result_revision": revision + 1},
                operation_kind=OPERATION_KIND,
            )
            published_revision = await publish_presentation_revision(
                session,
                presentation_id=claim.presentation_id,
                expected_revision=revision,
                operation_id=claim.operation_id,
                attempt_token=claim.attempt_token,
                result=safe_result,
                operation_kind=OPERATION_KIND,
            )
        return safe_result, published_revision


def register_workspace_batch_remake_operation_handler(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> WorkspaceBatchRemakeOperationHandler:
    handler = WorkspaceBatchRemakeOperationHandler()
    registry.register(
        OPERATION_KIND,
        handler,
        # The whole publish stays uncommitted until the final revision bump, so
        # a crashed attempt published nothing and can safely be retried.
        replace=replace,
        recovery_policy="automatic",
    )
    return handler
