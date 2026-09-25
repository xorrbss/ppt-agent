"""Durable slide-order persistence: the editor's drag reorder.

The runner is the only writer of presentation state (see
services/workspace_legacy_write_gate.py); this handler is the one place that
sets SlideModel.index for a reorder and publishes it. It reuses
publish_presentation_revision exactly like workspace_page_remake_operation_handler
does, so an SVG/raster-redesign deck's page-number footers stay consistent
(refresh_svg_page_numbers -> services/svg_raster_renumber.py) and the revision/
versions/receipts contract is the same one every other mutation uses.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.slide import SlideModel
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)

OPERATION_KIND = "presentation.slides.reorder"

LOGGER = logging.getLogger(__name__)


def _requested_slide_ids(payload: Mapping[str, object]) -> tuple[uuid.UUID, ...]:
    context = payload.get("context")
    raw = context.get("slide_ids") if isinstance(context, Mapping) else None
    if not isinstance(raw, list) or not raw:
        raise NonRetryableWorkspaceOperationError("slide_order_request_invalid")
    try:
        return tuple(uuid.UUID(str(item)) for item in raw)
    except (TypeError, ValueError) as error:
        raise NonRetryableWorkspaceOperationError("slide_order_request_invalid") from error


class WorkspaceSlideOrderOperationHandler:
    def __init__(
        self, *, session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def execute(
        self, context: WorkspaceOperationExecutionContext,
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")
        requested_order = _requested_slide_ids(claim.request_payload)
        if len(set(requested_order)) != len(requested_order):
            raise NonRetryableWorkspaceOperationError("slide_order_duplicate_id")
        await context.raise_if_cancelled()
        # The runner runs this as the submitting principal (owner and tenant,
        # services/workspace_operation_principal.py); without a tenant, the
        # raster footer renumbering fails with its own explicit code.
        return await self._reorder_and_publish(claim, requested_order)

    async def _reorder_and_publish(
        self, claim: WorkspaceOperationClaim, requested_order: tuple[uuid.UUID, ...],
    ) -> WorkspaceOperationResult:
        async with self._session_factory() as session:
            try:
                slides = list((await session.scalars(
                    select(SlideModel)
                    .where(SlideModel.presentation == claim.presentation_id)
                    .order_by(SlideModel.index, SlideModel.id)
                )).all())
                if not slides:
                    raise NonRetryableWorkspaceOperationError("slide_order_requires_slide")
                current_ids = {slide.id for slide in slides}
                if set(requested_order) != current_ids:
                    raise NonRetryableWorkspaceOperationError("slide_order_id_set_mismatch")
                position_by_id = {slide_id: index for index, slide_id in enumerate(requested_order)}
                changed = False
                for slide in slides:
                    new_index = position_by_id[slide.id]
                    if slide.index != new_index:
                        slide.index = new_index
                        session.add(slide)
                        changed = True
                result = {"order": [str(slide_id) for slide_id in requested_order], "changed": changed}
                published_revision = await publish_presentation_revision(
                    session,
                    presentation_id=claim.presentation_id,
                    expected_revision=claim.expected_revision,
                    operation_id=claim.operation_id,
                    attempt_token=claim.attempt_token,
                    result=result,
                    operation_kind=OPERATION_KIND,
                    # Idempotent for the same order: a no-op reorder still
                    # advances the operation's own fence (so a retried submit
                    # of the same command resolves), but never bumps the deck
                    # revision when nothing actually moved.
                    advance_revision=changed,
                )
                await session.commit()
            except PresentationPublicationConflict as error:
                await session.rollback()
                if str(error) in {"stale_operation_fence", "stale_operation_event_fence"}:
                    from services.workspace_operation_handler_registry import WorkspaceOperationLeaseLost
                    raise WorkspaceOperationLeaseLost("operation_lease_lost") from error
                raise NonRetryableWorkspaceOperationError("stale_presentation_revision") from error
            except NonRetryableWorkspaceOperationError:
                await session.rollback()
                raise
            except Exception as error:
                await session.rollback()
                # Without this the real traceback never reached any log -- the
                # operation row only recorded the generic
                # "slide_order_publish_failed" code (same class of gap as
                # WorkspaceOperationRunner._execute's catch-all).
                LOGGER.exception(
                    "slide_order_publish_failed kind=%s operation_id=%s",
                    claim.kind,
                    claim.operation_id,
                )
                raise RetryableWorkspaceOperationError("slide_order_publish_failed") from error
        return WorkspaceOperationResult(payload=result, result_revision=published_revision)


def register_workspace_slide_order_operation_handler(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    replace: bool = False,
) -> WorkspaceSlideOrderOperationHandler:
    handler = WorkspaceSlideOrderOperationHandler(session_factory=session_factory)
    registry.register(OPERATION_KIND, handler, replace=replace, recovery_policy="automatic")
    return handler
