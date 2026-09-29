"""The Stage A content brief survives a generation-job resume.

The bench case these cover: a 20-slide deck spent 535 s on the brief, the
outline call behind it failed with a provider 500, and attempt 2 paid the same
535 s again out of a deadline pinned per operation at admission time.
"""

import asyncio
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from services.content_brief_policy import (
    BRIEF_STATUS_APPLIED,
    BRIEF_STATUS_REUSED,
    BRIEF_STATUS_TIMED_OUT_FALLBACK,
    ContentBriefTimeoutPolicy,
)
from models.content_brief_model import BriefSection, ContentBrief
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import AuthoredDeckPlanCheckpoint
from services import database as database_module
from services.authored_content_brief_checkpoint import (
    CONTENT_BRIEF_CHECKPOINT_STAGE,
    run_content_brief_with_checkpoint,
)

PREVIOUS_REVISION = "c1a3e5f7b9d0"
CURRENT_REVISION = "e8b3d1f6a4c2"
TABLE = "authored_deck_plan_checkpoints"


# ---------------------------------------------------------------------------
# Service: reuse across attempts
# ---------------------------------------------------------------------------


def _brief(title: str) -> ContentBrief:
    return ContentBrief(
        title=title,
        overview="Framing sentence.",
        sections=[
            BriefSection(
                heading="Market",
                key_points=["The segment grew."],
                facts_figures=["37% in 2025"],
            )
        ],
    )


async def _checkpoint_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'content-brief-checkpoints.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                PresentationGenerationJob.__table__,
                AuthoredDeckPlanCheckpoint.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "async_session_maker", maker)
    return engine, maker


async def _running_job(maker, *, attempt_number: int = 1):
    job = PresentationGenerationJob(
        task_id=uuid.uuid4().hex,
        presentation_id=uuid.uuid4(),
        request_sha256="a" * 64,
        request_payload={},
        state="running",
        attempt_number=attempt_number,
        attempt_token=uuid.uuid4().hex,
    )
    async with maker() as session:
        session.add(job)
        await session.commit()
    return job


def _next_attempt(job: PresentationGenerationJob, maker):
    """Re-lease the same job the way the operation runner does on a retry."""

    async def _apply():
        async with maker() as session:
            stored = await session.get(PresentationGenerationJob, job.id)
            stored.attempt_number += 1
            stored.attempt_token = uuid.uuid4().hex
            session.add(stored)
            await session.commit()
            return stored

    return _apply()


async def _stored_rows(maker, job_id):
    async with maker() as session:
        rows = await session.scalars(
            select(AuthoredDeckPlanCheckpoint).where(
                AuthoredDeckPlanCheckpoint.generation_job_id == job_id
            )
        )
        return list(rows)


def _run(coro):
    return asyncio.run(coro)


_INPUTS = {
    "content": "Quarterly results for the Korean market.",
    "language": "Korean",
    "additional_context": "Uploaded revenue table.",
    "tone": "professional",
    "verbosity": "standard",
    "instructions": "Lead with the numbers.",
}


def _never_called(*args, **kwargs):
    raise AssertionError("the brief provider must not be called on a resume")


def test_a_resumed_job_reuses_the_stored_brief_without_a_provider_call(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            job = await _running_job(maker)
            calls: list[str] = []

            async def first_attempt(*args, **kwargs):
                calls.append("provider")
                return _brief("Korean market Q3")

            first = await run_content_brief_with_checkpoint(
                first_attempt, generation_job=job, **_INPUTS
            )
            assert first.status == BRIEF_STATUS_APPLIED
            assert calls == ["provider"]

            resumed = await _next_attempt(job, maker)
            # ``_never_called`` raises if the provider is reached at all, so a
            # green assertion below is proof no call was made.
            second = await run_content_brief_with_checkpoint(
                _never_called, generation_job=resumed, **_INPUTS
            )
            assert second.status == BRIEF_STATUS_REUSED
            assert second.attempts == 0
            assert isinstance(second.brief, ContentBrief)
            assert second.brief.title == "Korean market Q3"
            assert second.brief == first.brief

            rows = await _stored_rows(maker, job.id)
            assert [row.stage for row in rows] == [CONTENT_BRIEF_CHECKPOINT_STAGE]
            assert rows[0].produced_attempt_number == 1
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_resumed_job_with_changed_inputs_recomputes_the_brief(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            job = await _running_job(maker)

            async def produce(title):
                async def _call(*args, **kwargs):
                    return _brief(title)

                return _call

            await run_content_brief_with_checkpoint(
                await produce("original"), generation_job=job, **_INPUTS
            )
            resumed = await _next_attempt(job, maker)

            changed = dict(_INPUTS, instructions="Lead with the risks instead.")
            recomputed = await run_content_brief_with_checkpoint(
                await produce("recomputed"), generation_job=resumed, **changed
            )
            assert recomputed.status == BRIEF_STATUS_APPLIED
            assert recomputed.brief.title == "recomputed"

            # The stale row is replaced, so a third attempt on the changed
            # inputs reuses rather than paying the brief a third time.
            third = await _next_attempt(job, maker)
            reused = await run_content_brief_with_checkpoint(
                _never_called, generation_job=third, **changed
            )
            assert reused.status == BRIEF_STATUS_REUSED
            assert reused.brief.title == "recomputed"

            rows = await _stored_rows(maker, job.id)
            assert len(rows) == 1
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_timed_out_fallback_stores_nothing(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            job = await _running_job(maker)

            async def too_slow(*args, **kwargs):
                await asyncio.sleep(5)
                return _brief("never returned")

            outcome = await run_content_brief_with_checkpoint(
                too_slow,
                generation_job=job,
                policy=ContentBriefTimeoutPolicy(
                    base_seconds=0.01,
                    per_kchar_seconds=0.0,
                    max_seconds=0.01,
                    total_budget_seconds=0.05,
                ),
                **_INPUTS,
            )
            assert outcome.status == BRIEF_STATUS_TIMED_OUT_FALLBACK
            assert outcome.brief is None
            assert await _stored_rows(maker, job.id) == []

            # The next attempt therefore recomputes rather than reusing a
            # fallback that never produced a brief.
            resumed = await _next_attempt(job, maker)
            recovered = await run_content_brief_with_checkpoint(
                lambda *a, **k: _ready(_brief("recovered")),
                generation_job=resumed,
                **_INPUTS,
            )
            assert recovered.status == BRIEF_STATUS_APPLIED
            assert len(await _stored_rows(maker, job.id)) == 1
        finally:
            await engine.dispose()

    _run(scenario())


async def _ready(value):
    return value


def test_without_a_generation_job_the_stage_is_unchanged(tmp_path, monkeypatch):
    """The outline streaming endpoint has no job and must not read the table."""

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            calls: list[str] = []

            async def provider(*args, **kwargs):
                calls.append("provider")
                return _brief("streamed")

            first = await run_content_brief_with_checkpoint(
                provider, generation_job=None, **_INPUTS
            )
            second = await run_content_brief_with_checkpoint(
                provider, generation_job=None, **_INPUTS
            )
            assert [first.status, second.status] == [
                BRIEF_STATUS_APPLIED,
                BRIEF_STATUS_APPLIED,
            ]
            assert calls == ["provider", "provider"]
        finally:
            await engine.dispose()

    _run(scenario())


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


_INSERT = text(
    f"INSERT INTO {TABLE} (id, generation_job_id, stage, input_digest, "
    "output_digest, payload, produced_attempt_number, created_at) VALUES "
    "(:id, :job, :stage, :digest, :digest, '{}', 1, CURRENT_TIMESTAMP)"
)


def _insert(engine, stage: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            _INSERT,
            {
                "id": uuid.uuid4().hex,
                "job": uuid.uuid4().hex,
                "stage": stage,
                "digest": "a" * 64,
            },
        )


def test_stage_widening_round_trips_on_sqlite(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'stage-widening.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        columns_before = {c["name"] for c in inspect(engine).get_columns(TABLE)}
        indexes_before = {i["name"] for i in inspect(engine).get_indexes(TABLE)}
        foreign_keys_before = inspect(engine).get_foreign_keys(TABLE)
        with pytest.raises(IntegrityError):
            _insert(engine, "content_brief")

        command.upgrade(config, CURRENT_REVISION)

        inspector = inspect(engine)
        # The SQLite rewrite must carry the whole table across, not just the
        # constraint it was asked to widen.
        assert {c["name"] for c in inspector.get_columns(TABLE)} == columns_before
        assert {i["name"] for i in inspector.get_indexes(TABLE)} == indexes_before
        assert inspector.get_foreign_keys(TABLE) == foreign_keys_before
        assert {c["name"] for c in inspector.get_check_constraints(TABLE)} == {
            "ck_authored_deck_plan_checkpoint_stage",
            "ck_authored_deck_plan_checkpoint_digests",
            "ck_authored_deck_plan_checkpoint_attempt",
        }
        _insert(engine, "content_brief")
        _insert(engine, "deck_plan")
        with pytest.raises(IntegrityError):
            _insert(engine, "not_a_stage")

        command.downgrade(config, PREVIOUS_REVISION)

        with engine.begin() as connection:
            stages = set(
                connection.execute(text(f"SELECT stage FROM {TABLE}")).scalars()
            )
        assert stages == {"deck_plan"}
        with pytest.raises(IntegrityError):
            _insert(engine, "content_brief")

        command.upgrade(config, CURRENT_REVISION)
        _insert(engine, "content_brief")
        inspector = inspect(engine)
        assert {c["name"] for c in inspector.get_columns(TABLE)} == columns_before
        assert {i["name"] for i in inspector.get_indexes(TABLE)} == indexes_before
    finally:
        engine.dispose()


def test_stage_widening_runs_from_base_to_head(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'stage-widening-head.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        _insert(engine, "content_brief")
    finally:
        engine.dispose()
