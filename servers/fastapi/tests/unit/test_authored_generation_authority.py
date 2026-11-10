import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from services import authored_checkpoint_service as checkpoints
from services.authored_checkpoint_state import provider_attempt_count_in_epoch
from services.authored_provider_attempt_ledger import (
    reserve_provider_overload_retry,
    take_provider_attempt_reservations,
)
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_generation_timing_receipt import (
    GENERATION_TIMING_STAGES,
    GenerationTimingRecorder,
    load_generation_attempt_timing_receipts,
)
from tests.support.authored_generation_attempt_timing_v1 import (
    create_generation_attempt_timing_receipt,
)
from services.provider_usage_instrumentation import ProviderUsageScope
from services.generation_core.authoring.author_slide import authored_provider_semantic_slot


def test_attempt_receipt_keeps_non_null_stages_and_operational_causes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    recorder = GenerationTimingRecorder()
    recorder.record_stage_ms("author", 9)
    # ``snapshot_stage_timings_ms`` only ever reports stages actually recorded
    # (it has never zero-filled; see services/authored_generation_timing_receipt.py
    # GenerationTimingRecorder, unchanged since its introduction in 4e7221520).
    # The one production caller (services/authored_presentation_generation.py,
    # ``stage_timings_ms = {stage: 0 for stage in _PERFORMANCE_STAGE_NAMES}``)
    # owns zero-filling itself before merging in recorded values; this test
    # does the same to build a receipt-ready, fully-keyed stage map.
    stages = {stage: 0 for stage in GENERATION_TIMING_STAGES}
    stages.update(recorder.snapshot_stage_timings_ms())

    assert all(stage in stages for stage in GENERATION_TIMING_STAGES)
    assert stages["outline"] == 0
    assert stages["repair"] == 0
    assert stages["checkpoint"] == 0

    job_id = uuid.uuid4()
    receipt = create_generation_attempt_timing_receipt(
        job_id=job_id,
        attempt_id="private-attempt-token",
        attempt_number=3,
        revision=0,
        outcome="failed",
        failure_stage="author",
        stage_timings_ms=stages,
        total_ms=10,
        causes={
            "queueReason": "workspace_retry",
            "stageReason": "public_provider_retry_limit_exceeded",
            "retryReason": "terminal_failure",
        },
    )

    assert dict(receipt.causes) == {
        "queueReason": "workspace_retry",
        "stageReason": "public_provider_retry_limit_exceeded",
        "retryReason": "terminal_failure",
    }
    assert load_generation_attempt_timing_receipts(job_id) == (receipt,)
    assert "private-attempt-token" not in receipt.to_dict()["attemptId"]


def test_provider_attempt_count_rejects_unbounded_checkpoint_value() -> None:
    error = AuthoredStageError(
        slide_index=0,
        stage="author",
        code="authored_slide_author_failed",
        provider_attempt_count=99,
    )
    assert "provider_attempt_count" not in error.as_safe_dict()


def test_a_later_operation_attempt_gives_a_failed_slide_its_calls_back(
    tmp_path, monkeypatch
) -> None:
    """The ordinal pair fences one attempt; it is not a lifetime cap.

    A deck that fails on one slide is retried to redo that slide.  When the
    slide's two ordinals stayed spent across attempts the retry could not call
    the provider at all and failed the whole deck in under a second, so the
    counter restarts when the fencing token advances and what the slide already
    cost is kept in ``provider_attempt_total``.
    """

    async def scenario() -> dict[str, int]:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'provider-attempt-epoch.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AsyncPresentationGenerationTaskModel.__table__,
                    PresentationGenerationJob.__table__,
                    AuthoredSlideCheckpoint.__table__,
                ],
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(checkpoints, "async_session_maker", maker)
        monkeypatch.setenv(
            "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoint-artifacts")
        )
        status = AsyncPresentationGenerationTaskModel(status="running")
        job = PresentationGenerationJob(
            task_id=status.id,
            presentation_id=uuid.uuid4(),
            request_sha256="b" * 64,
            request_payload={"template": "authored"},
            state="running",
            attempt_number=1,
            attempt_token="attempt-a",
        )
        async with maker() as session:
            session.add_all((status, job))
            await session.commit()
        await checkpoints.initialize_checkpoints(
            job_id=job.id, attempt_token="attempt-a", roles=["CLOSING"]
        )
        await checkpoints.start_stage(
            job_id=job.id, slide_index=0, stage="author", attempt_token="attempt-a"
        )
        for ordinal in (1, 2):
            await checkpoints.reserve_provider_attempt(
                job_id=job.id,
                slide_index=0,
                attempt_token="attempt-a",
                ordinal=ordinal,
                fencing_token=1,
            )
        await reserve_provider_overload_retry(
            take_provider_attempt_reservations(),
            physical_ordinal=2,
        )
        exhausted = await checkpoints.get_checkpoint(job.id, 0)
        spent_in_first = provider_attempt_count_in_epoch(exhausted.error, 1)
        physical_in_first = exhausted.error["provider_physical_attempts"]

        # The operation is retried: a new lease, a new fencing token, the same
        # durable checkpoint carrying the slides that already succeeded.
        async with maker() as session:
            persisted = await session.get(PresentationGenerationJob, job.id)
            persisted.attempt_token = "attempt-b"
            persisted.attempt_number = 2
            session.add(persisted)
            await session.commit()
        carried = await checkpoints.get_checkpoint(job.id, 0)
        remaining = provider_attempt_count_in_epoch(carried.error, 2)

        await checkpoints.start_stage(
            job_id=job.id, slide_index=0, stage="author", attempt_token="attempt-b"
        )
        await checkpoints.reserve_provider_attempt(
            job_id=job.id,
            slide_index=0,
            attempt_token="attempt-b",
            ordinal=1,
            fencing_token=2,
        )
        retried = await checkpoints.get_checkpoint(job.id, 0)
        await engine.dispose()
        return {
            "spent_in_first": spent_in_first,
            "physical_in_first": physical_in_first,
            "remaining": remaining,
            "in_second": provider_attempt_count_in_epoch(retried.error, 2),
            "total": int(retried.error["provider_attempt_total"]),
            "physical_total": int(retried.error["provider_physical_attempt_total"]),
        }

    assert asyncio.run(scenario()) == {
        "spent_in_first": 2,
        "physical_in_first": {"1": 1, "2": 2},
        "remaining": 0,
        "in_second": 1,
        "total": 2,
        "physical_total": 3,
    }


def test_provider_attempt_reservation_survives_crash_and_fences_stale_worker(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> tuple[int, int]:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'provider-attempt-crash.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AsyncPresentationGenerationTaskModel.__table__,
                    PresentationGenerationJob.__table__,
                    AuthoredSlideCheckpoint.__table__,
                ],
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(checkpoints, "async_session_maker", maker)
        monkeypatch.setenv(
            "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoint-artifacts")
        )
        status = AsyncPresentationGenerationTaskModel(status="running")
        job = PresentationGenerationJob(
            task_id=status.id,
            presentation_id=uuid.uuid4(),
            request_sha256="a" * 64,
            request_payload={"template": "authored"},
            state="running",
            attempt_number=1,
            attempt_token="attempt-a",
        )
        async with maker() as session:
            session.add_all((status, job))
            await session.commit()

        await checkpoints.initialize_checkpoints(
            job_id=job.id,
            attempt_token="attempt-a",
            roles=["CLOSING"],
        )
        await checkpoints.start_stage(
            job_id=job.id,
            slide_index=0,
            stage="author",
            attempt_token="attempt-a",
        )
        await checkpoints.reserve_provider_attempt(
            job_id=job.id,
            slide_index=0,
            attempt_token="attempt-a",
            ordinal=1,
        )
        # Simulate provider return followed by process death: no complete_stage
        # and no fail_stage occurs before a new worker takes the lease.
        async with maker() as session:
            persisted = await session.get(PresentationGenerationJob, job.id)
            persisted.attempt_token = "attempt-b"
            persisted.attempt_number = 2
            session.add(persisted)
            await session.commit()

        crashed = await checkpoints.get_checkpoint(job.id, 0)
        crash_count = int(crashed.error["provider_attempt_count"])
        with pytest.raises(AuthoredStageError, match="attempt lease"):
            await checkpoints.reserve_provider_attempt(
                job_id=job.id,
                slide_index=0,
                attempt_token="attempt-a",
                ordinal=2,
            )

        await checkpoints.start_stage(
            job_id=job.id,
            slide_index=0,
            stage="author",
            attempt_token="attempt-b",
        )
        await checkpoints.reserve_provider_attempt(
            job_id=job.id,
            slide_index=0,
            attempt_token="attempt-b",
            ordinal=2,
        )
        # Same ordinal is an idempotent replay, not a third budget unit.
        await checkpoints.reserve_provider_attempt(
            job_id=job.id,
            slide_index=0,
            attempt_token="attempt-b",
            ordinal=2,
        )
        recovered = await checkpoints.get_checkpoint(job.id, 0)
        recovered_count = int(recovered.error["provider_attempt_count"])
        await engine.dispose()
        return crash_count, recovered_count

    assert asyncio.run(scenario()) == (1, 2)


def test_initial_recovery_and_rescue_use_distinct_stable_public_logical_slots() -> None:
    initial = authored_provider_semantic_slot(
        receipt_retry_class="initial", receipt_attempt_ordinal=1
    )
    recovery = authored_provider_semantic_slot(
        receipt_retry_class="recovery", receipt_attempt_ordinal=2
    )
    rescue = authored_provider_semantic_slot(
        receipt_retry_class="recovery", receipt_attempt_ordinal=3
    )
    first_scope = ProviderUsageScope(
        job_id=uuid.uuid4(),
        operation="authored_generation",
        stage="author",
        attempt_number=1,
        slide_index=2,
    )
    resumed_scope = ProviderUsageScope(
        job_id=first_scope.job_id,
        operation="authored_generation",
        stage="author",
        attempt_number=2,
        slide_index=2,
    )

    assert (initial, recovery, rescue) == (
        "author.initial",
        "author.recovery",
        "author.rescue",
    )
    assert first_scope.public_logical_call_ordinal(initial) != (
        first_scope.public_logical_call_ordinal(recovery)
    )
    assert first_scope.public_logical_call_ordinal(recovery) == (
        resumed_scope.public_logical_call_ordinal(recovery)
    )
    assert len(
        {
            first_scope.public_logical_call_ordinal(slot)
            for slot in (initial, recovery, rescue)
        }
    ) == 3


def test_deck_level_stage_gives_every_remade_slide_its_own_logical_call():
    """Repair and vqa remake several slides inside one scope that names none.

    Durable identity is (operation, stage, slide, slot). Without the slide in the
    slot, two slides remade at once reserve the same logical call and the second
    is refused as a replay, which fails the whole deck.
    """

    job_id = uuid.uuid4()
    deck_scope = ProviderUsageScope(
        job_id=job_id,
        operation="authored_generation",
        stage="repair",
        attempt_number=1,
        slide_index=None,
    )

    slots = {
        authored_provider_semantic_slot(
            receipt_retry_class="recovery",
            receipt_attempt_ordinal=2,
            slide_index=index,
        )
        for index in (3, 7)
    }
    assert len(slots) == 2

    ordinals = {
        deck_scope.public_logical_call_ordinal(slot) for slot in slots
    }
    assert len(ordinals) == 2


def test_a_slide_scope_keeps_the_slot_free_of_the_slide():
    """The per-slide author stage already separates calls by the scope's slide."""

    assert (
        authored_provider_semantic_slot(
            receipt_retry_class="recovery", receipt_attempt_ordinal=2
        )
        == "author.recovery"
    )
    assert (
        authored_provider_semantic_slot(
            receipt_retry_class="initial", receipt_attempt_ordinal=1
        )
        == "author.initial"
    )


def test_active_scope_slide_index_tells_a_deck_stage_from_a_slide_stage():
    """The wiring in author_slide_html asks this to decide the slot shape."""

    from services.provider_usage_instrumentation import (
        active_usage_scope_slide_index,
        provider_usage_scope,
    )

    job_id = uuid.uuid4()
    assert active_usage_scope_slide_index() is None
    with provider_usage_scope(
        job_id=job_id, operation="authored_generation", stage="author", slide_index=4
    ):
        assert active_usage_scope_slide_index() == 4
    with provider_usage_scope(
        job_id=job_id, operation="authored_generation", stage="repair"
    ):
        assert active_usage_scope_slide_index() is None
