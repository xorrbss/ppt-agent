import asyncio
import logging
import os

import pytest

from services import export_retention_service as retention


DAY_SECONDS = 24 * 60 * 60


def _artifact(path, *, atime: float, mtime: float, content: bytes = b"export"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (atime, mtime))
    return path


def test_sweep_deletes_only_expired_supported_export_artifacts(
    tmp_path, monkeypatch, caplog
):
    app_data = tmp_path / "app-data"
    exports = app_data / "exports"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    now = 1_800_000_000.0
    old = now - 8 * DAY_SECONDS

    expired = [
        _artifact(exports / "deck.pptx", atime=old, mtime=old),
        _artifact(exports / "deck.pptx.quality.json", atime=old, mtime=old),
        _artifact(exports / "users" / "owner" / "deck.pdf", atime=old, mtime=old),
    ]
    unsupported = _artifact(exports / "deck.json", atime=old, mtime=old)
    recently_accessed = _artifact(
        exports / "recent.pdf",
        atime=now - DAY_SECONDS,
        mtime=old,
    )

    with caplog.at_level(logging.INFO, logger=retention.__name__):
        result = retention.sweep_expired_exports(now=now)

    assert result["scanned"] == 4
    assert result["deleted"] == 3
    assert result["deleted_bytes"] == 3 * len(b"export")
    assert result["skipped_recent"] == 1
    assert all(not path.exists() for path in expired)
    assert unsupported.exists()
    assert recently_accessed.exists()
    assert "deleted=3" in caplog.text


def test_sweep_retains_files_modified_within_minimum_age(tmp_path, monkeypatch):
    app_data = tmp_path / "app-data"
    exports = app_data / "exports"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    now = 1_800_000_000.0
    in_progress = _artifact(
        exports / "rendering.pptx",
        atime=now - 30 * DAY_SECONDS,
        mtime=now - 30 * 60,
    )

    result = retention.sweep_expired_exports(retention_days=0, now=now)

    assert result["deleted"] == 0
    assert result["skipped_recent"] == 1
    assert in_progress.exists()


def test_sweep_never_follows_symlinks_outside_exports(tmp_path, monkeypatch):
    app_data = tmp_path / "app-data"
    exports = app_data / "exports"
    exports.mkdir(parents=True)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    now = 1_800_000_000.0
    old = now - 8 * DAY_SECONDS
    outside = _artifact(tmp_path / "outside.pdf", atime=old, mtime=old)
    link = exports / "linked.pdf"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are not available on this host")

    result = retention.sweep_expired_exports(now=now)

    assert result["deleted"] == 0
    assert result["skipped_unsafe"] == 1
    assert link.is_symlink()
    assert outside.exists()


def test_periodic_sweep_is_interval_gated(monkeypatch):
    async def scenario():
        calls = 0

        def fake_sweep():
            nonlocal calls
            calls += 1
            return {"deleted": calls}

        async def run_inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        ticks = iter((10.0, 50.0, 111.0))
        monkeypatch.setattr(retention, "sweep_expired_exports", fake_sweep)
        monkeypatch.setattr(retention.asyncio, "to_thread", run_inline)
        monkeypatch.setattr(
            retention.time,
            "monotonic",
            lambda: next(ticks, 111.0),
        )
        monkeypatch.setattr(retention, "_last_export_sweep_monotonic", None)
        monkeypatch.setenv("EXPORT_SWEEP_INTERVAL_SECONDS", "100")

        assert await retention.maybe_sweep_expired_exports() == {"deleted": 1}
        assert await retention.maybe_sweep_expired_exports() is None
        assert await retention.maybe_sweep_expired_exports() == {"deleted": 2}

    asyncio.run(scenario())


def test_invalid_retention_values_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))

    with pytest.raises(ValueError, match="export_retention_days"):
        retention.sweep_expired_exports(retention_days=-1)
    with pytest.raises(ValueError, match="minimum_export_age"):
        retention.sweep_expired_exports(minimum_age_seconds=-1)
