from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
import sqlite3

from pptx import Presentation
import pytest

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import GenerationMode, PageCountPolicy
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.generation_store import DurableSourceLockedGenerationStore
from services.source_locked.job_store import DurableSourceLockedJobStore
from services.source_locked.lifecycle import ArtifactLifecyclePolicy
from services.source_locked.maintenance import run_source_locked_maintenance_once


def _source() -> bytes:
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _create_job(store: DurableSourceLockedJobStore, *, owner_id: str, key: str):
    source = _source()
    contract = admit_source_locked_v1(
        ["source.pptx"],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )
    digest = idempotency_payload_digest(contract, sha256_digest(source))
    snapshot, created = store.create(
        owner_id=owner_id,
        idempotency_key=key,
        idempotency_payload_digest=digest,
        admission_contract=contract,
        source_bytes=source,
    )
    assert created is True
    return snapshot


def test_startup_recovery_fences_stale_runner_and_preserves_cancel_barrier(tmp_path) -> None:
    store = DurableSourceLockedJobStore(tmp_path / "source-locked")
    queued = _create_job(store, owner_id="owner-a", key="queued")
    stale = _create_job(store, owner_id="owner-b", key="stale")
    stale_token = store.claim(owner_id="owner-b", job_id=stale.job_id)
    assert stale_token is not None
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE generation_jobs_v1
            SET lease_expires_at = ?, commit_started = 1
            WHERE job_id = ?
            """,
            (expired.isoformat(), stale.job_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="source_locked_job_authority_immutable"):
            connection.execute(
                "UPDATE generation_jobs_v1 SET owner_id = 'guessed-owner' WHERE job_id = ?",
                (stale.job_id,),
            )

    recoverable = store.recover_startup_jobs(now=datetime.now(timezone.utc), limit=10)
    assert {(job.owner_id, job.job_id) for job in recoverable} == {
        ("owner-a", queued.job_id),
        ("owner-b", stale.job_id),
    }
    assert store.advance(
        owner_id="owner-b",
        job_id=stale.job_id,
        runner_token=stale_token,
        completed_stage=next(iter(stale.progress_stages)).stage,
    ) is False
    recovered = store.get(owner_id="owner-b", job_id=stale.job_id)
    assert recovered is not None and recovered.status.value == "queued"
    cancelled, accepted = store.cancel(owner_id="owner-b", job_id=stale.job_id)
    assert cancelled is not None and cancelled.status.value == "queued"
    assert accepted is False


def test_idle_maintenance_protects_legacy_ownerless_reference_and_removes_true_orphan(
    tmp_path,
) -> None:
    root = tmp_path / "source-locked"
    policy = ArtifactLifecyclePolicy(
        retention_seconds=60,
        max_artifact_bytes=1024,
        max_owner_bytes=2048,
        max_total_bytes=4096,
        orphan_grace_seconds=1,
    )
    generation_store = DurableSourceLockedGenerationStore(root, lifecycle_policy=policy)
    job_store = DurableSourceLockedJobStore(root)
    legacy = generation_store.artifact_root / ("sla_" + ("a" * 64) + ".pptx")
    orphan = generation_store.artifact_root / ("sla_" + ("b" * 64) + ".pptx")
    legacy.write_bytes(b"legacy ownerless artifact")
    orphan.write_bytes(b"true orphan")
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).timestamp()
    os.utime(legacy, (old, old))
    os.utime(orphan, (old, old))
    with generation_store._connect() as connection:
        connection.execute(
            """
            CREATE TABLE generation_records_v1 (
                generation_record_id TEXT PRIMARY KEY,
                artifact_relative_path TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO generation_records_v1 VALUES (?, ?)",
            ("legacy-ownerless", legacy.relative_to(root).as_posix()),
        )

    result = asyncio.run(
        run_source_locked_maintenance_once(
            job_store=job_store,
            generation_store=generation_store,
            recovery_limit=1,
        )
    )

    assert legacy.is_file()
    assert not orphan.exists()
    assert result.artifact_lifecycle.orphan_artifacts_removed == 1
    assert generation_store.lookup(
        owner_id="unrelated-owner",
        idempotency_key="legacy-ownerless",
        idempotency_payload_digest="sha256:" + ("0" * 64),
    ) is None
