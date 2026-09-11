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
