"""Isolated canvas bench backend configuration."""

import os

ENV_NAMES = ("CANVAS_STAGES_PATH",)


def get_canvas_stages_path_env() -> str | None:
    return os.getenv("CANVAS_STAGES_PATH") or None
