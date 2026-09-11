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
