"""Projection failures retain safe page context at the conversion boundary."""

from types import SimpleNamespace
import asyncio
import hashlib
import json
import sys
import time
import uuid

import pytest

from services.public_export_failure_classification import classified_public_export_error
from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services import isolated_worker_process
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireResult
from workers import public_svg_whole_page_convert as converter
from workers import public_worker_runtime
from workers.public_worker_store import PublicWorkerExecutionError
from workers.public_svg_projection_error import (
    SvgExportProjectionError, projection_failure_detail, projection_page,
)


@pytest.mark.parametrize("inner", [
    ValueError("svg_source_export_table_projection_invalid"),
    TypeError("private source text"),
    KeyError("private source text"),
    IndexError("private source text"),
])
def test_converter_attaches_the_failing_page_and_sanitized_reason(monkeypatch, inner):
    calls = []

    def project(source, _receipt, page):
        calls.append(source)
        if len(calls) == 2:
            raise inner
        return page, []

    monkeypatch.setattr(converter, "project_source_table_end_alignment", project)
    with pytest.raises(SvgExportProjectionError) as raised:
        converter.convert_whole_page_pptx(
            ["page-1", "page-2"], None, [], None,
            receipts=[{}, {}], native_objects=[SimpleNamespace(), SimpleNamespace()],
            source_originals=["first", "second"], font_embedding=False,
        )
    error = raised.value
    assert error.page_index == 1
    assert error.code == "svg_source_export_projection_invalid"
    assert error.reason_code == (
        "svg_source_export_table_projection_invalid" if isinstance(inner, ValueError) else type(inner).__name__
    )
    assert error.__cause__ is inner
    assert "private" not in str(error.detail)
    assert classified_public_export_error(error, strategy="svg").retryable is False


def test_existing_structured_page_error_survives_outer_projection_context():
    with pytest.raises(SvgExportProjectionError) as raised:
        with projection_page(0), projection_page(2):
            raise ValueError("svg_source_export_fragment_duplicate")
    assert raised.value.page_index == 2


def test_deck_level_projection_error_does_not_invent_a_page():
    error = SvgExportProjectionError(None, ValueError("private source text"))
    assert error.detail == {"page_index": None, "reason_code": "ValueError"}


def _job():
    operation_id = uuid.uuid4()
    artifact = ArtifactRef(
        artifact_id=uuid.uuid4(), owner_id=uuid.uuid4(), tenant_id="test-tenant",
        kind="export-bundle", sha256=hashlib.sha256(b"input").hexdigest(), size_bytes=5,
        media_type="application/json", storage_key="tenant/owner/input",
        created_by_operation_id=operation_id, retention_class="staged",
    )
    return IsolatedWorkerJob(
        role="export", operation_id=operation_id, attempt_token="attempt-token-1234567890",
        fencing_token=1, input_digest=artifact.sha256, input_artifact=artifact,
        capability="pending", deadline_epoch_seconds=int(time.time()) + 300,
    )


@pytest.mark.parametrize("error", [
    SvgExportProjectionError(2, ValueError("svg_source_export_fragment_duplicate")),
    SvgExportProjectionError(2, type("_Private_Error", (ValueError,), {})("private source")),
    SvgExportProjectionError(2, type("Error" * 30, (TypeError,), {})("private source")),
    SvgExportProjectionError(2, type("lowercase_error", (TypeError,), {})("private source")),
    ValueError("private authored source"),
    PublicWorkerExecutionError("public_export_hybrid_not_editable"),
])
def test_projection_failure_crosses_real_worker_wire_and_authored_evidence_stays_empty(
    tmp_path, monkeypatch, error,
):
    async def scenario():
        job = _job()
        results = []
        monkeypatch.setattr(public_worker_runtime.os, "environ", {
            "PUBLIC_WORKER_SESSIONLESS": "1", "PUBLIC_WORKER_ROLE": "export",
            "PUBLIC_WORKER_LOCAL_DEVELOPMENT": "1",
            "PUBLIC_WORKER_OS_ISOLATION": public_worker_runtime.WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION,
            "PUBLIC_WORKER_INPUT_STORE_ROOT": str(tmp_path),
            "PUBLIC_WORKER_OUTPUT_STORE_ROOT": str(tmp_path),
        })
        monkeypatch.setattr(public_worker_runtime, "_read_job", lambda: job)
        monkeypatch.setattr(public_worker_runtime, "_read_signing_key", lambda: b"k" * 32)
        monkeypatch.setattr(public_worker_runtime, "verify_and_consume_worker_capability", lambda *_a, **_k: None)
        monkeypatch.setattr(public_worker_runtime, "LocalContentAddressedStore", lambda *_a, **_k: object())
        monkeypatch.setattr(public_worker_runtime, "_write_result", results.append)

        async def execute(_job, _store):
            raise error

        assert await public_worker_runtime.run_worker("export", execute) == 1
        wire, = results
        expected = {"svgProjectionFailure": error.detail} if isinstance(error, SvgExportProjectionError) else {}
        assert wire["executionEvidence"] == expected
        assert "private" not in json.dumps(wire)

        class Dispatcher:
            async def execute(self, **_kwargs):
                return TrustedWorkerWireResult(
                    stdout=json.dumps(wire).encode(), exit_code=1, resource_metrics={"wallMillis": 12},
                )

        monkeypatch.setattr(isolated_worker_process, "worker_environment", lambda *_a, **_k: {})
        with pytest.raises(isolated_worker_process.IsolatedWorkerProcessError) as raised:
            await isolated_worker_process.execute_isolated_worker_job(
                job, presentation_id=uuid.uuid4(), command=[sys.executable], dispatcher=Dispatcher(),
            )
        received = raised.value
        assert received.resource_metrics == {"wallMillis": 12, **expected}
        classified = classified_public_export_error(
            received, strategy="svg", detail=received.resource_metrics,
        )
        assert classified.retryable is False
        if expected:
            assert classified.code == "svg_source_export_projection_invalid"
            assert classified.detail["page_index"] == 2
            assert classified.detail["reason_code"] == error.reason_code
            assert projection_failure_detail(error.detail) == error.detail

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [
    {"page_index": -1, "reason_code": "ValueError"},
    {"page_index": True, "reason_code": "ValueError"},
    {"page_index": 2**40, "reason_code": "ValueError"},
    {"page_index": 1, "reason_code": "private source"},
    {"page_index": 1, "reason_code": "svg_" + "a" * 200},
    {"page_index": 1, "reason_code": "ValueError", "source": "private"},
])
def test_projection_wire_detail_is_bounded_and_rejects_source_fields(value):
    with pytest.raises(ValueError, match="svg_export_projection_failure_detail_invalid"):
        projection_failure_detail(value)
    classified = classified_public_export_error(
        isolated_worker_process.IsolatedWorkerProcessError("svg_source_export_projection_invalid"),
        strategy="svg", detail={"svgProjectionFailure": value},
    )
    assert classified.retryable is False
    assert "private" not in str(classified.detail)
