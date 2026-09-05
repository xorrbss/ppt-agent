"""Env-configured retention TTLs and budgets for the checkpoint artifact sweeper.

Split out of ``services/authored_checkpoint_artifacts.py`` to keep that module
under the file-size ratchet.  Everything here is pure policy: how long a
checkpoint directory may survive after its job reached a terminal state, and
how much of the filesystem one sweep pass is allowed to touch before it gives
up on a directory.  The knobs are read at call time rather than at import so a
running worker picks up a changed environment without a restart, which is what
the existing sweeper tests rely on.
"""

from __future__ import annotations

import os
from pathlib import Path


_DEFAULT_ARTIFACT_TTL_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_SWEEP_BATCH_SIZE = 50
_MAX_SWEEP_BATCH_SIZE = 500
_MAX_SWEEP_SCAN_SIZE = 5000
_DEFAULT_SWEEP_MAX_BYTES = 1024 * 1024 * 1024
_DEFAULT_SWEEP_MAX_FILES = 10_000
_MAX_SWEEP_MAX_FILES = 100_000


def _configured_positive_int(
    name: str,
    default: int,
    *,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, maximum) if maximum is not None else value


def _artifact_ttl_seconds() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_ARTIFACT_TTL_SECONDS",
        _DEFAULT_ARTIFACT_TTL_SECONDS,
    )


def _failed_artifact_ttl_seconds(fallback: int) -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_FAILED_ARTIFACT_TTL_SECONDS",
        fallback,
    )


def _published_artifact_ttl_seconds(fallback: int) -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_PUBLISHED_ARTIFACT_TTL_SECONDS",
        fallback,
    )


def _artifact_sweep_batch_size() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE",
        _DEFAULT_SWEEP_BATCH_SIZE,
        maximum=_MAX_SWEEP_BATCH_SIZE,
    )


def _artifact_sweep_max_bytes() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_MAX_BYTES",
        _DEFAULT_SWEEP_MAX_BYTES,
    )


def _artifact_sweep_max_files() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_MAX_FILES",
        _DEFAULT_SWEEP_MAX_FILES,
        maximum=_MAX_SWEEP_MAX_FILES,
    )


def _directory_usage_within_budget(
    directory: Path,
    *,
    max_bytes: int,
    max_files: int,
) -> tuple[int, int] | None:
    """Measure a private directory without following links, stopping at a budget."""

    total_bytes = 0
    total_files = 0
    pending = [directory]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                total_files += 1
                if total_files > max_files:
                    return None
                stat_result = entry.stat(follow_symlinks=False)
                total_bytes += stat_result.st_size
                if total_bytes > max_bytes:
                    return None
                if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    pending.append(Path(entry.path))
    return total_bytes, total_files
