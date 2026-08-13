from __future__ import annotations

import os

from services.export_task_contracts import (
    DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS,
    EXPORT_DIRECTORY_MODE,
    EXPORT_FILE_MODE,
    MIN_EXPORT_TASK_TIMEOUT_SECONDS,
    ExtractSchemaDocument,
    ExtractSchemaSlide,
    PptxToHtmlDocument,
    PptxToJsonDocument,
    PresentationExportTaskResult,
    export_task_timeout_seconds,
    redact_export_url,
    sys_arch,
    sys_platform,
    windows_hidden_subprocess_kwargs,
)
from services.export_task_operations import ExportTaskOperationsMixin
from services.export_task_runtime import ExportTaskRuntimeMixin


_export_task_timeout_seconds = export_task_timeout_seconds
_redact_export_url = redact_export_url
_windows_hidden_subprocess_kwargs = windows_hidden_subprocess_kwargs

__all__ = [
    "DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS",
    "EXPORT_DIRECTORY_MODE",
    "EXPORT_FILE_MODE",
    "MIN_EXPORT_TASK_TIMEOUT_SECONDS",
    "EXPORT_TASK_SERVICE",
    "ExportTaskService",
    "ExtractSchemaDocument",
    "ExtractSchemaSlide",
    "PptxToHtmlDocument",
    "PptxToJsonDocument",
    "PresentationExportTaskResult",
    "sys_arch",
    "sys_platform",
]


class ExportTaskService(ExportTaskOperationsMixin, ExportTaskRuntimeMixin):
    def __init__(self, timeout_seconds: int | None = None):
        self.timeout_seconds = (
            _export_task_timeout_seconds()
            if timeout_seconds is None
            else timeout_seconds
        )
        self.node_binary = os.getenv("LITEPARSE_NODE_BINARY", "node")
        self.export_dir = self._resolve_export_dir()
        self.entrypoint_path = self._resolve_entrypoint_path(self.export_dir)
        self.converter_path = self._resolve_converter_path(self.export_dir)


EXPORT_TASK_SERVICE = ExportTaskService()
