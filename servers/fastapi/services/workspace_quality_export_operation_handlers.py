"""Immutable export workspace operations (svg decks)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.auth.internal import authenticated_internal_request_headers
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission
from services.database import async_session_maker
from services.presentation_access_service import UNSUPPORTED_PRESENTATION_DRIVER
from services.public_export_failure_classification import (
    classified_public_export_error,
    public_export_error_code,
    public_export_failure_is_non_retryable,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationDeferred,
    WorkspaceOperationExecutionError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
)

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)

_public_export_error_code = public_export_error_code
_public_export_failure_is_non_retryable = public_export_failure_is_non_retryable


def _context(claim_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = claim_payload.get("context")
    return value if isinstance(value, Mapping) else {}


def _resolve_workspace_export_options(
    claim_payload: Mapping[str, Any],
    *,
    export_format: str,
    is_svg: bool = False,
) -> tuple[Literal["fidelity", "hybrid"], bool | None]:
    """Resolve existing workspace wire values without implicit Hybrid opt-in.

    ``font_embedding=None`` means the command context named no such key, so
    the caller can apply I1's CJK default once the language is known;
    ``True``/``False`` is explicit and always wins.

    ``pptx_mode`` (fidelity/hybrid) and the "font embedding requires hybrid"
    rule below are authored-only: svg has no pptx_mode concept at all (its
    own native conversion is neither fidelity-raster nor editable-hybrid),
    and its font_embedding request is validated separately inside
    ``execute_svg_workspace_export``/``render_and_export_svgs``. ``is_svg``
    skips the authored-only rule so a real svg font_embedding request is not
    rejected before it ever reaches that svg-side validation.
    """

    command_context = _context(claim_payload)
    raw_mode = command_context.get("pptx_mode")
    if raw_mode is None:
        pptx_mode: Literal["fidelity", "hybrid"] = "fidelity"
    elif raw_mode in {"fidelity", "hybrid"}:
        pptx_mode = cast(Literal["fidelity", "hybrid"], raw_mode)
    else:
        raise NonRetryableWorkspaceOperationError("workspace_export_pptx_mode_invalid")

    font_embedding: bool | None = command_context.get("font_embedding", None)
    if font_embedding is not None and not isinstance(font_embedding, bool):
        raise NonRetryableWorkspaceOperationError("workspace_export_font_embedding_invalid")
    if (
        font_embedding
        and not is_svg
        and (export_format != "pptx" or pptx_mode != "hybrid")
    ):
        raise NonRetryableWorkspaceOperationError("workspace_export_font_embedding_requires_hybrid")
    return pptx_mode, font_embedding


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

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        export_format = claim.kind.removeprefix("export.")
        if export_format not in {"pptx", "pdf"}:
            raise NonRetryableWorkspaceOperationError("workspace_export_format_invalid")
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")

        await context.raise_if_cancelled()
        async with self._session_factory() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            # The wire options are still validated for svg (its own
            # font_embedding request is checked inside
            # execute_svg_workspace_export/render_and_export_svgs).
            _resolve_workspace_export_options(
                claim.request_payload,
                export_format=export_format,
                is_svg=presentation.mode == "svg",
            )
            admission = await session.scalar(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == claim.operation_id
                )
            )
            if presentation.mode == "svg":
                from services.svg_export_operation import execute_svg_workspace_export
                try:
                    return await execute_svg_workspace_export(
                        context, presentation=presentation, admission=admission,
                        session_factory=self._session_factory,
                    )
                except (
                    WorkspaceOperationExecutionError,
                    WorkspaceOperationCancelled,
                    WorkspaceOperationDeferred,
                    WorkspaceOperationLeaseLost,
                ):
                    raise
                except Exception as error:
                    LOGGER.exception("SVG export failed for operation %s", claim.operation_id)
                    # The runner's catch-all would record every failure as a
                    # retryable internal error and lose the real code.
                    resource_detail = getattr(error, "resource_metrics", None)
                    raise classified_public_export_error(
                        error, detail=resource_detail if isinstance(resource_detail, Mapping) else None,
                        strategy="svg",
                    ) from error
        # Only svg decks export. The authored export (Hybrid, the legacy master
        # bridge and the release-reviewed fidelity path) was removed with
        # authored generation (P3 B4c); the runner already refuses any other
        # deck before this handler runs.
        raise NonRetryableWorkspaceOperationError(UNSUPPORTED_PRESENTATION_DRIVER)
