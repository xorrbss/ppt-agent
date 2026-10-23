"""Only proven OS crashes enter the retryable SVG crash category."""

import asyncio
from dataclasses import replace
import sys
import uuid

import pytest

from services import isolated_worker_process as process
from services.public_export_failure_classification import classified_public_export_error
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireResult
from tests.unit.test_svg_export_projection_error import _job
from workers.public_worker_runtime import _execute_before_job_deadline
from workers.public_worker_store import PublicWorkerExecutionError


@pytest.mark.parametrize(("exit_code", "stdout", "crashed"), [
    (-9, b"", True),
    (-11, b"", True),
    (0xC0000005, b"", True),
    (0xC000001D, b"", True),
    (-1073741819, b"", True),
    (1, b"", False),
    (0xC0000022, b"", False),
    (-1073741790, b"", False),
    (-9, b"private non-JSON failure", False),
])
def test_only_abnormal_os_exit_without_result_is_a_retryable_crash(
    monkeypatch, exit_code, stdout, crashed,
):
    async def scenario():
        job = _job()

        class Dispatcher:
            async def execute(self, **_kwargs):
                return TrustedWorkerWireResult(stdout=stdout, exit_code=exit_code, resource_metrics={"wallMillis": 1})

        monkeypatch.setattr(process, "worker_environment", lambda *_a, **_k: {})
        with pytest.raises(process.IsolatedWorkerProcessError) as raised:
            await process.execute_isolated_worker_job(
                job, presentation_id=uuid.uuid4(), command=[sys.executable], dispatcher=Dispatcher(),
            )
        error = raised.value
        assert isinstance(error, process.IsolatedWorkerCrashedError) is crashed
        classified = classified_public_export_error(error, strategy="svg", detail=error.resource_metrics)
        assert classified.retryable is crashed
        assert "private" not in str(classified.detail)
        if crashed:
            assert classified.code == "presentation_export_failed"
            assert classified.detail["reason_code"] == "public_worker_crashed"
            # Before crash normalization, empty stdout raised this decoder error.
            legacy = classified_public_export_error(
                process.IsolatedWorkerProcessError("public worker result exceeded the wire limit"),
                detail=error.resource_metrics,
            )
            authored = classified_public_export_error(error, detail=error.resource_metrics)
            assert (authored.retryable, authored.code, authored.detail) == (
                legacy.retryable, legacy.code, legacy.detail,
            )

    asyncio.run(scenario())


def test_actual_worker_deadline_signal_is_transient_but_pinned_admission_is_terminal():
    async def scenario():
        async def execute(_job, _store):
            pytest.fail("an expired job must not execute")

        with pytest.raises(PublicWorkerExecutionError) as raised:
            await _execute_before_job_deadline(
                job=replace(_job(), deadline_epoch_seconds=0), executor=execute, store=None,
            )
        assert raised.value.code == "public_worker_job_expired"
        for error in (raised.value, process.IsolatedWorkerProcessError(raised.value.code)):
            assert classified_public_export_error(error, strategy="svg").retryable is True
        admission = process.IsolatedWorkerProcessError("public_export_admission_deadline_exhausted")
        assert classified_public_export_error(admission, strategy="svg").retryable is False

    asyncio.run(scenario())
