"""Register the canonical generation handler on a registry, for test harnesses.

Production resolves ``presentation.generate`` through the workspace operation
catalog; harnesses that drive a private registry (or replace the process-wide
one) register the handler explicitly with this helper.
"""

from __future__ import annotations

from services.presentation_generation_operation_handler import (
    PRESENTATION_GENERATION_OPERATION_KIND,
    PresentationGenerationOperationHandler,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    workspace_operation_handler_registry,
)


def register_presentation_generation_operation_handler(
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    *,
    replace: bool = False,
) -> PresentationGenerationOperationHandler:
    """Register the canonical generation kind and return its handler."""

    handler = PresentationGenerationOperationHandler()
    registry.register(
        PRESENTATION_GENERATION_OPERATION_KIND,
        handler,
        replace=replace,
    )
    return handler
