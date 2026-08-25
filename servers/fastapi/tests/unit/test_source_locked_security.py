from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from threading import Event, Thread
import uuid

from fastapi import HTTPException, UploadFile
from pptx import Presentation
import pytest

from api.v1.ppt.endpoints import source_locked as endpoint
from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import (
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
)
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.generation_store import DurableSourceLockedGenerationStore
from services.source_locked.job_runner import generate_source_locked_result
from services.source_locked.job_store import DurableSourceLockedJobStore


def _source(label: str) -> bytes:
    presentation = Presentation()
    presentation.core_properties.title = label
    presentation.slides.add_slide(presentation.slide_layouts[6])
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _contract():
    return admit_source_locked_v1(
        ["source.pptx"],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )


def _analyze(data: bytes, key: str):
    digest = idempotency_payload_digest(_contract(), sha256_digest(data))
    return asyncio.run(
        endpoint.analyze_source_locked_v1(
            files=[UploadFile(filename="source.pptx", file=BytesIO(data))],
            generation_mode=GenerationMode.SOURCE_LOCKED,
            page_count_policy=PageCountPolicy.SOURCE,
            preserve_all_image_occurrences=True,
            idempotency_key=key,
            expected_payload_digest=digest,
        )
    )


def _create_job(store: DurableSourceLockedJobStore, *, owner_id: str, key: str, source: bytes):
    contract = _contract()
    digest = idempotency_payload_digest(contract, sha256_digest(source))
    snapshot, created = store.create(
        owner_id=owner_id,
        idempotency_key=key,
        idempotency_payload_digest=digest,
        admission_contract=contract,
        source_bytes=source,
    )
    assert created is True
    return snapshot, contract, digest


def test_analyze_requires_owner_and_namespaces_shared_seals(monkeypatch) -> None:
    source_a = _source("owner-a")
    source_b = _source("owner-b")
    key = f"owner-scoped-analysis-{uuid.uuid4().hex}"

    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: uuid.UUID(int=1))
    first = _analyze(source_a, key)
    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: uuid.UUID(int=2))
    second = _analyze(source_b, key)
    assert first.analysis_seal.idempotency_key == second.analysis_seal.idempotency_key == key
    assert first.analysis_seal.source_snapshot_sha256 != second.analysis_seal.source_snapshot_sha256

    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: uuid.UUID(int=1))
    with pytest.raises(HTTPException) as conflict:
        _analyze(source_b, key)
    assert conflict.value.status_code == 409

    monkeypatch.setattr(endpoint, "get_current_owner_id", lambda: None)
    with pytest.raises(HTTPException) as unauthenticated:
        _analyze(source_a, f"unauthenticated-{key}")
    assert unauthenticated.value.status_code == 401


def test_publication_transaction_wins_before_stale_recovery(tmp_path) -> None:
    owner_id = "owner-a"
    source = _source("publication-wins")
    root = tmp_path / "runtime"
    job_store = DurableSourceLockedJobStore(root)
    generation_store = DurableSourceLockedGenerationStore(root)
    job, contract, digest = _create_job(
        job_store,
        owner_id=owner_id,
        key="publication-wins",
        source=source,
    )
    prepared_store = DurableSourceLockedGenerationStore(tmp_path / "prepared")
    prepared = generate_source_locked_result(
        owner_id=owner_id,
        admission_contract=contract,
        source_bytes=source,
        idempotency_key="publication-wins",
        expected_payload_digest=digest,
        generation_store=prepared_store,
    )
    assert prepared is not None
    token = job_store.claim(owner_id=owner_id, job_id=job.job_id)
    assert token is not None
    for stage in AnalysisStage:
        assert job_store.advance(
            owner_id=owner_id,
            job_id=job.job_id,
            runner_token=token,
            completed_stage=stage,
        )
    assert job_store.prepare_commit(owner_id=owner_id, job_id=job.job_id, runner_token=token)
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    with job_store._connect() as connection:
        connection.execute(
            "UPDATE generation_jobs_v1 SET lease_expires_at = ? WHERE job_id = ?",
            (expired.isoformat(), job.job_id),
        )

    publisher_entered = Event()
    release_publisher = Event()
    recovery_attempted = Event()
    recovery_done = Event()
    errors: list[BaseException] = []
    recovered_jobs: list[tuple] = []

    def publish() -> None:
        try:
            job_store.publish_and_succeed(
                owner_id=owner_id,
                job_id=job.job_id,
                runner_token=token,
                publisher=lambda: _blocked_persist(),
            )
        except BaseException as exc:  # pragma: no cover - asserted below.
            errors.append(exc)

    def _blocked_persist():
        publisher_entered.set()
        assert release_publisher.wait(5)
        return generation_store.persist(
            owner_id=owner_id,
            response=prepared,
            artifact_bytes=source,
        )

    def recover() -> None:
        recovery_attempted.set()
        try:
            recovered_jobs.append(
                job_store.recover_startup_jobs(
                    now=datetime.now(timezone.utc) + timedelta(minutes=2),
                    limit=10,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below.
            errors.append(exc)
        finally:
            recovery_done.set()

    publisher_thread = Thread(target=publish)
    publisher_thread.start()
    assert publisher_entered.wait(5)
    recovery_thread = Thread(target=recover)
    recovery_thread.start()
    assert recovery_attempted.wait(5)
    assert recovery_done.wait(0.1) is False
    release_publisher.set()
    publisher_thread.join(5)
    recovery_thread.join(5)

    assert errors == []
    assert recovered_jobs == [()]
    terminal = job_store.get(owner_id=owner_id, job_id=job.job_id)
    assert terminal is not None and terminal.status.value == "succeeded"
    assert terminal.result is not None


def test_stale_recovery_wins_before_publication_and_fences_old_runner(tmp_path) -> None:
    owner_id = "owner-a"
    source = _source("recovery-wins")
    job_store = DurableSourceLockedJobStore(tmp_path / "runtime")
    job, _, _ = _create_job(
        job_store,
        owner_id=owner_id,
        key="recovery-wins",
        source=source,
    )
    token = job_store.claim(owner_id=owner_id, job_id=job.job_id)
    assert token is not None
    assert job_store.prepare_commit(owner_id=owner_id, job_id=job.job_id, runner_token=token)
    with job_store._connect() as connection:
        connection.execute(
            "UPDATE generation_jobs_v1 SET lease_expires_at = ? WHERE job_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), job.job_id),
        )
    recovered = job_store.recover_startup_jobs(
        now=datetime.now(timezone.utc),
        limit=10,
    )
    assert [item.job_id for item in recovered] == [job.job_id]
    publisher_called = False

    def forbidden_publisher():
        nonlocal publisher_called
        publisher_called = True
        raise AssertionError("fenced runner must not publish")

    assert job_store.publish_and_succeed(
        owner_id=owner_id,
        job_id=job.job_id,
        runner_token=token,
        publisher=forbidden_publisher,
    ) is None
    assert publisher_called is False
