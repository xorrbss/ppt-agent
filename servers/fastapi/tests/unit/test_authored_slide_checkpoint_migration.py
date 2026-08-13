from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import migrations
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def test_checkpoint_models_match_the_additive_schema_contract():
    job_columns = set(PresentationGenerationJob.__table__.columns.keys())
    checkpoint_columns = set(AuthoredSlideCheckpoint.__table__.columns.keys())

    assert migrations.PRESENTATION_GENERATION_JOB_COLUMNS.issubset(job_columns)
    assert migrations.AUTHORED_SLIDE_CHECKPOINT_COLUMNS == checkpoint_columns
    assert {
        constraint.name
        for constraint in AuthoredSlideCheckpoint.__table__.constraints
        if constraint.name
    }.issuperset(
        migrations.AUTHORED_SLIDE_CHECKPOINT_CONSTRAINTS
        | {migrations.AUTHORED_SLIDE_CHECKPOINT_UNIQUE}
    )
    assert {
        index.name for index in AuthoredSlideCheckpoint.__table__.indexes
    } == {migrations.AUTHORED_SLIDE_CHECKPOINT_INDEX}


def test_checkpoint_migration_preserves_old_jobs_and_enforces_slide_state(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'authored-checkpoints.db'}"
    config = _alembic_config(database_url)
    engine = create_engine(database_url)
    job_id = "11111111111111111111111111111111"
    task_id = "task-existing-authored-job"
    try:
        command.upgrade(config, migrations.REVISION_CHAT_TEMPLATE_SCOPE_REPAIR)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO async_presentation_generation_tasks
                        (id, status, created_at, updated_at)
                    VALUES (:task_id, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"task_id": task_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO presentation_generation_jobs
                        (id, task_id, presentation_id, request_sha256,
                         request_payload, export_cookie_required, state,
                         attempt_number, created_at, updated_at)
                    VALUES
                        (:job_id, :task_id, :presentation_id, :request_sha256,
                         '{}', false, 'queued', 0,
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {
                    "job_id": job_id,
                    "task_id": task_id,
                    "presentation_id": "22222222222222222222222222222222",
                    "request_sha256": "a" * 64,
                },
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert migrations.AUTHORED_PRESENTATION_GENERATION_JOB_COLUMNS <= (
                _column_names(inspector, "presentation_generation_jobs")
            )
            assert migrations.AUTHORED_SLIDE_CHECKPOINT_COLUMNS <= (
                _column_names(inspector, "authored_slide_checkpoints")
            )
            progress = connection.execute(
                text(
                    """
                    SELECT total_slides, completed_slides, current_stage,
                           last_error, resume_allowed, authored_outline,
                           resume_count
                    FROM presentation_generation_jobs
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            ).mappings().one()
            assert dict(progress) == {
                "total_slides": 0,
                "completed_slides": 0,
                "current_stage": None,
                "last_error": None,
                "resume_allowed": 0,
                "authored_outline": None,
                "resume_count": 0,
            }

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO authored_slide_checkpoints
                        (id, job_id, slide_index)
                    VALUES (:id, :job_id, 0)
                    """
                ),
                {
                    "id": "33333333333333333333333333333333",
                    "job_id": job_id,
                },
            )
            checkpoint = connection.execute(
                text(
                    """
                    SELECT author_status, illustrate_status,
                           render_status, persist_status
                    FROM authored_slide_checkpoints
                    WHERE job_id = :job_id AND slide_index = 0
                    """
                ),
                {"job_id": job_id},
            ).mappings().one()
            assert set(checkpoint.values()) == {"pending"}

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO authored_slide_checkpoints
                            (id, job_id, slide_index)
                        VALUES (:id, :job_id, 0)
                        """
                    ),
                    {
                        "id": "44444444444444444444444444444444",
                        "job_id": job_id,
                    },
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO authored_slide_checkpoints
                            (id, job_id, slide_index, author_status)
                        VALUES (:id, :job_id, 1, 'unknown')
                        """
                    ),
                    {
                        "id": "55555555555555555555555555555555",
                        "job_id": job_id,
                    },
                )

        command.downgrade(config, migrations.REVISION_CHAT_TEMPLATE_SCOPE_REPAIR)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "authored_slide_checkpoints" not in inspector.get_table_names()
            assert not (
                (
                    migrations.AUTHORED_PRESENTATION_GENERATION_JOB_COLUMNS
                    | migrations.AUTHORED_RESUME_COUNT_COLUMNS
                )
                & _column_names(inspector, "presentation_generation_jobs")
            )
            assert connection.execute(
                text(
                    "SELECT COUNT(*) FROM presentation_generation_jobs "
                    "WHERE id = :job_id"
                ),
                {"job_id": job_id},
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_schema_inference_distinguishes_previous_complete_and_partial_heads(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'authored-inference.db'}"
    config = _alembic_config(database_url)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, migrations.REVISION_CHAT_TEMPLATE_SCOPE_REPAIR)
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            assert migrations._infer_revision_from_schema(
                inspector, tables, "future-head"
            ) == migrations.REVISION_CHAT_TEMPLATE_SCOPE_REPAIR

        command.upgrade(config, migrations.REVISION_AUTHORED_SLIDE_CHECKPOINTS)
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            assert migrations._infer_revision_from_schema(
                inspector, tables, "future-head"
            ) == migrations.REVISION_AUTHORED_SLIDE_CHECKPOINTS

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            assert migrations._infer_revision_from_schema(
                inspector, tables, "future-head"
            ) == migrations.REVISION_ASSET_GATEWAY_IDEMPOTENCY

        with engine.begin() as connection:
            connection.execute(text("DROP TABLE authored_slide_checkpoints"))
        with engine.connect() as connection:
            inspector = inspect(connection)
            with pytest.raises(
                RuntimeError,
                match="Authored slide checkpoint schema is only partially applied",
            ):
                migrations._infer_revision_from_schema(
                    inspector,
                    set(inspector.get_table_names()),
                    "future-head",
                )
    finally:
        engine.dispose()
