"""Workspace handler for the ``template.apply`` and ``design.apply`` kinds.

The authored half (regenerating a target deck with another authored style)
was removed with the authored strategy (2026-11-09/10); such operations now
fail with ``authored_strategy_removed``. A ``design.apply`` whose target is the
svg common header is the remaining live case and goes to the svg header
design handler, unchanged.
"""

from __future__ import annotations

from services.generation_core.admission.strategy import AUTHORED_STRATEGY_REMOVED
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationResult,
)


TEMPLATE_APPLY_OPERATION_KIND = "template.apply"
DESIGN_APPLY_OPERATION_KIND = "design.apply"


class WorkspaceAuthoredTemplateOperationHandler:
    """Route an svg common-header design change; refuse authored redesigns."""

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.kind == DESIGN_APPLY_OPERATION_KIND and (claim.request_payload.get("context") or {}).get("target") == "common_header":
            from services.workspace_svg_header_design_operation_handler import WorkspaceSvgHeaderDesignOperationHandler
            return await WorkspaceSvgHeaderDesignOperationHandler().execute(context)
        raise NonRetryableWorkspaceOperationError(AUTHORED_STRATEGY_REMOVED)
