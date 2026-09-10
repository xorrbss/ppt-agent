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
from utils.llm_calls.author_deck import author_correction_from_stored_failure
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_composition_policy import (
    CompositionProfile,
    build_authored_capacity_plan,
)
from services.authored_generation_planner import plan_authored_text
from services.authored_generation_timing_receipt import (
    GENERATION_TIMING_STAGES,
    GenerationTimingRecorder,
    create_generation_attempt_timing_receipt,
    load_generation_attempt_timing_receipts,
)
from services.authored_presentation_pipeline import _author_stage_failure
from services.provider_usage_instrumentation import ProviderUsageScope
from utils.llm_calls import author_deck as author_deck_module
from utils.llm_calls.author_slide import Brand, authored_provider_semantic_slot


def test_pre_author_planner_freezes_known_closing_text_and_call_budget(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    content = "최종 실행 원칙\n" + ("검증 가능한 운영 근거를 유지합니다. " * 20)
    profile = CompositionProfile("ordinary", "authored", True, True, 1)
    capacity_plan = build_authored_capacity_plan((content,), ("CLOSING",), profile)

    planned = plan_authored_text((content,), capacity_plan).at(0)

    assert planned.input_title_chars == len("최종 실행 원칙")
    assert planned.input_body_chars > planned.body_target_chars == 380
    assert planned.input_visible_chars > planned.visible_target_chars == 460
    assert planned.semantic_target_limit == 20
    assert planned.condensation_required is True
    assert planned.condensation_allowed is True
    assert planned.provider_initial_call_limit == 1
    assert planned.provider_recovery_call_limit == 1
    assert "one initial call plus at most one" in planned.prompt_brief()


def test_recovered_slide_uses_only_remaining_provider_attempt(monkeypatch) -> None:
    calls: list[int] = []

    async def fail_author(*_args, **kwargs):
        calls.append(int(kwargs["receipt_attempt_ordinal"]))
        raise RuntimeError("transient author failure")

    monkeypatch.setattr(author_deck_module, "author_slide_html", fail_author)

    with pytest.raises(RuntimeError, match="transient author failure"):
        asyncio.run(
            author_deck_module._author_one_resilient(
                "content",
                "design",
                Brand(topic="test"),
                "CLOSING",
                0,
                1,
                raise_on_failure=True,
                provider_attempt_offset=1,
            )
        )

    assert calls == [2]


def test_terminal_provider_cause_and_attempt_count_survive_safe_checkpoint() -> None:
    terminal = RuntimeError("public provider retry limit")
    terminal.code = "public_provider_retry_limit_exceeded"
    terminal.retryable = False
    wrapped = RuntimeError("wrapped provider failure")
    wrapped.__cause__ = terminal

    error = _author_stage_failure(2, wrapped, provider_attempt_count=2)
    safe = error.as_safe_dict(attempt_number=3)

    assert error.code == "public_provider_retry_limit_exceeded"
    assert error.retryable is False
    assert safe["provider_attempt_count"] == 2
    assert safe["attempt_number"] == 3


def test_attempt_receipt_keeps_non_null_stages_and_operational_causes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    recorder = GenerationTimingRecorder()
    recorder.record_stage_ms("author", 9)
    stages = recorder.snapshot_stage_timings_ms()

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


def test_a_retry_carries_the_reason_the_slide_was_rejected() -> None:
    """The next attempt must know what to fix, or it repeats the mistake.

    The durable checkpoint already records why a slide was rejected. Operation
    ce48036c failed both of slide 35's calls on the same header contract
    violation, so an attempt that re-sends the identical prompt reproduces it.
    """

    stored = {
        "code": "authored_provider_attempt_budget_exhausted",
        "cause": "Authored slide author failed",
        "prior_failure": {
            "code": "authored_slide_author_failed",
            "exception_type": "ValueError",
            "cause": (
                "header contract violated: header_band_missing, "
                "header_title_missing, header_eyebrow_missing"
            ),
        },
    }
    hint = author_correction_from_stored_failure(stored)
    assert hint is not None
    assert "header_band_missing" in hint
    assert "header_title_missing" in hint
    # The generic wrapper message says nothing actionable and is dropped.
    assert "Authored slide author failed" not in hint
    assert len(hint) <= 460

    # A bare code is still better than nothing; an empty record stays silent.
    assert "public_provider_retry_limit_exceeded" in (
        author_correction_from_stored_failure(
            {"code": "public_provider_retry_limit_exceeded"}
        )
        or ""
    )
    assert author_correction_from_stored_failure(None) is None
    assert author_correction_from_stored_failure({}) is None


def test_stored_source_rejection_prefers_actionable_detail_and_exact_block() -> None:
    expected = "‘영상’이 아니라 ‘콘텐츠 운영’을 판다"
    hint = author_correction_from_stored_failure({
        "code": "authored_provider_attempt_budget_exhausted",
        "cause": "Authored slide author failed",
        "prior_failure": {
            "code": "authored_slide_author_failed",
            "cause": "source_content_browser_readback_failed",
            "detail": f"source block text has no unique DOM element (run 0: expected {expected!r})",
            "message": "Authored slide author failed",
            "block_ids": ["source-slide:7:text:10"],
        },
    })
    assert hint is not None
    assert expected in hint
    assert "source-slide:7:text:10" in hint
    assert "authored_provider_attempt_budget_exhausted" not in hint
    assert "source_content_browser_readback_failed" not in hint
    assert len(hint) <= 460


def test_prior_source_rejection_preserves_sanitized_message_across_attempts() -> None:
    expected = "‘영상’이 아니라 ‘콘텐츠 운영’을 판다"
    message = f"source-slide:7:text:10: expected {expected!r}; Bearer sensitivecredential"
    stage_error = AuthoredStageError(
        slide_index=6,
        stage="author",
        code="authored_provider_attempt_budget_exhausted",
        prior_failure={
            "code": "source_content_browser_readback_failed",
            "cause": "source_content_browser_readback_failed",
            "message": message,
            "detail": "Authored slide author failed",
            "block_ids": ["source-slide:7:text:10"],
            "untrusted_payload": "must not survive",
        },
    )
    stored = stage_error.as_safe_dict()
    prior = stored["prior_failure"]
    assert expected in prior["message"]
    assert "sensitivecredential" not in prior["message"]
    assert "[redacted]" in prior["message"]
    assert prior["block_ids"] == ["source-slide:7:text:10"]
    assert "untrusted_payload" not in prior
    hint = author_correction_from_stored_failure(stored)
    assert expected in hint
    assert "source-slide:7:text:10" in hint
    assert "sensitivecredential" not in hint


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
        exhausted = await checkpoints.get_checkpoint(job.id, 0)
        spent_in_first = provider_attempt_count_in_epoch(exhausted.error, 1)

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
            "remaining": remaining,
            "in_second": provider_attempt_count_in_epoch(retried.error, 2),
            "total": int(retried.error["provider_attempt_total"]),
        }

    assert asyncio.run(scenario()) == {
        "spent_in_first": 2,
        "remaining": 0,
        "in_second": 1,
        "total": 2,
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
