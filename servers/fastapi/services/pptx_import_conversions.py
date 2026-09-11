from __future__ import annotations

# Conversion payloads are local runtime hand-offs and intentionally read in place.
# ruff: noqa: ASYNC230, ASYNC240

import json
import logging
import os
import shutil
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import ValidationError

from services.export_task_contracts import (
    ExtractSchemaDocument,
    PptxToHtmlDocument,
    PptxToJsonDocument,
    export_task_timeout_seconds,
)
from services.export_task_runtime import ExportTaskRuntimeMixin
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id


LOGGER = logging.getLogger(__name__)

__all__ = [
    "EXPORT_TASK_SERVICE",
    "PptxImportConversionsService",
]


class PptxImportConversionsService(ExportTaskRuntimeMixin):
    """PPTX -> JSON/HTML/schema conversions used by the template-v2 import pipeline.

    Extracted out of `services.export_task_service.ExportTaskService` so that the
    direct export attempt path (`export_from_url`, in
    `services.export_task_operations`) can be deleted later without breaking PPTX
    import, which is unrelated to export. Reuses the generic Node-runtime bridge
    (`ExportTaskRuntimeMixin`, still shared with `ExportTaskService`) unchanged --
    it is not export-specific, it is the transport every task type spawns through.
    """

    def __init__(self, timeout_seconds: int | None = None):
        self.timeout_seconds = (
            export_task_timeout_seconds()
            if timeout_seconds is None
            else timeout_seconds
        )
        self.node_binary = os.getenv("LITEPARSE_NODE_BINARY", "node")
        self.export_dir = self._resolve_export_dir()
        self.entrypoint_path = self._resolve_entrypoint_path(self.export_dir)
        self.converter_path = self._resolve_converter_path(self.export_dir)

    async def convert_pptx_to_html(
        self, pptx_path: str, get_fonts: bool = False
    ) -> PptxToHtmlDocument:
        if not os.path.isfile(pptx_path):
            raise HTTPException(status_code=400, detail=f"PPTX not found: {pptx_path}")

        try:
            response_data = await self._run_task(
                {
                    "type": "pptx-to-html",
                    "pptx_path": pptx_path,
                    "get_fonts": get_fonts,
                },
                "PPTX-to-HTML export task did not produce a response file",
            )

            output_path = self._resolve_output_path(response_data)
            with open(output_path, "r", encoding="utf-8") as output_file:
                output_data = json.load(output_file)
            output_data = self._scope_conversion_artifacts(
                output_path,
                output_data,
                "pptx-to-html",
            )

            return PptxToHtmlDocument(**output_data)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail="PPTX-to-HTML export produced invalid JSON output",
            ) from exc

    async def convert_pptx_to_json(
        self, pptx_path: str, *, session_id: str
    ) -> PptxToJsonDocument:
        """Convert a PPTX into raw slide layouts, extracting embedded media to disk.

        `session_id` is required by the converter's task schema even though it does
        not determine the output location -- the runtime allocates its own directory
        per run. Callers that need to reclaim the extracted media must use
        `output_dir`, not a path derived from the session id.
        """
        if not os.path.isfile(pptx_path):
            raise HTTPException(status_code=400, detail=f"PPTX not found: {pptx_path}")

        try:
            response_data = await self._run_task(
                {
                    "type": "pptx-to-json",
                    "pptx_path": pptx_path,
                    "session_id": session_id,
                },
                "PPTX-to-JSON export task did not produce a response file",
            )

            output_path = self._resolve_output_path(response_data)
            with open(output_path, "r", encoding="utf-8") as output_file:
                output_data = json.load(output_file)
            output_data = self._scope_conversion_artifacts(
                output_path,
                output_data,
                "pptx-to-json",
            )

            if not isinstance(output_data, dict):
                raise HTTPException(
                    status_code=500,
                    detail="PPTX-to-JSON export produced invalid output",
                )
            output_data.setdefault("output_dir", os.path.dirname(output_path))
            return PptxToJsonDocument(**output_data)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail="PPTX-to-JSON export produced invalid JSON output",
            ) from exc
        except ValidationError as exc:
            raise HTTPException(
                status_code=500,
                detail="PPTX-to-JSON export produced invalid output",
            ) from exc

    @staticmethod
    def _scope_conversion_artifacts(
        output_path: str,
        output_data: Any,
        root_name: Literal["pptx-to-html", "pptx-to-json"],
    ) -> Any:
        owner_id = get_current_owner_id()
        if owner_id is None:
            return output_data

        source_dir = os.path.realpath(os.path.dirname(output_path))
        session_id = os.path.basename(source_dir)
        app_data = get_app_data_directory_env()
        if not app_data:
            raise HTTPException(
                status_code=500,
                detail="APP_DATA_DIRECTORY is required for conversion artifacts",
            )
        source_root = os.path.realpath(os.path.join(app_data, root_name))
        owner_root = os.path.realpath(
            os.path.join(source_root, "users", str(owner_id))
        )
        source_parent = os.path.dirname(source_dir)
        if source_parent not in {source_root, owner_root} or session_id == "users":
            raise HTTPException(
                status_code=500,
                detail="Conversion task returned an output outside its asset directory",
            )
        target_dir = os.path.join(
            owner_root,
            session_id,
        )
        os.makedirs(os.path.dirname(target_dir), exist_ok=True)
        if os.path.realpath(target_dir) != source_dir:
            shutil.move(source_dir, target_dir)

        source_url = f"/app_data/{root_name}/{session_id}"
        target_url = (
            f"/app_data/{root_name}/users/{owner_id}/{session_id}"
        )

        def rewrite(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(source_dir, target_dir).replace(
                    source_url,
                    target_url,
                )
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            return value

        return rewrite(output_data)

    async def extract_schema(self, url: str) -> ExtractSchemaDocument:
        LOGGER.info(
            "[export_runtime] extract_schema spawn "
            "url=%s entrypoint=%s export_dir=%s",
            url,
            self.entrypoint_path,
            self.export_dir,
        )
        try:
            response_data = await self._run_task(
                {
                    "type": "extract-schema",
                    "url": url,
                },
                "Extract-schema task did not produce a response file",
            )
            slides = response_data.get("slides") if isinstance(response_data, dict) else None
            slide_n = len(slides) if isinstance(slides, list) else "?"
            LOGGER.info(
                "[export_runtime] extract_schema node finished url=%s "
                "response_name=%r ordered=%s icon_weight=%s slides=%s",
                url,
                response_data.get("name") if isinstance(response_data, dict) else None,
                response_data.get("ordered") if isinstance(response_data, dict) else None,
                response_data.get("icon_weight") if isinstance(response_data, dict) else None,
                slide_n,
            )
            return ExtractSchemaDocument(**response_data)
        except ValidationError as exc:
            LOGGER.exception(
                "[export_runtime] extract_schema pydantic validation failed url=%s",
                url,
            )
            raise HTTPException(
                status_code=500,
                detail="Extract-schema task produced invalid output",
            ) from exc


EXPORT_TASK_SERVICE = PptxImportConversionsService()
