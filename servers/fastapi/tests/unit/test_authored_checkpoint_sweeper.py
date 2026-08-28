import asyncio
from datetime import timedelta
import os
from pathlib import Path
import time
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services import authored_checkpoint_service as checkpoints
from services import presentation_generation_job_service as jobs


async def _sweeper_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'artifact-sweeper.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoints, "async_session_maker", maker)
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints")
    )
    return engine, maker


def _artifact_directory(root: Path, job_id: uuid.UUID, *, old: bool = True) -> Path:
    directory = root / str(job_id)
    directory.mkdir(parents=True)
    (directory / "slide-0.png").write_bytes(b"checkpoint")
    if old:
        timestamp = time.time() - 3600
        os.utime(directory, (timestamp, timestamp))
    return directory


def test_sweeper_removes_only_expired_terminal_and_orphan_directories(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _sweeper_database(tmp_path, monkeypatch)
        now = checkpoints.get_current_utc_datetime()

        def job_for(
            state: str,
            *,
            old: bool,
            attempt_token: str | None = None,
        ):
            status = AsyncPresentationGenerationTaskModel(status="error")
            terminal_at = now - timedelta(hours=1 if old else 0)
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                request_sha256=uuid.uuid4().hex * 2,
                request_payload={"template": "authored"},
                state=state,
                attempt_token=attempt_token,
                failed_at=terminal_at if state == "failed" else None,
                published_at=terminal_at if state == "published" else None,
                updated_at=terminal_at,
            )
            return status, job

        old_failed = job_for("failed", old=True)
        old_published = job_for("published", old=True)
        recent_failed = job_for("failed", old=False)
        active_running = job_for("running", old=True, attempt_token="active")
        awaiting_resume = job_for("awaiting_resume", old=True)
        fenced_failed = job_for("failed", old=True, attempt_token="unexpected-owner")
        try:
            async with maker() as session:
                for status, job in (
                    old_failed,
                    old_published,
                    recent_failed,
                    active_running,
                    awaiting_resume,
                    fenced_failed,
                ):
                    session.add_all((status, job))
                await session.commit()

            root = checkpoints.checkpoint_root()
            directories = {
                job.id: _artifact_directory(root, job.id)
                for _, job in (
                    old_failed,
                    old_published,
                    recent_failed,
                    active_running,
                    awaiting_resume,
                    fenced_failed,
                )
            }
            orphan_id = uuid.uuid4()
            old_orphan = _artifact_directory(root, orphan_id)
            recent_orphan = _artifact_directory(root, uuid.uuid4(), old=False)
            invalid = root / "not-a-job"
            invalid.mkdir()
            old_timestamp = time.time() - 3600
            os.utime(invalid, (old_timestamp, old_timestamp))

            swept = await checkpoints.sweep_expired_checkpoint_artifacts(
                ttl_seconds=60,
                batch_size=10,
            )

            assert swept["deleted_terminal"] == 2
            assert swept["deleted_orphan"] == 1
            assert not directories[old_failed[1].id].exists()
            assert not directories[old_published[1].id].exists()
            assert directories[recent_failed[1].id].is_dir()
            assert directories[active_running[1].id].is_dir()
            assert directories[awaiting_resume[1].id].is_dir()
            assert directories[fenced_failed[1].id].is_dir()
            assert not old_orphan.exists()
            assert recent_orphan.is_dir()
            assert invalid.is_dir()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_and_published_artifacts_use_independent_ttls(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _sweeper_database(tmp_path, monkeypatch)
        now = checkpoints.get_current_utc_datetime()

        def terminal_job(state: str):
            status = AsyncPresentationGenerationTaskModel(status="error")
            terminal_at = now - timedelta(hours=2)
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                request_sha256=uuid.uuid4().hex * 2,
                request_payload={"template": "authored"},
                state=state,
                failed_at=terminal_at if state == "failed" else None,
                published_at=terminal_at if state == "published" else None,
                updated_at=terminal_at,
            )
            return status, job

        failed = terminal_job("failed")
        published = terminal_job("published")
        try:
            async with maker() as session:
                session.add_all((*failed, *published))
                await session.commit()

            root = checkpoints.checkpoint_root()
            failed_directory = _artifact_directory(root, failed[1].id)
            published_directory = _artifact_directory(root, published[1].id)
            monkeypatch.setenv("AUTHORED_CHECKPOINT_ARTIFACT_TTL_SECONDS", "3600")
            monkeypatch.setenv(
                "AUTHORED_CHECKPOINT_FAILED_ARTIFACT_TTL_SECONDS", "60"
            )
            monkeypatch.setenv(
                "AUTHORED_CHECKPOINT_PUBLISHED_ARTIFACT_TTL_SECONDS", "10800"
            )

            swept = await checkpoints.sweep_expired_checkpoint_artifacts(
                batch_size=10
            )

            assert swept["deleted_terminal"] == 1
            assert not failed_directory.exists()
            assert published_directory.is_dir()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_state_ttls_fall_back_to_legacy_ttl_environment(monkeypatch):
    monkeypatch.setenv("AUTHORED_CHECKPOINT_ARTIFACT_TTL_SECONDS", "1234")
    monkeypatch.delenv(
        "AUTHORED_CHECKPOINT_FAILED_ARTIFACT_TTL_SECONDS", raising=False
    )
    monkeypatch.delenv(
        "AUTHORED_CHECKPOINT_PUBLISHED_ARTIFACT_TTL_SECONDS", raising=False
    )

    legacy_ttl = checkpoints._artifact_ttl_seconds()

    assert legacy_ttl == 1234
    assert checkpoints._failed_artifact_ttl_seconds(legacy_ttl) == 1234
    assert checkpoints._published_artifact_ttl_seconds(legacy_ttl) == 1234


def test_sweep_enforces_total_disk_budget(tmp_path, monkeypatch):
    async def scenario():
        engine, _ = await _sweeper_database(tmp_path, monkeypatch)
        try:
            root = checkpoints.checkpoint_root()
            directories = [
                _artifact_directory(root, uuid.uuid4()) for _ in range(2)
            ]

            swept = await checkpoints.sweep_expired_checkpoint_artifacts(
                ttl_seconds=60,
                batch_size=10,
                max_bytes=len(b"checkpoint"),
                max_files=10,
            )

            assert swept["deleted_orphan"] == 1
            assert swept["deleted_bytes"] == len(b"checkpoint")
            assert swept["deleted_files"] == 1
            assert swept["skipped_budget"] == 1
            assert sum(directory.exists() for directory in directories) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_failure_isolated_and_retried(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _sweeper_database(tmp_path, monkeypatch)
        now = checkpoints.get_current_utc_datetime()
        rows = []
        for _ in range(2):
            status = AsyncPresentationGenerationTaskModel(status="error")
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                request_sha256=uuid.uuid4().hex * 2,
                request_payload={"template": "authored"},
                state="failed",
                failed_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
            rows.append((status, job))
        try:
            async with maker() as session:
                for row in rows:
                    session.add_all(row)
                await session.commit()

            root = checkpoints.checkpoint_root()
            directories = [_artifact_directory(root, row[1].id) for row in rows]
            failed_directory = directories[0]
            real_rmtree = checkpoints.shutil.rmtree

            def flaky_rmtree(path):
                if Path(path) == failed_directory:
                    raise OSError("injected cleanup failure")
                return real_rmtree(path)

            monkeypatch.setattr(checkpoints.shutil, "rmtree", flaky_rmtree)
            first = await checkpoints.sweep_expired_checkpoint_artifacts(
                ttl_seconds=60,
                batch_size=10,
            )

            assert first["errors"] == 1
            assert first["deleted_terminal"] == 1
            assert failed_directory.is_dir()

            monkeypatch.setattr(checkpoints.shutil, "rmtree", real_rmtree)
            second = await checkpoints.sweep_expired_checkpoint_artifacts(
                ttl_seconds=60,
                batch_size=10,
            )
            assert second["errors"] == 0
            assert second["deleted_terminal"] == 1
            assert not failed_directory.exists()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_orphan_sweep_is_bounded_by_batch_size(tmp_path, monkeypatch):
    async def scenario():
        engine, _ = await _sweeper_database(tmp_path, monkeypatch)
        try:
            root = checkpoints.checkpoint_root()
            directories = [
                _artifact_directory(root, uuid.uuid4()) for _ in range(4)
            ]

            first = await checkpoints.sweep_expired_checkpoint_artifacts(
                ttl_seconds=60,
                batch_size=2,
            )
            assert first["deleted_orphan"] == 2
            assert sum(directory.exists() for directory in directories) == 2

            second = await checkpoints.sweep_expired_checkpoint_artifacts(
                ttl_seconds=60,
                batch_size=2,
            )
            assert second["deleted_orphan"] == 2
            assert not any(directory.exists() for directory in directories)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_periodic_sweep_is_interval_gated(monkeypatch):
    async def scenario():
        calls = 0

        async def fake_sweep():
            nonlocal calls
            calls += 1
            return {"deleted_terminal": calls}

        ticks = iter((10.0, 50.0, 111.0))
        monkeypatch.setattr(checkpoints, "sweep_expired_checkpoint_artifacts", fake_sweep)
        monkeypatch.setattr(
            checkpoints.time,
            "monotonic",
            lambda: next(ticks, 111.0),
        )
        monkeypatch.setattr(checkpoints, "_last_artifact_sweep_monotonic", None)
        monkeypatch.setenv("AUTHORED_CHECKPOINT_SWEEP_INTERVAL_SECONDS", "100")

        assert await checkpoints.maybe_sweep_expired_checkpoint_artifacts() == {
            "deleted_terminal": 1
        }
        assert await checkpoints.maybe_sweep_expired_checkpoint_artifacts() is None
        assert await checkpoints.maybe_sweep_expired_checkpoint_artifacts() == {
            "deleted_terminal": 2
        }

    asyncio.run(scenario())


def test_cleanup_never_follows_a_checkpoint_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints")
    )
    root = checkpoints.checkpoint_root()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    link = root / str(uuid.uuid4())
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this host")

    with pytest.raises(ValueError, match="invalid_checkpoint_path"):
        checkpoints.cleanup_checkpoint_artifacts(uuid.UUID(link.name))
    assert marker.read_text(encoding="utf-8") == "keep"


def test_orphan_evidence_is_retained_until_explicit_seal(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints")
    )
    root = checkpoints.checkpoint_root()
    orphan = root / str(uuid.uuid4())
    orphan.mkdir()
    (orphan / "slide-0.png").write_bytes(b"png")
    (orphan / ".slide-0.png.evidence.json").write_text("{}", encoding="utf-8")

    assert checkpoints._artifacts._has_unsealed_evidence(orphan)
    checkpoints.mark_checkpoint_artifacts_sealed(uuid.UUID(orphan.name))
    assert (orphan / ".sealed").read_text(encoding="utf-8").startswith("authored-checkpoint-sealed")
    assert not checkpoints._artifacts._has_unsealed_evidence(orphan)


def test_dispatch_continues_when_artifact_sweep_fails(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _sweeper_database(tmp_path, monkeypatch)
        recovered = 0

        async def fake_recover(*, include_queued=True):
            nonlocal recovered
            assert include_queued is False
            recovered += 1
            return 0

        async def failing_sweep():
            raise RuntimeError("injected sweeper failure")

        monkeypatch.setattr(jobs, "async_session_maker", maker)
        monkeypatch.setattr(jobs, "recover_generation_jobs", fake_recover)
        monkeypatch.setattr(
            checkpoints,
            "maybe_sweep_expired_checkpoint_artifacts",
            failing_sweep,
        )
        try:
            await jobs._dispatch_ready_jobs()
            assert recovered == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
