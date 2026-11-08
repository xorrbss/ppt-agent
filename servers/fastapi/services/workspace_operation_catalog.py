"""Single declarative routing contract for durable workspace operations."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Literal

from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationRecoveryPolicy,
    workspace_operation_handler_registry,
)


Controller = Literal["generation", "export", "embedded"]


@dataclass(frozen=True, slots=True)
class WorkspaceOperationSpec:
    kind: str
    handler_factory: str
    controller: Controller
    embedded: bool = True
    recovery_policy: WorkspaceOperationRecoveryPolicy = "manual"


def _spec(
    kind: str,
    factory: str,
    controller: Controller,
    *,
    recovery: WorkspaceOperationRecoveryPolicy = "manual",
) -> WorkspaceOperationSpec:
    return WorkspaceOperationSpec(kind, factory, controller, recovery_policy=recovery)


OPERATION_SPECS = (
    _spec("svg.template.analyze", "svg_templates.registration.SvgTemplateOperationHandler", "generation"),
    _spec("svg.template.prepare", "svg_templates.registration.SvgTemplateOperationHandler", "generation"),
    _spec(
        "presentation.generate",
        "presentation_generation_operation_handler.PresentationGenerationOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "raster.analysis",
        "raster_analysis_operation_handler.RasterAnalysisOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "quality.review",
        "workspace_quality_operation_handler.WorkspaceQualityReviewOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "slide.regenerate.preview",
        "workspace_authored_preview_operation_handlers.WorkspacePageRemakePreviewOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "slide.regenerate",
        "workspace_page_remake_operation_handler.WorkspacePageRemakeOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "slide.regenerate.batch.preview",
        "workspace_authored_preview_operation_handlers.WorkspaceBatchRemakePreviewOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "slide.regenerate.batch",
        "workspace_batch_remake_operation_handler.WorkspaceBatchRemakeOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "template.apply",
        "workspace_authored_template_operation_handler.WorkspaceAuthoredTemplateOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "design.apply",
        "workspace_authored_template_operation_handler.WorkspaceAuthoredTemplateOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec(
        "presentation.slides.reorder",
        "workspace_slide_order_operation_handler.WorkspaceSlideOrderOperationHandler",
        "generation",
        recovery="automatic",
    ),
    _spec("export.pptx", "workspace_quality_export_operation_handlers.WorkspaceExportOperationHandler", "export"),
    _spec("export.pdf", "workspace_quality_export_operation_handlers.WorkspaceExportOperationHandler", "export"),
    # Deferred v5 chart paint verification enqueued by a v5 ``export.pptx``;
    # it runs the same capture proof the export runs, so it shares its host.
    _spec("svg.chart.verify", "svg_chart_paint_followup.SvgChartPaintVerificationOperationHandler", "export"),
    _spec(
        "presentation.delete",
        "presentation_deletion_operation_handler.PresentationDeletionOperationHandler",
        "embedded",
        recovery="automatic",
    ),
    _spec("presentation.edit", "workspace_chat_operation_handler.PresentationChatOperationHandler", "embedded"),
    _spec("slide.edit", "workspace_chat_operation_handler.PresentationChatOperationHandler", "embedded"),
    _spec("slide.delete", "workspace_chat_operation_handler.PresentationChatOperationHandler", "embedded"),
    _spec(
        "version.restore",
        "workspace_chat_operation_handler.VersionRestoreOperationHandler",
        "embedded",
        recovery="automatic",
    ),
)

if len({spec.kind for spec in OPERATION_SPECS}) != len(OPERATION_SPECS):
    raise RuntimeError("workspace_operation_catalog_duplicate_kind")


def operation_kinds_for_controller(controller: Controller) -> tuple[str, ...]:
    return tuple(spec.kind for spec in OPERATION_SPECS if spec.controller == controller)


def register_operation_handlers(
    *,
    controller: Controller | None = None,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = True,
) -> None:
    """Install the catalog's handlers, sharing instances across related kinds."""

    instances: dict[str, object] = {}
    for spec in OPERATION_SPECS:
        if controller is None:
            if not spec.embedded:
                continue
        elif spec.controller != controller:
            continue
        if spec.handler_factory not in instances:
            module_name, class_name = spec.handler_factory.rsplit(".", 1)
            factory = getattr(import_module(f"services.{module_name}"), class_name)
            instances[spec.handler_factory] = factory()
        registry.register(
            spec.kind,
            instances[spec.handler_factory],
            replace=replace,
            recovery_policy=spec.recovery_policy,
        )
