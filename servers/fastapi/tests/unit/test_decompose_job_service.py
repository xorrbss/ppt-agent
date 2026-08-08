import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import services.decompose_job_service as job_module
from services.decompose_job_service import (
    DecomposeJobConflictError,
    DecomposeJobService,
)
from services.document_conversion_service import DocumentConversionError
from services.temp_file_service import TempFileService


async def _wait_for_terminal(
    service: DecomposeJobService, owner_key: str, job_id, timeout: float = 2
):
    async with asyncio.timeout(timeout):
        while True:
            snapshot = await service.get_job(owner_key=owner_key, job_id=job_id)
            assert snapshot is not None
            if snapshot.status in {"succeeded", "failed", "cancelled"}:
                return snapshot
            await asyncio.sleep(0.01)


def _install_temp_service(monkeypatch, tmp_path: Path) -> TempFileService:
    temp_service = TempFileService(str(tmp_path / "decompose-jobs"))
    monkeypatch.setattr(job_module, "TEMP_FILE_SERVICE", temp_service)
    return temp_service


def test_async_job_succeeds_reports_progress_and_expires(monkeypatch, tmp_path):
    async def scenario():
        temp_service = _install_temp_service(monkeypatch, tmp_path)

        class FakeLoader:
            def __init__(self, file_paths, presentation_language=None):
                self.file_paths = file_paths
                self.documents = []

            async def load_documents(self, temp_dir):
                self.documents = [f"body:{Path(self.file_paths[0]).name}<br>end"]

        monkeypatch.setattr(job_module, "DocumentsLoader", FakeLoader)
        service = DecomposeJobService(result_ttl_seconds=60)
        snapshot, created = await service.create_job(
            owner_key="owner-a",
            file_paths=[str(tmp_path / "one.pdf"), str(tmp_path / "two.txt")],
            language="ko",
            idempotency_key="request-1",
        )
        assert created is True
        assert snapshot.status == "queued"
        assert snapshot.progress.total_files == 2

        finished = await _wait_for_terminal(service, "owner-a", snapshot.job_id)
        assert finished.status == "succeeded"
        assert finished.progress.completed_files == 2
        assert [item.name for item in finished.result or []] == ["one.pdf", "two.txt"]
        assert Path(finished.result[0].file_path).read_text(encoding="utf-8") == "body:one.pdf\nend"
        assert Path(finished.result[1].file_path).read_text(encoding="utf-8") == "body:two.txt<br>end"

        async with service._lock:
            record = service._jobs[snapshot.job_id]
            output_dir = Path(record.temp_dir)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert output_dir.is_dir()
        assert await service.cleanup_expired() == 1
        assert not output_dir.exists()
        assert await service.get_job(owner_key="owner-a", job_id=snapshot.job_id) is None
        assert Path(temp_service.base_dir).is_dir()

    asyncio.run(scenario())


def test_idempotency_reuses_identical_job_and_rejects_payload_conflict(
    monkeypatch, tmp_path
):
    async def scenario():
        _install_temp_service(monkeypatch, tmp_path)
        release = asyncio.Event()

        class BlockingLoader:
            def __init__(self, file_paths, presentation_language=None):
                self.documents = ["done"]

            async def load_documents(self, temp_dir):
                await release.wait()

        monkeypatch.setattr(job_module, "DocumentsLoader", BlockingLoader)
        service = DecomposeJobService(result_ttl_seconds=60)
        first, first_created = await service.create_job(
            owner_key="owner-a",
            file_paths=[str(tmp_path / "one.pdf")],
            language=None,
            idempotency_key="same-key",
        )
        replay, replay_created = await service.create_job(
            owner_key="owner-a",
            file_paths=[str(tmp_path / "one.pdf")],
            language=None,
            idempotency_key="same-key",
        )
        assert first_created is True
        assert replay_created is False
        assert replay.job_id == first.job_id

        with pytest.raises(DecomposeJobConflictError):
            await service.create_job(
                owner_key="owner-a",
                file_paths=[str(tmp_path / "other.pdf")],
                language=None,
                idempotency_key="same-key",
            )

        # The same client key is scoped to an owner.
        other, other_created = await service.create_job(
            owner_key="owner-b",
            file_paths=[str(tmp_path / "other.pdf")],
            language=None,
            idempotency_key="same-key",
        )
        assert other_created is True
        assert other.job_id != first.job_id
        await service.cancel_job(owner_key="owner-a", job_id=first.job_id)
        await service.cancel_job(owner_key="owner-b", job_id=other.job_id)
        release.set()

    asyncio.run(scenario())


def test_running_job_cancellation_is_cooperative_and_cleans_output(
    monkeypatch, tmp_path
):
    async def scenario():
        _install_temp_service(monkeypatch, tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingLoader:
            def __init__(self, file_paths, presentation_language=None):
                self.documents = ["done"]

            async def load_documents(self, temp_dir):
                started.set()
                await release.wait()

        monkeypatch.setattr(job_module, "DocumentsLoader", BlockingLoader)
        service = DecomposeJobService(result_ttl_seconds=60)
        created, _ = await service.create_job(
            owner_key="owner-a",
            file_paths=[str(tmp_path / "one.pdf")],
            language=None,
            idempotency_key=None,
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        cancelling, requested = await service.cancel_job(
            owner_key="owner-a", job_id=created.job_id
        )
        assert requested is True
        assert cancelling.status == "cancelling"
        release.set()

        cancelled = await _wait_for_terminal(service, "owner-a", created.job_id)
        assert cancelled.status == "cancelled"
        assert cancelled.result is None
        async with service._lock:
            assert service._jobs[created.job_id].temp_dir is None

    asyncio.run(scenario())


def test_conversion_failure_exposes_safe_error_and_keeps_details_in_logs(
    monkeypatch, tmp_path, caplog
):
    async def scenario():
        _install_temp_service(monkeypatch, tmp_path)

        class FailingLoader:
            def __init__(self, file_paths, presentation_language=None):
                self.documents = []

            async def load_documents(self, temp_dir):
                raise DocumentConversionError(
                    "command C:\\secret\\magick.exe failed with token=private",
                    code="DOCUMENT_CONVERSION_FAILED",
                    filename="photo.jpg",
                )

        monkeypatch.setattr(job_module, "DocumentsLoader", FailingLoader)
        service = DecomposeJobService(result_ttl_seconds=60)
        created, _ = await service.create_job(
            owner_key="owner-a",
            file_paths=[str(tmp_path / "photo.jpg")],
            language=None,
            idempotency_key=None,
        )
        failed = await _wait_for_terminal(service, "owner-a", created.job_id)
        assert failed.status == "failed"
        assert failed.error.code == "DOCUMENT_CONVERSION_FAILED"
        assert failed.error.filename == "photo.jpg"
        assert failed.error.request_id == str(created.job_id)
        assert failed.error.message == "Unable to convert 'photo.jpg'."
        assert "secret" not in failed.error.message
        assert "secret" in caplog.text
        assert failed.progress.current_file == "photo.jpg"
        async with service._lock:
            record = service._jobs[created.job_id]
            retained_dir = Path(record.temp_dir)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        diagnostics = json.loads(
            (retained_dir / "failure-diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["job_id"] == str(created.job_id)
        assert diagnostics["current_file"] == "photo.jpg"
        assert diagnostics["exception"]["type"] == "DocumentConversionError"
        assert "token=private" in diagnostics["exception"]["message"]
        assert retained_dir.is_dir()

        assert await service.cleanup_expired() == 1
        assert not retained_dir.exists()

    asyncio.run(scenario())


def test_unexpected_failure_retains_artifacts_and_logs_structured_context(
    monkeypatch, tmp_path, caplog
):
    async def scenario():
        _install_temp_service(monkeypatch, tmp_path)

        class FailingLoader:
            def __init__(self, file_paths, presentation_language=None):
                self.documents = []

            async def load_documents(self, temp_dir):
                Path(temp_dir, "converter-output.tmp").write_text(
                    "partial output", encoding="utf-8"
                )
                raise RuntimeError("converter crashed at internal-stage-47")

        monkeypatch.setattr(job_module, "DocumentsLoader", FailingLoader)
        service = DecomposeJobService(result_ttl_seconds=60)
        created, _ = await service.create_job(
            owner_key="owner-a",
            file_paths=[str(tmp_path / "broken.pdf")],
            language=None,
            idempotency_key=None,
        )

        failed = await _wait_for_terminal(service, "owner-a", created.job_id)
        assert failed.status == "failed"
        assert failed.error.code == "DOCUMENT_DECOMPOSE_FAILED"
        assert failed.error.message == "Document processing failed. Please try again."
        assert "internal-stage-47" not in failed.error.message
        assert failed.error.filename == "broken.pdf"
        assert failed.error.request_id == str(created.job_id)
        assert failed.progress.current_file == "broken.pdf"

        async with service._lock:
            record = service._jobs[created.job_id]
            retained_dir = Path(record.temp_dir)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        assert (retained_dir / "converter-output.tmp").is_file()
        diagnostics = json.loads(
            (retained_dir / "failure-diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["exception"]["type"] == "RuntimeError"
        assert "internal-stage-47" in diagnostics["exception"]["traceback"]
        assert f"job_id={created.job_id}" in caplog.text
        assert "exception_type=RuntimeError" in caplog.text
        assert "current_file='broken.pdf'" in caplog.text
        assert "internal-stage-47" in caplog.text

        assert await service.cleanup_expired() == 1
        assert not retained_dir.exists()

    asyncio.run(scenario())
