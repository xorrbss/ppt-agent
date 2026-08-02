"""Read-only quality review and immutable export workspace operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import logging
from pathlib import Path
from typing import Any, Literal, cast

from api.v1.auth.internal import authenticated_internal_request_headers
from fastapi import HTTPException
from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.authored_quality_review_service import run_authored_quality_review
from services.database import async_session_maker
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_generation_artifact,
    publish_workspace_export_artifact,
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
from utils.export_utils import export_presentation


QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)


def _context(claim_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = claim_payload.get("context")
    return value if isinstance(value, Mapping) else {}


class WorkspaceQualityReviewOperationHandler:
    """Run the existing authored review in analyze-only mode at one revision."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )
        command_context = _context(claim.request_payload)
        if command_context.get("mode", "analyze_only") != "analyze_only":
            raise NonRetryableWorkspaceOperationError(
                "quality_review_read_only_required"
            )
        scope = command_context.get("scope", "all")
        slide_indices = command_context.get("slide_indices", [])
        if scope not in {"all", "current"} or not isinstance(slide_indices, list):
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        request = AuthoredQualityReviewRequest(
            scope=scope,
            slide_indices=slide_indices,
            mode="analyze_only",
        )

        await context.raise_if_cancelled()
        await context.progress(stage="quality_review_preparing", percent=5)
        async with self._session_factory() as session:
            inspected_revision = await session.scalar(
                select(PresentationModel.revision).where(
                    PresentationModel.id == claim.presentation_id
                )
            )
            if inspected_revision is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if int(inspected_revision) != claim.expected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision",
                    detail={"current_revision": int(inspected_revision)},
                )
            task = AsyncPresentationGenerationTaskModel(
                status="pending",
                message="Preparing quality review.",
                data=None,
            )
            session.add(task)
            await session.commit()
            try:
                await context.progress(stage="quality_review_analyzing", percent=20)
                await run_authored_quality_review(
                    claim.presentation_id, request, task, session
                )
                await context.raise_if_cancelled()
            except WorkspaceOperationCancelled:
                raise
            except WorkspaceOperationLeaseLost:
                raise
            except (ValueError, HTTPException) as error:
                raise NonRetryableWorkspaceOperationError(
                    "quality_review_unsupported_presentation"
                ) from error
            except Exception as error:
                raise RetryableWorkspaceOperationError(
                    "quality_review_failed"
                ) from error

            current_revision = await session.scalar(
                select(PresentationModel.revision).where(
                    PresentationModel.id == claim.presentation_id
                ).execution_options(populate_existing=True)
            )
            if current_revision != inspected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "quality_review_revision_changed",
                    detail={
                        "inspected_revision": int(inspected_revision),
                        "current_revision": int(current_revision or 0),
                    },
                )
            await session.refresh(task)
            if task.status != "completed" or not isinstance(task.data, dict):
                raise RetryableWorkspaceOperationError("quality_review_failed")
            result = {
                "kind": QUALITY_REVIEW_OPERATION_KIND,
                "message": task.message,
                "inspected_revision": int(inspected_revision),
                **task.data,
                # The service receives this literal too; repeating it in the
                # durable result makes the non-mutating contract auditable.
                "mode": "analyze_only",
            }
        await context.progress(stage="quality_review_completed", percent=100)
        return WorkspaceOperationResult(
            payload=result,
            result_revision=int(inspected_revision),
        )


class WorkspaceExportOperationHandler:
    """Render then immutably publish an owner-scoped export artifact."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        internal_header_provider: InternalHeaderProvider = authenticated_internal_request_headers,
    ) -> None:
        self._session_factory = session_factory
        self._internal_header_provider = internal_header_provider

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        export_format = claim.kind.removeprefix("export.")
        if export_format not in {"pptx", "pdf"}:
            raise NonRetryableWorkspaceOperationError(
                "workspace_export_format_invalid"
            )
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )

        await context.raise_if_cancelled()
        async with self._session_factory() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if presentation.revision != claim.expected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision"
                )
            title = presentation.title or "presentation"

        await context.progress(stage="export_rendering", percent=10)
        # Mint at execution time only. This credential is never copied into a
        # request payload, event, artifact row or result projection.
        headers = await self._internal_header_provider()
        cookie_header = headers.get("Cookie") or None
        exported_source: Path | None = None
        try:
            exported = await export_presentation(
                claim.presentation_id,
                title=title,
                export_as=cast(Literal["pptx", "pdf"], export_format),
                cookie_header=cookie_header,
            )
            exported_source = Path(exported.path)
            await context.raise_if_cancelled()
            await context.progress(stage="export_publishing", percent=80)
            async with self._session_factory() as session:
                artifact = await prepare_workspace_generation_artifact(
                    session,
                    source_path=exported.path,
                    operation_id=claim.operation_id,
                    presentation_id=claim.presentation_id,
                    source_revision=claim.expected_revision,
                    kind=claim.kind,
                )
                try:
                    await context.raise_if_cancelled()
                except WorkspaceOperationCancelled:
                    await compensate_workspace_artifact(session, artifact)
                    raise
                revision = await publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token=claim.attempt_token,
                    fencing_token=claim.fencing_token,
                    export_format=export_format,
                )
        except WorkspaceOperationCancelled:
            raise
        except WorkspaceArtifactPublicationError as error:
            error_type = (
                NonRetryableWorkspaceOperationError
                if error.code
                in {
                    "stale_presentation_revision",
                    "operation_lease_lost",
                    "workspace_export_format_invalid",
                }
                else RetryableWorkspaceOperationError
            )
            if error.code == "operation_lease_lost":
                raise WorkspaceOperationLeaseLost(error.code) from error
            raise error_type(error.code) from error
        except WorkspaceOperationLeaseLost:
            raise
        except Exception as error:
            raise RetryableWorkspaceOperationError("presentation_export_failed") from error
        finally:
            # The renderer output is only a staging source. The immutable,
            # hashed workspace artifact is the durable copy and must be the
            # sole downloadable file after prepare succeeds or fails.
            if exported_source is not None:
                try:
                    exported_source.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Unable to remove workspace export staging source",
                        extra={
                            "operation_id": str(claim.operation_id),
                            "presentation_id": str(claim.presentation_id),
                            "format": export_format,
                        },
                        exc_info=True,
                    )

        await context.progress(stage="export_completed", percent=100)
        # Publication has already terminalized the operation. The runner will
        # reconcile this canonical durable projection rather than append twice.
        return WorkspaceOperationResult(
            payload={"status": "completed", "format": export_format},
            result_revision=revision,
        )


def register_workspace_quality_export_operation_handlers(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> tuple[WorkspaceQualityReviewOperationHandler, WorkspaceExportOperationHandler]:
    quality = WorkspaceQualityReviewOperationHandler()
    export = WorkspaceExportOperationHandler()
    registry.register(QUALITY_REVIEW_OPERATION_KIND, quality, replace=replace)
    for kind in EXPORT_OPERATION_KINDS:
        registry.register(kind, export, replace=replace)
    return quality, export
