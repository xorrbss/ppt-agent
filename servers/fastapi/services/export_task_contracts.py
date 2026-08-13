from __future__ import annotations

import logging
import os
import re
import subprocess

from pydantic import BaseModel, model_validator

from utils.icon_weights import DEFAULT_ICON_WEIGHT, extract_icon_weight_from_settings


LOGGER = logging.getLogger(__name__)

EXPORT_DIRECTORY_MODE = 0o755
EXPORT_FILE_MODE = 0o644
DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS = 30 * 60
MIN_EXPORT_TASK_TIMEOUT_SECONDS = 60


def export_task_timeout_seconds() -> int:
    """Return the configurable runtime limit used for large authored decks."""

    raw_value = (os.getenv("EXPORT_TASK_TIMEOUT_SECONDS") or "").strip()
    if not raw_value:
        return DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS
    try:
        configured = int(raw_value)
    except ValueError:
        LOGGER.warning(
            "Ignoring invalid EXPORT_TASK_TIMEOUT_SECONDS=%r; using %s seconds",
            raw_value,
            DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS,
        )
        return DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS
    return max(configured, MIN_EXPORT_TASK_TIMEOUT_SECONDS)


def redact_export_url(url: str) -> str:
    """Mask the admin session token in an export URL before logging."""

    return re.sub(r"(exportSession=)[^&]*", r"\1***", url)


def windows_hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def sys_platform() -> str:
    if os.name == "nt":
        return "win32"
    return os.sys.platform


def sys_arch() -> str:
    machine = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    if not machine and hasattr(os, "uname"):
        machine = os.uname().machine.lower()

    arch_map = {
        "x86_64": "x64",
        "amd64": "x64",
        "x64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return arch_map.get(machine, machine or "x64")


class PptxToHtmlDocument(BaseModel):
    slides: list[str]
    font_css: str = ""
    width: float
    height: float
    images_dir: str
    fonts_dir: str


class PptxToJsonDocument(BaseModel):
    """Permissive transport shape emitted by the bundled PPTX converter."""

    layouts: list[dict]
    output_dir: str = ""


class PresentationExportTaskResult(BaseModel):
    path: str


class ExtractSchemaSlide(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None
    json_schema: dict


class ExtractSchemaDocument(BaseModel):
    name: str
    ordered: bool = False
    icon_weight: str = DEFAULT_ICON_WEIGHT
    slides: list[ExtractSchemaSlide]

    @model_validator(mode="before")
    @classmethod
    def normalize_icon_weight(cls, data):
        if isinstance(data, dict):
            normalized = dict(data)
            normalized["icon_weight"] = extract_icon_weight_from_settings(normalized)
            return normalized
        return data

