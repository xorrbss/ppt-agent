"""Cluster/runner concurrency knobs kept behind the get_env facade.

Split out of ``utils/get_env.py`` (a frozen file-size exception) rather than
grown in place; ``utils/get_env.py`` re-exports these so callers keep using
the single ``utils.get_env`` facade.

All four values are read once at import/runner-construction time (module
load for the authored job-service and job-runtime constants, and
``WorkspaceOperationRunner.__init__``/``default_trusted_worker_scheduler_policy``
for the other two). Changing the backing environment variable or managed
setting therefore needs a process restart to take effect -- there is no
runtime-settings hot path for these four knobs.
"""

from __future__ import annotations

from utils.runtime_settings import RuntimeSettings


def get_authored_job_concurrency_env(settings: RuntimeSettings | None = None) -> int:
    """In-process authored job semaphore size (default 1)."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("AUTHORED_JOB_CONCURRENCY", settings) or 1


def get_authored_cluster_job_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int:
    """Database-backed cluster-wide authored job cap.

    Falls back to ``AUTHORED_JOB_CONCURRENCY`` (default 1) so adding FastAPI
    workers does not silently multiply provider concurrency and spend.
    """
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "AUTHORED_CLUSTER_JOB_CONCURRENCY", settings
    ) or get_authored_job_concurrency_env(settings)


def get_workspace_operation_max_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int:
    """Max concurrently-running workspace operations per runner (default 2)."""
    from utils.get_env import _optional_positive_int_setting

    return (
        _optional_positive_int_setting("WORKSPACE_OPERATION_MAX_CONCURRENCY", settings)
        or 2
    )


def get_public_export_scheduler_global_cap_env(
    settings: RuntimeSettings | None = None,
) -> int:
    """Trusted-executor scheduler global concurrency cap (default 4)."""
    from utils.get_env import _optional_positive_int_setting

    return (
        _optional_positive_int_setting("PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP", settings)
        or 4
    )


def get_svg_author_concurrency_env(settings: RuntimeSettings | None = None) -> int | None:
    """Explicit SVG author-call concurrency, or ``None`` for the profile default."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("SVG_AUTHOR_CONCURRENCY", settings)


def get_svg_author_concurrency_source_reflow_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit SVG author-call concurrency for source-reflow decks."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW", settings
    )


def get_svg_render_concurrency_env(settings: RuntimeSettings | None = None) -> int | None:
    """Explicit SVG page-render concurrency, or ``None`` for the profile default."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("SVG_RENDER_CONCURRENCY", settings)


def get_svg_render_concurrency_source_reflow_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit SVG page-render concurrency for source-reflow decks."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "SVG_RENDER_CONCURRENCY_SOURCE_REFLOW", settings
    )


def get_svg_illustration_budget_env(settings: RuntimeSettings | None = None) -> int | None:
    """Explicit per-deck illustration budget shared by the SVG driver's asset
    stage, or ``None`` for the profile default. Name agreed with c3-assets so
    the illustration stage can read the same field once it moves into
    ``generation_core/assets/``.
    """
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("SVG_ILLUSTRATION_BUDGET", settings)


SVG_ILLUSTRATION_MODES = ("off", "vector", "hybrid")


def get_svg_illustration_mode_env(settings: RuntimeSettings | None = None) -> str:
    """How the SVG driver's asset stage makes the deck's pictures.

    ``off`` keeps the image-provider scenes only; ``vector`` has the model draw
    scenes, cover/section pictures, step pictures and before/after pairs as
    vector SVG; ``hybrid`` does the same but leaves a scene the plan marked as
    a photograph to the image provider when one is configured. Read when a
    deck's asset offer is built; a resumed deck replays its persisted offer.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_ILLUSTRATION_MODE", settings) or "").strip().lower()
    return value if value in SVG_ILLUSTRATION_MODES else "off"


def get_content_structure_policy_scope_env(settings: RuntimeSettings | None = None) -> str:
    """Which decks get the content structure policy (``utils/content_structure_policy.py``):
    ``off`` (the default), ``template`` (a template-redesign deck) or ``svg`` (every svg deck).
    Read when a deck is admitted and when its pages are authored.

    Internal, opt-in knob deliberately outside ``constants/settings_registry.json``
    (like ``SVG_ILLUSTRATION_MODE``): the registry holds managed/public settings, and
    this one is experimental and off by default, because turning it on measured worse (see
    ``docs/content-structure-policy-20261104.md``). It is accounted for in
    ``tests/support/internal_env_names.py``, and resolves as a process-start env value.
    """
    from utils.content_structure_policy import normalize_scope
    from utils.get_env import get_setting_env

    return normalize_scope(get_setting_env("CONTENT_STRUCTURE_POLICY_SCOPE", settings))


SVG_ILLUSTRATION_PARTS = ("cover", "scene", "spots", "pairs", "pictograph")
DEFAULT_SVG_ILLUSTRATION_PARTS = ("cover", "scene")


def get_svg_illustration_parts_env(settings: RuntimeSettings | None = None) -> tuple[str, ...]:
    """Which model-drawn picture kinds a vector-mode deck gets (comma list).

    ``cover``, ``scene``, ``spots`` (step pictures), ``pairs`` (before/after) and
    ``pictograph`` (a percentage as ten people, composed, not drawn).
    Unset, empty or naming nothing known: ``cover,scene``. Unknown names are ignored.
    """
    from utils.get_env import get_setting_env

    raw = get_setting_env("SVG_ILLUSTRATION_PARTS", settings) or ""
    parts = tuple(part for part in SVG_ILLUSTRATION_PARTS
                  if part in {item.strip().lower() for item in raw.split(",")})
    return parts or DEFAULT_SVG_ILLUSTRATION_PARTS


def get_svg_source_output_view_env(settings: RuntimeSettings | None = None) -> str | None:
    """Raw ``SVG_SOURCE_OUTPUT_VIEW`` text (unset = v1; explicit off disables it).

    Not a concurrency knob, but an SVG driver knob read the same way: only at admission of a
    whole-page source restyle job, whose frozen snapshot then pins the parsed view
    (``services/svg_author_output_view.py`` owns the grammar), so a change affects new jobs only.
    """
    from utils.get_env import get_setting_env

    value = get_setting_env("SVG_SOURCE_OUTPUT_VIEW", settings)
    return "v1" if value is None else value


def get_svg_illustration_timeout_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit per-illustration timeout in seconds, or ``None`` for the default."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "SVG_ILLUSTRATION_TIMEOUT_SECONDS", settings
    )
