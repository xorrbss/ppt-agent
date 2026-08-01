from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import time

from utils.get_env import get_app_data_directory_env


logger = logging.getLogger(__name__)

DEFAULT_EXPORT_RETENTION_DAYS = 7
EXPORT_SWEEP_INTERVAL_SECONDS = 60 * 60
MINIMUM_EXPORT_AGE_SECONDS = 60 * 60
_EXPORT_SUFFIXES = (".pptx.quality.json", ".pptx", ".pdf")

_last_export_sweep_monotonic: float | None = None
_export_sweep_task: asyncio.Task | None = None
_export_sweep_stop: asyncio.Event | None = None


def _configured_positive_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _exports_root() -> Path:
    app_data = (get_app_data_directory_env() or "").strip()
    if not app_data:
        raise RuntimeError("APP_DATA_DIRECTORY is required for export retention")
    root = Path(app_data).expanduser().resolve() / "exports"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=True)


def _is_export_artifact(path: Path) -> bool:
    return path.name.lower().endswith(_EXPORT_SUFFIXES)


def _is_within_exports_root(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


def sweep_expired_exports(
    *,
    retention_days: int | None = None,
    minimum_age_seconds: int = MINIMUM_EXPORT_AGE_SECONDS,
    now: float | None = None,
) -> dict[str, int]:
    """Delete expired export artifacts without following paths outside exports."""

    retention = (
        retention_days
        if retention_days is not None
        else _configured_positive_int(
            "EXPORT_RETENTION_DAYS", DEFAULT_EXPORT_RETENTION_DAYS
        )
    )
    if retention < 0:
        raise ValueError("export_retention_days_must_be_nonnegative")
    if minimum_age_seconds < 0:
        raise ValueError("minimum_export_age_must_be_nonnegative")

    current_time = time.time() if now is None else now
    access_cutoff = current_time - retention * 24 * 60 * 60
    modification_cutoff = current_time - minimum_age_seconds
    root = _exports_root()
    result = {
        "scanned": 0,
        "deleted": 0,
        "deleted_bytes": 0,
        "skipped_recent": 0,
        "skipped_unsafe": 0,
        "errors": 0,
    }

    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as directory_entries:
                entries = list(directory_entries)
        except OSError:
            result["errors"] += 1
            continue

        for entry in entries:
            try:
                if entry.is_symlink():
                    result["skipped_unsafe"] += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                    continue
                candidate = Path(entry.path)
                if not entry.is_file(follow_symlinks=False) or not _is_export_artifact(
                    candidate
                ):
                    continue

                result["scanned"] += 1
                file_stat = entry.stat(follow_symlinks=False)
                if (
                    file_stat.st_atime >= access_cutoff
                    or file_stat.st_mtime >= modification_cutoff
                ):
                    result["skipped_recent"] += 1
                    continue

                # Re-resolve immediately before deletion. This rejects a file or
                # parent directory swapped for a symlink after enumeration.
                if candidate.is_symlink():
                    result["skipped_unsafe"] += 1
                    continue
                resolved = candidate.resolve(strict=True)
                if not _is_within_exports_root(resolved, root) or not resolved.is_file():
                    result["skipped_unsafe"] += 1
                    continue
                current_stat = resolved.stat()
                if (
                    current_stat.st_atime >= access_cutoff
                    or current_stat.st_mtime >= modification_cutoff
                ):
                    result["skipped_recent"] += 1
                    continue
                resolved.unlink()
                result["deleted"] += 1
                result["deleted_bytes"] += current_stat.st_size
            except FileNotFoundError:
                continue
            except OSError:
                result["errors"] += 1

    logger.info(
        "Export retention sweep completed: scanned=%d deleted=%d "
        "deleted_bytes=%d skipped_recent=%d skipped_unsafe=%d errors=%d",
        result["scanned"],
        result["deleted"],
        result["deleted_bytes"],
        result["skipped_recent"],
        result["skipped_unsafe"],
        result["errors"],
    )
    return result


async def maybe_sweep_expired_exports() -> dict[str, int] | None:
    """Run export retention at most once per configured process interval."""

    global _last_export_sweep_monotonic
    interval = _configured_positive_int(
        "EXPORT_SWEEP_INTERVAL_SECONDS", EXPORT_SWEEP_INTERVAL_SECONDS
    )
    current = time.monotonic()
    if (
        _last_export_sweep_monotonic is not None
        and current - _last_export_sweep_monotonic < interval
    ):
        return None
    _last_export_sweep_monotonic = current
    return await asyncio.to_thread(sweep_expired_exports)


async def _export_sweep_loop() -> None:
    assert _export_sweep_stop is not None
    while not _export_sweep_stop.is_set():
        try:
            await maybe_sweep_expired_exports()
        except Exception:
            logger.exception("Export retention sweep iteration failed")
        try:
            await asyncio.wait_for(
                _export_sweep_stop.wait(),
                timeout=_configured_positive_int(
                    "EXPORT_SWEEP_INTERVAL_SECONDS", EXPORT_SWEEP_INTERVAL_SECONDS
                ),
            )
        except TimeoutError:
            pass


async def start_export_retention_sweeper() -> None:
    """Sweep once at startup, then continue on a background interval."""

    global _export_sweep_stop, _export_sweep_task
    if _export_sweep_task is not None and not _export_sweep_task.done():
        return
    _export_sweep_stop = asyncio.Event()
    try:
        await maybe_sweep_expired_exports()
    except Exception:
        logger.exception("Initial export retention sweep failed")
    _export_sweep_task = asyncio.create_task(_export_sweep_loop())


async def stop_export_retention_sweeper() -> None:
    global _export_sweep_stop, _export_sweep_task
    if _export_sweep_stop is not None:
        _export_sweep_stop.set()
    if _export_sweep_task is not None:
        await _export_sweep_task
    _export_sweep_task = None
    _export_sweep_stop = None
