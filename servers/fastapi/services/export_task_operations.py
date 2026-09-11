from __future__ import annotations

import logging
import os
from typing import Literal

from fastapi import HTTPException

from services.export_task_contracts import (
    PresentationExportTaskResult,
    redact_export_url,
)
from utils.asset_directory_utils import get_exports_directory
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id


LOGGER = logging.getLogger(__name__)


class ExportTaskOperationsMixin:
    async def export_from_url(
        self,
        url: str,
        title: str,
        export_as: Literal["pdf", "pptx"],
        fastapi_url: str | None = None,
        cookie_header: str | None = None,
    ) -> PresentationExportTaskResult:
        LOGGER.info(
            "[export_runtime] export_from_url url=%s format=%s cookie_header=%s",
            redact_export_url(url),
            export_as,
            "set" if cookie_header else "empty",
        )
        response_data = await self._run_task(
            {
                "type": "export",
                "url": url,
                "format": export_as,
                "title": title,
                "fastapiUrl": fastapi_url or None,
                "cookieHeader": cookie_header or None,
            },
            "Export task did not produce a response file",
        )

        output_path = self._resolve_output_path(response_data)
        output_path = self._move_export_to_owner(output_path)
        self._ensure_output_readable(output_path)

        return PresentationExportTaskResult(
            path=output_path,
        )

    @staticmethod
    def _move_export_to_owner(output_path: str) -> str:
        if get_current_owner_id() is None:
            return output_path
        destination_dir = get_exports_directory()
        app_data = get_app_data_directory_env()
        if not app_data:
            raise HTTPException(
                status_code=500,
                detail="APP_DATA_DIRECTORY is required for exported files",
            )
        exports_root = os.path.realpath(os.path.join(app_data, "exports"))
        resolved_output = os.path.realpath(output_path)
        resolved_destination_dir = os.path.realpath(destination_dir)
        source_parent = os.path.dirname(resolved_output)
        if source_parent not in {exports_root, resolved_destination_dir}:
            raise HTTPException(
                status_code=500,
                detail="Export task returned an output outside its asset directory",
            )
        destination = os.path.join(destination_dir, os.path.basename(output_path))
        os.makedirs(destination_dir, exist_ok=True)
        if resolved_output != os.path.realpath(destination):
            os.replace(output_path, destination)
        return destination
