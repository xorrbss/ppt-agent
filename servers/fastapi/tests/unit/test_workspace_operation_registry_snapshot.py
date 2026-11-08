"""The pre-refactor operation routing contract, captured on 2026-09-26."""

EXPECTED_ROUTE_MAP = {
    "presentation.generate": ("generation", "PresentationGenerationOperationHandler"),
    "raster.analysis": ("generation", "RasterAnalysisOperationHandler"),
    "quality.review": ("generation", "WorkspaceQualityReviewOperationHandler"),
    "slide.regenerate.preview": ("generation", "WorkspacePageRemakePreviewOperationHandler"),
    "slide.regenerate": ("generation", "WorkspacePageRemakeOperationHandler"),
    "slide.regenerate.batch.preview": ("generation", "WorkspaceBatchRemakePreviewOperationHandler"),
    "slide.regenerate.batch": ("generation", "WorkspaceBatchRemakeOperationHandler"),
    "template.apply": ("generation", "WorkspaceAuthoredTemplateOperationHandler"),
    "svg.template.analyze": ("generation", "SvgTemplateOperationHandler"),
    "svg.template.prepare": ("generation", "SvgTemplateOperationHandler"),
    "design.apply": ("generation", "WorkspaceAuthoredTemplateOperationHandler"),
    "presentation.slides.reorder": ("generation", "WorkspaceSlideOrderOperationHandler"),
    "export.pptx": ("export", "WorkspaceExportOperationHandler"),
    "export.pdf": ("export", "WorkspaceExportOperationHandler"),
    "svg.chart.verify": ("export", "SvgChartPaintVerificationOperationHandler"),
    "presentation.delete": ("embedded", "PresentationDeletionOperationHandler"),
    "presentation.edit": ("embedded", "PresentationChatOperationHandler"),
    "slide.edit": ("embedded", "PresentationChatOperationHandler"),
    "slide.delete": ("embedded", "PresentationChatOperationHandler"),
    "version.restore": ("embedded", "VersionRestoreOperationHandler"),
}


def test_operation_catalog_preserves_pre_refactor_route_map():
    from services.workspace_operation_catalog import OPERATION_SPECS, register_operation_handlers
    from services.workspace_operation_handler_registry import WorkspaceOperationHandlerRegistry

    actual = {spec.kind: (spec.controller, spec.handler_factory.rsplit(".", 1)[1]) for spec in OPERATION_SPECS}
    assert actual == EXPECTED_ROUTE_MAP

    for controller in (None, "generation", "export"):
        registry = WorkspaceOperationHandlerRegistry()
        register_operation_handlers(controller=controller, registry=registry)
        expected = {
            kind: handler
            for kind, (owner, handler) in EXPECTED_ROUTE_MAP.items()
            if controller is None or owner == controller
        }
        assert {kind: type(registry.resolve(kind)).__name__ for kind in registry.kinds} == expected
