"""Admission gate for request-scoped, disposable canvas experiments."""

from utils.get_env import get_setting_env


def is_canvas_bench_enabled() -> bool:
    """Only an explicit true enables bench inputs; default is fail closed."""
    return get_setting_env("CANVAS_BENCH_ENABLED") == "true"


def get_svg_authoring_profile_revision_env() -> int | None:
    """``SVG_AUTHORING_PROFILE_REVISION``: the svg authoring profile revision a fresh default
    admission freezes, for paired measurement runs of two backends. Unset or empty: the
    code's current revision. Any other value must be a revision number the admission
    accepts (``services/svg_authoring_profile.py``); a malformed one refuses the admission.
    Internal knob, listed in ``tests/support/internal_env_names.py``.
    """
    value = (get_setting_env("SVG_AUTHORING_PROFILE_REVISION") or "").strip()
    if not value:
        return None
    if not value.isdecimal():
        raise ValueError("svg_authoring_profile_revision_invalid")
    return int(value)
