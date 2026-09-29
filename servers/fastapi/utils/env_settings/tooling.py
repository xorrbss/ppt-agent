"""Environment knobs that locate external tools and LiteParse runtime options.

Layering: this module may import only stdlib, ``utils.get_env``,
``utils.env_parse`` and (type only) ``utils.runtime_settings``.  It never
imports ``services`` or ``api``.  Nothing is read at import time.

Values are returned raw (``str | None``, ``None`` meaning unset) so that every
call site keeps its own historical normalisation (strip, fallback chains,
default binary names).  Numeric knobs go through ``env_int``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from utils.get_env import env_int, get_setting_env

if TYPE_CHECKING:
    from utils.runtime_settings import RuntimeSettings

ENV_NAMES: tuple[str, ...] = (
    "LITEPARSE_NODE_BINARY",
    "LITEPARSE_RUNNER_PATH",
    "LITEPARSE_RUNNER_OUTPUT",
    "LITEPARSE_OCR_SERVER_URL",
    "LITEPARSE_TESSDATA_PATH",
    "LITEPARSE_DPI",
    "LITEPARSE_NUM_WORKERS",
    "TESSERACT_CMD",
    "TESSDATA_PREFIX",
    "PRESENTON_APP_ROOT",
    "AUTHORED_OCR_TESSDATA_PATH",
    "SOFFICE_PATH",
    "IMAGEMAGICK_BINARY",
    "NODE_BINARY",
    "PLAYWRIGHT_BROWSERS_PATH",
)

DEFAULT_LITEPARSE_DPI = 120
LITEPARSE_DPI_RANGE = (72, 600)
LITEPARSE_WORKERS_RANGE = (1, 64)


def _default_num_workers() -> int:
    return max((os.cpu_count() or 1) - 2, 1)


@dataclass(frozen=True, slots=True)
class LiteparseSettings:
    """LiteParse knobs; ``None`` means the variable is unset."""

    node_binary: str  # unset -> "node" (a blank value stays blank, as before)
    runner_path: str | None  # unset -> service resolves its own default
    runner_output: str | None
    ocr_server_url: str | None
    tessdata_path: str | None
    dpi: int  # clamped to 72..600; malformed -> default
    num_workers: int  # clamped to 1..64; malformed -> default


def get_liteparse_settings(
    settings: RuntimeSettings | None = None,
    *,
    default_dpi: int = DEFAULT_LITEPARSE_DPI,
    default_num_workers: int | None = None,
) -> LiteparseSettings:
    """Read LiteParse knobs.

    Numeric knobs use ``on_invalid="clamp"``: out-of-range values clamp (as the
    old private helper did).  Behaviour change: malformed text no longer logs a
    warning; it silently yields the default.
    """
    node = get_setting_env("LITEPARSE_NODE_BINARY", settings)
    workers_default = (
        default_num_workers
        if default_num_workers is not None
        else _default_num_workers()
    )
    return LiteparseSettings(
        node_binary="node" if node is None else node,
        runner_path=get_setting_env("LITEPARSE_RUNNER_PATH", settings),
        runner_output=get_setting_env("LITEPARSE_RUNNER_OUTPUT", settings),
        ocr_server_url=get_setting_env("LITEPARSE_OCR_SERVER_URL", settings),
        tessdata_path=get_setting_env("LITEPARSE_TESSDATA_PATH", settings),
        dpi=env_int(
            "LITEPARSE_DPI",
            default_dpi,
            minimum=LITEPARSE_DPI_RANGE[0],
            maximum=LITEPARSE_DPI_RANGE[1],
            on_invalid="clamp",
            settings=settings,
        ),
        num_workers=env_int(
            "LITEPARSE_NUM_WORKERS",
            workers_default,
            minimum=LITEPARSE_WORKERS_RANGE[0],
            maximum=LITEPARSE_WORKERS_RANGE[1],
            on_invalid="clamp",
            settings=settings,
        ),
    )


@dataclass(frozen=True, slots=True)
class ExternalToolPaths:
    """Raw external tool locations; ``None`` means unset (blank is preserved)."""

    tesseract_cmd: str | None
    tessdata_prefix: str | None
    presenton_app_root: str | None
    authored_ocr_tessdata_path: str | None
    soffice_path: str | None
    imagemagick_binary: str | None
    node_binary: str | None
    playwright_browsers_path: str | None


def get_external_tool_paths(
    settings: RuntimeSettings | None = None,
) -> ExternalToolPaths:
    return ExternalToolPaths(
        tesseract_cmd=get_setting_env("TESSERACT_CMD", settings),
        tessdata_prefix=get_setting_env("TESSDATA_PREFIX", settings),
        presenton_app_root=get_setting_env("PRESENTON_APP_ROOT", settings),
        authored_ocr_tessdata_path=get_setting_env(
            "AUTHORED_OCR_TESSDATA_PATH", settings
        ),
        soffice_path=get_setting_env("SOFFICE_PATH", settings),
        imagemagick_binary=get_setting_env("IMAGEMAGICK_BINARY", settings),
        node_binary=get_setting_env("NODE_BINARY", settings),
        playwright_browsers_path=get_setting_env(
            "PLAYWRIGHT_BROWSERS_PATH", settings
        ),
    )
