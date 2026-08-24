"""Durable chat-first publication for an approved one-page remake preview."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    publish_authored_page_remake,
    validated_authored_page_remake_candidate_body,
)
from services.authored_vnext.generation.legacy_bridge import (
    regenerate_legacy_master_body,
)
from services.authored_vnext.generation.legacy_pin import (
    canonical_legacy_master_pin_identity,
)
from services.authored_semantic_candidate import compile_semantic_components
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.remake import (
    republish_authored_semantic_bundle_graph_for_remake,
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
        master_pin = command_context.get("master_pin")
        layout_id = command_context.get("layout_id")
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
            theme = presentation.theme if isinstance(presentation.theme, dict) else {}
            projection = theme.get("authored_vnext")
            pinned_body_only = projection is not None
            if pinned_body_only or master_pin is not None:
                projected_pin = (
                    projection.get("masterPin")
                    if isinstance(projection, dict)
                    else None
                )
                if (
                    not isinstance(projection, dict)
                    or not isinstance(projected_pin, Mapping)
                    or not isinstance(master_pin, Mapping)
                    or command_context.get("body_only") is not True
                    or command_context.get("allow_layout_change") is not False
                    or not isinstance(layout_id, str)
                    or not layout_id
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "authored_vnext_regenerate_body_contract_invalid"
                    )
                requested_identity = canonical_legacy_master_pin_identity(master_pin)
                projected_identity = canonical_legacy_master_pin_identity(projected_pin)
                if requested_identity is None or requested_identity != projected_identity:
                    raise NonRetryableWorkspaceOperationError(
                        "authored_vnext_regenerate_master_changed"
                    )
                try:
                    body = await validated_authored_page_remake_candidate_body(
                        session=session,
                        presentation_id=claim.presentation_id,
                        slide_index=slide_index,
                        preview_task_id=preview_task_id,
                    )
                except AuthoredPageRemakeError as error:
                    raise NonRetryableWorkspaceOperationError(
                        error.code, detail={"stage": error.stage}
                    ) from error

        if pinned_body_only:
            if await context.progress(stage="page_remake_publishing", percent=70) is False:
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
            try:
                regenerated = await regenerate_legacy_master_body(
                    owner_id=claim.owner_id,
                    presentation_id=claim.presentation_id,
                    expected_presentation_revision=int(revision),
                    projection=projection,
                    slide_index=slide_index,
                    layout_id=layout_id,
                    body=body.strip(),
                    operation_uuid=claim.operation_id,
                )
            except Exception as error:
                code = str(error)
                if not code.startswith("authored_vnext_"):
                    code = getattr(error, "code", "authored_vnext_generation_conflict")
                raise NonRetryableWorkspaceOperationError(code) from error
            await context.progress(stage="page_remake_completed", percent=100)
            return WorkspaceOperationResult(
                payload={
                    "status": "published",
                    "kind": "authored_vnext_body_regeneration",
                    "deck_revision": regenerated.result.deck.revision,
                    "deck_digest": regenerated.result.deck.digest,
                    "slide_index": slide_index,
                    "layout_id": layout_id,
                },
                result_revision=regenerated.presentation_revision,
            )

        async with async_session_maker() as session:
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
                        expected_presentation_revision=int(revision),
                        operation_id=claim.operation_id,
                        generation_attempt_id=None,
                        ordered_slides=ordered_slides,
                        htmls=htmls,
                        semantic_drafts=compile_semantic_components(htmls),
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
