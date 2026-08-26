from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import BackgroundTasks, HTTPException, Response, UploadFile
from pptx import Presentation
import pytest

from api.v1.ppt.endpoints import source_locked as endpoint
from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import GenerationMode, PageCountPolicy
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.generation_store import DurableSourceLockedGenerationStore
from services.source_locked.job_store import DurableSourceLockedJobStore


OWNER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(autouse=True)
def _authenticated_owner(monkeypatch):
    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: OWNER_A)


def _pptx(label: str = "source locked") -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    textbox = slide.shapes.add_textbox(0, 0, presentation.slide_width, presentation.slide_height)
    textbox.text = label
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _contract(filename: str = "source.pptx"):
    return admit_source_locked_v1(
        [filename],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )


def _upload(data: bytes, filename: str = "source.pptx") -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(data))


def _generate(data: bytes, key: str):
    contract = _contract()
    digest = idempotency_payload_digest(contract, sha256_digest(data))
    return asyncio.run(
        endpoint.generate_source_locked_v1(
            files=[_upload(data)],
            generation_mode=GenerationMode.SOURCE_LOCKED,
            page_count_policy=PageCountPolicy.SOURCE,
            preserve_all_image_occurrences=True,
            idempotency_key=key,
            expected_payload_digest=digest,
        )
    )


def _create_job(data: bytes, key: str, background_tasks: BackgroundTasks):
    contract = _contract()
    digest = idempotency_payload_digest(contract, sha256_digest(data))
    return asyncio.run(
        endpoint.create_source_locked_job_v1(
            background_tasks=background_tasks,
            files=[_upload(data)],
            generation_mode=GenerationMode.SOURCE_LOCKED,
            page_count_policy=PageCountPolicy.SOURCE,
            preserve_all_image_occurrences=True,
            idempotency_key=key,
            expected_payload_digest=digest,
        )
    )


def test_generate_persists_terminal_artifact_and_durable_replay(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "app-data" / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    source = _pptx()

    first = _generate(source, "generation-api-1")

    assert [item.status.value for item in first.progress_stages] == [
        "completed",
        "completed",
        "completed",
    ]
    assert first.fallback_allowed is False
    assert first.idempotent_replay is False
    assert first.artifact.sha256 == sha256_digest(source)
    assert first.restoration_verification.byte_exact_source_package is True
    assert first.restoration_verification.missing_content_count == 0
    assert first.artifact.download_url.endswith(first.artifact.artifact_id)

    artifact_path, _ = store.artifact_path(first.artifact.artifact_id, owner_id=str(OWNER_A))
    assert artifact_path.read_bytes() == source
    assert artifact_path.is_relative_to(store.artifact_root)

    # A new store instance proves the record and response survive process-local state.
    reopened = DurableSourceLockedGenerationStore(store.root)
    monkeypatch.setattr(endpoint, "_generation_store", lambda: reopened)
    monkeypatch.setattr(
        endpoint,
        "_run_restoration",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("replay restored again")),
    )
    replay = _generate(source, "generation-api-1")
    assert replay.idempotent_replay is True
    assert replay.generation_record_id == first.generation_record_id
    assert replay.artifact == first.artifact


def test_generate_same_key_with_different_digest_fails_closed(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    _generate(_pptx("first"), "generation-api-conflict")

    with pytest.raises(HTTPException) as exc:
        _generate(_pptx("different"), "generation-api-conflict")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert exc.value.detail["fallback_allowed"] is False


def test_generation_digest_mismatch_never_calls_restoration_or_store(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    monkeypatch.setattr(
        endpoint,
        "_run_restoration",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("restoration called")),
    )
    source = _pptx()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint.generate_source_locked_v1(
                files=[_upload(source)],
                generation_mode=GenerationMode.SOURCE_LOCKED,
                page_count_policy=PageCountPolicy.SOURCE,
                preserve_all_image_occurrences=True,
                idempotency_key="generation-api-mismatch",
                expected_payload_digest="sha256:" + "0" * 64,
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH"
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_records_v2").fetchone()[0] == 0


def test_download_resolves_only_recorded_safe_artifact_ids(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    generated = _generate(_pptx(), "generation-api-download")

    response = asyncio.run(endpoint.download_source_locked_artifact_v1(generated.artifact.artifact_id))
    assert Path(response.path).resolve().is_relative_to(store.artifact_root)
    assert response.filename == generated.artifact.filename
    assert response.headers["etag"] == f'"{generated.artifact.sha256}"'

    with pytest.raises(HTTPException) as invalid:
        asyncio.run(endpoint.download_source_locked_artifact_v1("../../outside.pptx"))
    assert invalid.value.status_code == 404
    assert invalid.value.detail["code"] == "ARTIFACT_NOT_FOUND"


def test_store_detects_artifact_tampering_on_replay(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    source = _pptx()
    generated = _generate(source, "generation-api-integrity")
    path, _ = store.artifact_path(generated.artifact.artifact_id, owner_id=str(OWNER_A))
    path.write_bytes(b"tampered")

    contract = _contract()
    digest = idempotency_payload_digest(contract, sha256_digest(source))
    with pytest.raises(SourceLockedError) as exc:
        store.lookup(
            owner_id=str(OWNER_A),
            idempotency_key="generation-api-integrity",
            idempotency_payload_digest=digest,
        )
    assert exc.value.detail.code == "ARTIFACT_INTEGRITY_FAILURE"


def test_missing_content_readback_never_persists_success(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    source = _pptx()
    source_digest = sha256_digest(source)
    monkeypatch.setattr(
        endpoint,
        "_run_restoration",
        lambda **_kwargs: SimpleNamespace(
            artifact_bytes=source,
            artifact_media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            verification=SimpleNamespace(
                source_type="pptx",
                source_snapshot_sha256=source_digest,
                output_package_sha256=source_digest,
                page_count=1,
                slide_count=1,
                direct_occurrence_count=0,
                rendered_page_count=0,
                authoritative_image_digests=(),
                missing_content_count=1,
                byte_exact_source_package=True,
                fallback_allowed=False,
            ),
        ),
    )

    with pytest.raises(HTTPException) as exc:
        _generate(source, "generation-api-missing")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "RESTORATION_READBACK_INVALID"
    assert exc.value.detail["fallback_allowed"] is False
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_records_v2").fetchone()[0] == 0
    assert list(store.artifact_root.glob("*.pptx")) == []


def test_async_job_survives_store_reopen_and_completes_with_durable_replay(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "source_locked" / "v1"
    job_store = DurableSourceLockedJobStore(root)
    generation_store = DurableSourceLockedGenerationStore(root)
    monkeypatch.setattr(endpoint, "_job_store", lambda: job_store)
    monkeypatch.setattr(endpoint, "_generation_store", lambda: generation_store)
    source = _pptx("async durable")
    tasks = BackgroundTasks()

    queued = _create_job(source, "async-durable-1", tasks)

    assert queued.status.value == "queued"
    assert queued.idempotent_replay is False
    with job_store._connect() as connection:
        relative_input = connection.execute(
            "SELECT source_relative_path FROM generation_jobs_v1 WHERE job_id = ?",
            (queued.job_id,),
        ).fetchone()[0]
    input_path = job_store.root / relative_input
    assert input_path.is_file()
    reopened = DurableSourceLockedJobStore(root)
    monkeypatch.setattr(endpoint, "_job_store", lambda: reopened)
    replay_tasks = BackgroundTasks()
    replay = _create_job(source, "async-durable-1", replay_tasks)
    assert replay.job_id == queued.job_id
    assert replay.idempotent_replay is True

    asyncio.run(tasks())
    completed = reopened.get(owner_id=str(OWNER_A), job_id=queued.job_id)
    assert completed is not None
    assert completed.status.value == "succeeded"
    assert completed.result is not None
    assert completed.result.artifact.sha256 == sha256_digest(source)
    assert [stage.status.value for stage in completed.progress_stages] == [
        "completed",
        "completed",
        "completed",
    ]
    assert not input_path.exists()


def test_async_job_owner_scope_and_accepted_cancel_never_succeed(tmp_path, monkeypatch):
    root = tmp_path / "source_locked" / "v1"
    job_store = DurableSourceLockedJobStore(root)
    generation_store = DurableSourceLockedGenerationStore(root)
    monkeypatch.setattr(endpoint, "_job_store", lambda: job_store)
    monkeypatch.setattr(endpoint, "_generation_store", lambda: generation_store)
    source = _pptx("cancelled")
    owner_a_tasks = BackgroundTasks()
    owner_a_job = _create_job(source, "owner-scoped-key", owner_a_tasks)

    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: OWNER_B)
    with pytest.raises(HTTPException) as hidden_get:
        asyncio.run(
            endpoint.get_source_locked_job_v1(
                owner_a_job.job_id,
                BackgroundTasks(),
            )
        )
    assert hidden_get.value.status_code == 404
    with pytest.raises(HTTPException) as hidden_cancel:
        asyncio.run(
            endpoint.cancel_source_locked_job_v1(
                owner_a_job.job_id,
                Response(),
            )
        )
    assert hidden_cancel.value.status_code == 404

    owner_b_job = _create_job(source, "owner-scoped-key", BackgroundTasks())
    assert owner_b_job.job_id != owner_a_job.job_id
    owner_b_list = asyncio.run(
        endpoint.list_source_locked_jobs_v1(BackgroundTasks(), limit=50)
    )
    assert [job.job_id for job in owner_b_list] == [owner_b_job.job_id]

    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: OWNER_A)
    owner_a_list = asyncio.run(
        endpoint.list_source_locked_jobs_v1(BackgroundTasks(), limit=50)
    )
    assert [job.job_id for job in owner_a_list] == [owner_a_job.job_id]
    cancelled = asyncio.run(
        endpoint.cancel_source_locked_job_v1(
            owner_a_job.job_id,
            Response(),
        )
    )
    assert cancelled.status.value == "cancelled"
    assert cancelled.cancel_requested is True

    asyncio.run(owner_a_tasks())
    terminal = job_store.get(owner_id=str(OWNER_A), job_id=owner_a_job.job_id)
    assert terminal is not None
    assert terminal.status.value == "cancelled"
    assert terminal.result is None
    assert not list(job_store.input_root.glob(f"{owner_a_job.job_id}.*"))


def test_stale_running_job_is_fenced_requeued_and_rescheduled(tmp_path, monkeypatch):
    root = tmp_path / "source_locked" / "v1"
    job_store = DurableSourceLockedJobStore(root)
    generation_store = DurableSourceLockedGenerationStore(root)
    monkeypatch.setattr(endpoint, "_job_store", lambda: job_store)
    monkeypatch.setattr(endpoint, "_generation_store", lambda: generation_store)
    queued = _create_job(_pptx("stale recovery"), "stale-job", BackgroundTasks())
    stale_token = job_store.claim(owner_id=str(OWNER_A), job_id=queued.job_id)
    assert stale_token is not None

    recovered, did_recover = job_store.recover_stale(
        owner_id=str(OWNER_A),
        job_id=queued.job_id,
        now=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    assert did_recover is True
    assert recovered is not None and recovered.status.value == "queued"
    assert job_store.advance(
        owner_id=str(OWNER_A),
        job_id=queued.job_id,
        runner_token=stale_token,
        completed_stage=endpoint.AnalysisStage.SOURCE_ANALYSIS,
    ) is False

    resumed_tasks = BackgroundTasks()
    visible = asyncio.run(endpoint.get_source_locked_job_v1(queued.job_id, resumed_tasks))
    assert visible.status.value == "queued"
    asyncio.run(resumed_tasks())
    terminal = job_store.get(owner_id=str(OWNER_A), job_id=queued.job_id)
    assert terminal is not None and terminal.status.value == "succeeded"
