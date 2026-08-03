import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.v1.ppt.endpoints.file_decompose_jobs as endpoint_module
import services.decompose_job_service as job_module
from api.v1.ppt.endpoints.file_decompose_jobs import FILE_DECOMPOSE_JOBS_ROUTER
from models.decompose_job import DecomposeJobProgress, DecomposeJobSnapshot
from services.decompose_job_service import (
    DecomposeJobCapacityError,
    DecomposeJobService,
)
from services.document_conversion_service import DocumentConversionError
from services.temp_file_service import TempFileService


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(FILE_DECOMPOSE_JOBS_ROUTER, prefix="/api/v1/ppt")
    return app


def _wait_for_status(client: TestClient, job_id: str, expected: str):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/ppt/files/decompose/jobs/{job_id}")
        if response.status_code == 200 and response.json()["status"] == expected:
            return response
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {expected}")


def test_job_api_contract_owner_scope_idempotency_and_terminal_delete(
    monkeypatch, tmp_path
):
    class FakeLoader:
        def __init__(self, file_paths, presentation_language=None):
            self.file_paths = file_paths
            self.documents = []

        async def load_documents(self, temp_dir):
            self.documents = [f"text:{Path(self.file_paths[0]).name}"]

    owner = ["owner-a"]
    service = DecomposeJobService(result_ttl_seconds=60)
    monkeypatch.setattr(job_module, "DocumentsLoader", FakeLoader)
    monkeypatch.setattr(
        job_module,
        "TEMP_FILE_SERVICE",
        TempFileService(str(tmp_path / "api-jobs")),
    )
    monkeypatch.setattr(endpoint_module, "DECOMPOSE_JOB_SERVICE", service)
    monkeypatch.setattr(endpoint_module, "validate_document_paths", lambda value: value)
    monkeypatch.setattr(endpoint_module, "get_current_owner_id", lambda: owner[0])

    with TestClient(_app()) as client:
        created = client.post(
            "/api/v1/ppt/files/decompose/jobs",
            json={"file_paths": [str(tmp_path / "input.pdf")], "language": "ko"},
            headers={"Idempotency-Key": "upload-1"},
        )
        assert created.status_code == 202
        body = created.json()
        assert body["status"] == "queued"
        assert body["progress"] == {
            "completed_files": 0,
            "total_files": 1,
            "current_file": None,
        }
        assert body["result"] is None
        assert body["error"] is None

        succeeded = _wait_for_status(client, body["job_id"], "succeeded")
        assert succeeded.json()["result"][0]["name"] == "input.pdf"

        # An owner cannot infer whether another owner's job exists.
        owner[0] = "owner-b"
        hidden = client.get(
            f"/api/v1/ppt/files/decompose/jobs/{body['job_id']}"
        )
        assert hidden.status_code == 404

        owner[0] = "owner-a"
        replay = client.post(
            "/api/v1/ppt/files/decompose/jobs",
            json={"file_paths": [str(tmp_path / "input.pdf")], "language": "ko"},
            headers={"Idempotency-Key": "upload-1"},
        )
        assert replay.status_code == 202
        assert replay.json()["job_id"] == body["job_id"]

        conflict = client.post(
            "/api/v1/ppt/files/decompose/jobs",
            json={"file_paths": [str(tmp_path / "other.pdf")], "language": "ko"},
            headers={"Idempotency-Key": "upload-1"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT"

        terminal_delete = client.delete(
            f"/api/v1/ppt/files/decompose/jobs/{body['job_id']}"
        )
        assert terminal_delete.status_code == 200
        assert terminal_delete.json()["status"] == "succeeded"


def test_job_api_safe_failure_does_not_expose_internal_converter_details(
    monkeypatch, tmp_path
):
    class FailingLoader:
        def __init__(self, file_paths, presentation_language=None):
            self.documents = []

        async def load_documents(self, temp_dir):
            raise DocumentConversionError(
                "C:\\private\\magick.exe token=secret-token",
                code="DOCUMENT_CONVERSION_FAILED",
                filename="photo.jpg",
            )

    service = DecomposeJobService(result_ttl_seconds=60)
    monkeypatch.setattr(job_module, "DocumentsLoader", FailingLoader)
    monkeypatch.setattr(
        job_module,
        "TEMP_FILE_SERVICE",
        TempFileService(str(tmp_path / "failed-api-jobs")),
    )
    monkeypatch.setattr(endpoint_module, "DECOMPOSE_JOB_SERVICE", service)
    monkeypatch.setattr(endpoint_module, "validate_document_paths", lambda value: value)
    monkeypatch.setattr(endpoint_module, "get_current_owner_id", lambda: "owner-a")

    with TestClient(_app()) as client:
        created = client.post(
            "/api/v1/ppt/files/decompose/jobs",
            json={"file_paths": [str(tmp_path / "photo.jpg")]},
        )
        failed = _wait_for_status(client, created.json()["job_id"], "failed")
        body = failed.json()
        assert body["error"] == {
            "code": "DOCUMENT_CONVERSION_FAILED",
            "message": "Unable to convert 'photo.jpg'.",
            "filename": "photo.jpg",
            "request_id": body["job_id"],
            "retryable": False,
        }
        assert "private" not in failed.text
        assert "secret-token" not in failed.text


def test_job_api_capacity_and_cooperative_cancel_status_codes(monkeypatch):
    now = datetime.now(timezone.utc)
    cancelling_snapshot = DecomposeJobSnapshot(
        job_id=uuid.uuid4(),
        status="cancelling",
        progress=DecomposeJobProgress(
            completed_files=0,
            total_files=1,
            current_file="large.pptx",
        ),
        created_at=now,
        updated_at=now,
    )

    class CapacityService:
        async def create_job(self, **kwargs):
            raise DecomposeJobCapacityError

        async def cancel_job(self, **kwargs):
            return cancelling_snapshot, True

    monkeypatch.setattr(endpoint_module, "DECOMPOSE_JOB_SERVICE", CapacityService())
    monkeypatch.setattr(endpoint_module, "validate_document_paths", lambda value: value)
    monkeypatch.setattr(endpoint_module, "get_current_owner_id", lambda: "owner-a")

    with TestClient(_app()) as client:
        capacity = client.post(
            "/api/v1/ppt/files/decompose/jobs",
            json={"file_paths": ["large.pptx"]},
        )
        assert capacity.status_code == 429
        assert capacity.json()["detail"] == {
            "code": "DOCUMENT_DECOMPOSE_CAPACITY_EXCEEDED",
            "message": "Too many document processing jobs are pending. Try again shortly.",
            "retryable": True,
        }

        cancellation = client.delete(
            f"/api/v1/ppt/files/decompose/jobs/{cancelling_snapshot.job_id}"
        )
        assert cancellation.status_code == 202
        assert cancellation.json()["status"] == "cancelling"
        assert cancellation.json()["progress"]["current_file"] == "large.pptx"
