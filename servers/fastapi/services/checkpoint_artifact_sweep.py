"""Interval-gated sweep of expired generation checkpoint artifacts.

The durable-job dispatcher runs this for every generation driver. It moved
here unchanged from ``authored_checkpoint_service`` so the dispatcher does not
import that facade (and through it the authored HTML admission chain);
``authored_checkpoint_service`` re-exports it. The sweep itself stays in
``authored_checkpoint_artifacts``.
"""

from __future__ import annotations

import time

from services.authored_checkpoint_artifacts import sweep_expired_checkpoint_artifacts
from services.authored_checkpoint_sweep_budget import _configured_positive_int
from services.authored_generation_observability import log_authored_generation_observation


_DEFAULT_SWEEP_INTERVAL_SECONDS = 60 * 60
_last_artifact_sweep_monotonic: float | None = None


async def maybe_sweep_expired_checkpoint_artifacts() -> dict[str, int] | None:
    """Run the bounded sweeper at most once per configured process interval."""

    global _last_artifact_sweep_monotonic
    interval = _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_INTERVAL_SECONDS",
        _DEFAULT_SWEEP_INTERVAL_SECONDS,
    )
    now = time.monotonic()
    if (
        _last_artifact_sweep_monotonic is not None
        and now - _last_artifact_sweep_monotonic < interval
    ):
        return None
    _last_artifact_sweep_monotonic = now
    result = await sweep_expired_checkpoint_artifacts()
    log_authored_generation_observation(
        name="artifact_sweep",
        outcome="success",
        scanned_terminal=result.get("scanned_terminal", 0),
        deleted_terminal=result.get("deleted_terminal", 0),
        scanned_orphan=result.get("scanned_orphan", 0),
        deleted_orphan=result.get("deleted_orphan", 0),
        deleted_bytes=result.get("deleted_bytes", 0),
        deleted_files=result.get("deleted_files", 0),
        skipped_budget=result.get("skipped_budget", 0),
        errors=result.get("errors", 0),
    )
    return result
