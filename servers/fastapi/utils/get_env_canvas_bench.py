"""Admission gate for request-scoped, disposable canvas experiments."""

from utils.get_env import get_setting_env


def is_canvas_bench_enabled() -> bool:
    """Only an explicit true enables bench inputs; default is fail closed."""
    return get_setting_env("CANVAS_BENCH_ENABLED") == "true"
