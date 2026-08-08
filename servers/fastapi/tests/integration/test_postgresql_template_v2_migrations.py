"""Live PostgreSQL coverage for the additive Template V2 migration lineage.

The test database is destructive and must be explicitly provided through
PPT_AGENT_POSTGRES_TEST_URL. Dedicated CI also sets
PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1 so a missing URL cannot become a
false-green skip.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

import migrations
from utils.db_utils import to_sync_sqlalchemy_url


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
PRE_TEMPLATE_V2_REVISION = "f3a4b5c6d7e8"
PRE_LOCAL_STATE_REVISION = "e8f9a0b1c2d3"
HEAD_REVISION = "e7a9c2d4f6b8"
LEGACY_TASK_TIMEZONE = "Asia/Seoul"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(FASTAPI_ROOT / "alembic"),
    )
    # Alembic Config performs interpolation on percent signs.
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%"),
    )
    return config


def _reset_public_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def postgres_database() -> tuple[Engine, Config]:
    require_postgres = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured_url = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured_url:
        if require_postgres:
            pytest.fail(
                "PPT_AGENT_POSTGRES_TEST_URL is required by the PostgreSQL "
                "integration gate"
            )
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL migrations")

    database_url = to_sync_sqlalchemy_url(configured_url)
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    database_name = parsed_url.database or ""
    if not re.search(r"(?:^|[_-])tests?$", database_name, re.IGNORECASE):
        pytest.fail(
            "refusing destructive migration test against a database whose "
            "name does not end in test or tests"
        )

    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        _reset_public_schema(engine)
        yield engine, _alembic_config(database_url)
    finally:
        _reset_public_schema(engine)
        engine.dispose()


def _seed_legacy_presentation(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO presentations
                    (
                        id, content, n_slides, language, title,
                        created_at, updated_at, mode, share_token
                    )
                VALUES
                    (
                        :id, :content, 1, 'en', :title,
                        :created_at, :updated_at, 'template', :share_token
                    )
                """
            ),
            {
                "id": presentation_id,
                "content": "legacy PostgreSQL deck",
                "title": "Legacy PostgreSQL",
                "created_at": now,
                "updated_at": now,
                "share_token": "legacy-postgres-share",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO slides
                    (
                        id, presentation, layout_group, layout, "index",
                        content, html_content
                    )
                VALUES
                    (
                        :id, :presentation, 'default', 'title', 0,
                        CAST(:content AS JSON), :html_content
                    )
                """
            ),
            {
                "id": slide_id,
                "presentation": presentation_id,
                "content": '{"title": "preserved"}',
                "html_content": "<section>preserved</section>",
            },
        )
    return presentation_id, slide_id


def _assert_legacy_data(
    engine: Engine,
    presentation_id: uuid.UUID,
    slide_id: uuid.UUID,
    *,
    expect_template_columns: bool,
) -> None:
    with engine.connect() as connection:
        presentation = (
            connection.execute(
                text(
                    """
                SELECT content, title, mode, share_token
                FROM presentations
                WHERE id = :id
                """
                ),
                {"id": presentation_id},
            )
            .mappings()
            .one()
        )
        slide = (
            connection.execute(
                text(
                    """
                SELECT content, html_content
                FROM slides
                WHERE id = :id
                """
                ),
                {"id": slide_id},
            )
            .mappings()
            .one()
        )
        assert presentation == {
            "content": "legacy PostgreSQL deck",
            "title": "Legacy PostgreSQL",
            "mode": "template",
            "share_token": "legacy-postgres-share",
        }
        assert slide["content"] == {"title": "preserved"}
        assert slide["html_content"] == "<section>preserved</section>"

        if expect_template_columns:
            assert (
                connection.scalar(
                    text("SELECT version FROM presentations WHERE id = :id"),
                    {"id": presentation_id},
                )
                == "v1-standard"
            )
            assert (
                connection.scalar(
                    text("SELECT ui FROM slides WHERE id = :id"),
                    {"id": slide_id},
                )
                is None
            )


def _foreign_key(
    inspector,
    table_name: str,
    constraint_name: str,
) -> dict:
    return next(
        foreign_key
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key.get("name") == constraint_name
    )


def _assert_head_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert {
        "template_v2",
        "template_v2_local_state",
        "template_v2_pptx_imports",
    }.issubset(table_names)

    checks = {check.get("name") for check in inspector.get_check_constraints("slides")}
    assert "ck_slides_native_ui_or_authored_html" in checks

    template_fk = _foreign_key(
        inspector,
        "template_v2",
        "fk_template_v2_presentation_id_presentations",
    )
    local_state_fk = _foreign_key(
        inspector,
        "template_v2_local_state",
        "fk_template_v2_local_state_presentation_id_presentations",
    )
    assert template_fk["options"]["ondelete"].upper() == "RESTRICT"
    assert local_state_fk["options"]["ondelete"].upper() == "RESTRICT"

    local_template_fk = _foreign_key(
        inspector,
        "template_v2_local_state",
        "fk_template_v2_local_state_template_id_template_v2",
    )
    assert local_template_fk["options"]["ondelete"].upper() == "CASCADE"
    import_draft_fk = _foreign_key(
        inspector,
        "template_v2_pptx_imports",
        "fk_template_v2_pptx_imports_draft_template_id",
    )
    assert import_draft_fk["options"]["ondelete"].upper() == "SET NULL"

    template_indexes = {
        index["name"]: index for index in inspector.get_indexes("template_v2")
    }
    assert template_indexes["ix_template_v2_presentation_id"]["column_names"] == [
        "presentation_id"
    ]
    assert not template_indexes["ix_template_v2_presentation_id"]["unique"]

    import_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("template_v2_pptx_imports")
    }
    assert import_indexes["ix_template_v2_pptx_imports_task_id"]["unique"]
    assert import_indexes["ix_template_v2_pptx_imports_requested_template_id"][
        "column_names"
    ] == ["requested_template_id"]
    assert import_indexes["ix_template_v2_pptx_imports_draft_template_id"][
        "column_names"
    ] == ["draft_template_id"]

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("template_v2_pptx_imports")
    }
    assert {
        "uq_template_v2_pptx_imports_task_id",
        "uq_template_v2_pptx_imports_source_storage_key",
    }.issubset(unique_constraints)


def test_postgresql_full_cycle_preserves_legacy_data_and_contracts(
    postgres_database,
):
    engine, config = postgres_database
    command.upgrade(config, PRE_TEMPLATE_V2_REVISION)
    presentation_id, slide_id = _seed_legacy_presentation(engine)

    command.upgrade(config, "head")
    _assert_legacy_data(
        engine,
        presentation_id,
        slide_id,
        expect_template_columns=True,
    )
    _assert_head_constraints(engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == HEAD_REVISION
        )

    command.downgrade(config, PRE_TEMPLATE_V2_REVISION)
    _assert_legacy_data(
        engine,
        presentation_id,
        slide_id,
        expect_template_columns=False,
    )
    downgraded_inspector = inspect(engine)
    assert "template_v2" not in downgraded_inspector.get_table_names()
    assert "version" not in {
        column["name"] for column in downgraded_inspector.get_columns("presentations")
    }
    assert "ui" not in {
        column["name"] for column in downgraded_inspector.get_columns("slides")
    }

    command.upgrade(config, "head")
    _assert_legacy_data(
        engine,
        presentation_id,
        slide_id,
        expect_template_columns=True,
    )
    _assert_head_constraints(engine)


def _seed_populated_template_v2(
    engine: Engine,
) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID, datetime, datetime]:
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()
    template_id = "postgres-preserved-template"
    import_id = uuid.uuid4()
    task_id = "postgres-preserved-import"
    now = datetime.now(timezone.utc)
    # The legacy model used datetime.now(), so this value represents a local
    # wall clock rather than UTC. The e7 migration must preserve its instant.
    task_created_at = datetime(2026, 8, 3, 15, 30, 45)
    task_updated_at = task_created_at + timedelta(minutes=7, seconds=11)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO presentations
                    (
                        id, content, n_slides, language, created_at,
                        updated_at, mode, share_token, version
                    )
                VALUES
                    (
                        :id, 'Template V2 PostgreSQL', 1, 'en', :created_at,
                        :updated_at, 'template', 'v2-postgres-share',
                        'v2-standard'
                    )
                """
            ),
            {
                "id": presentation_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO slides
                    (
                        id, presentation, layout_group, layout, "index",
                        content
                    )
                VALUES
                    (
                        :id, :presentation, 'default', 'title', 0,
                        CAST(:content AS JSON)
                    )
                """
            ),
            {
                "id": slide_id,
                "presentation": presentation_id,
                "content": '{"title": "Template V2"}',
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO template_v2
                    (id, presentation_id, name, revision)
                VALUES
                    (:id, :presentation_id, 'Preserved template', 7)
                """
            ),
            {"id": template_id, "presentation_id": presentation_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO async_presentation_generation_tasks
                    (id, status, created_at, updated_at)
                VALUES
                    (:id, 'SUCCESS', :created_at, :updated_at)
                """
            ),
            {
                "id": task_id,
                "created_at": task_created_at,
                "updated_at": task_updated_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO template_v2_pptx_imports
                    (
                        id, task_id, requested_template_id,
                        draft_template_id, state, source_filename,
                        source_media_type, source_size_bytes, source_sha256,
                        source_storage_key, manifest
                    )
                VALUES
                    (
                        :id, :task_id, :template_id, :template_id, 'ready',
                        'source.pptx', :media_type, 4, :source_sha256,
                        :storage_key, CAST(:manifest AS JSON)
                    )
                """
            ),
            {
                "id": import_id,
                "task_id": task_id,
                "template_id": template_id,
                "media_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                "source_sha256": "3" * 64,
                "storage_key": "imports/postgres-preserved/source.pptx",
                "manifest": "{}",
            },
        )
    return (
        presentation_id,
        slide_id,
        template_id,
        import_id,
        task_created_at,
        task_updated_at,
    )


def _assert_populated_state(
    engine: Engine,
    presentation_id: uuid.UUID,
    template_id: str,
    import_id: uuid.UUID,
    legacy_task_created_at: datetime,
    legacy_task_updated_at: datetime,
) -> None:
    with engine.connect() as connection:
        task_timestamp_columns = {
            column["name"]: column["type"]
            for column in inspect(connection).get_columns(
                "async_presentation_generation_tasks"
            )
            if column["name"] in {"created_at", "updated_at"}
        }
        assert task_timestamp_columns["created_at"].timezone is True
        assert task_timestamp_columns["updated_at"].timezone is True
        local_state = (
            connection.execute(
                text(
                    """
                SELECT presentation_id, revision
                FROM template_v2_local_state
                WHERE template_id = :template_id
                """
                ),
                {"template_id": template_id},
            )
            .mappings()
            .one()
        )
        assert local_state["presentation_id"] == presentation_id
        assert local_state["revision"] == 7
        assert (
            connection.scalar(
                text(
                    """
                SELECT draft_template_id
                FROM template_v2_pptx_imports
                WHERE id = :id
                """
                ),
                {"id": import_id},
            )
            == template_id
        )
        task_timestamps = connection.execute(
            text(
                """
                SELECT task.created_at, task.updated_at
                FROM async_presentation_generation_tasks AS task
                JOIN template_v2_pptx_imports AS import_job
                  ON import_job.task_id = task.id
                WHERE import_job.id = :id
                """
            ),
            {"id": import_id},
        ).mappings().one()
        assert task_timestamps["created_at"].tzinfo is not None
        assert task_timestamps["updated_at"].tzinfo is not None
        assert task_timestamps["created_at"] == legacy_task_created_at.replace(
            tzinfo=ZoneInfo(LEGACY_TASK_TIMEZONE)
        ).astimezone(timezone.utc)
        assert task_timestamps["updated_at"] == legacy_task_updated_at.replace(
            tzinfo=ZoneInfo(LEGACY_TASK_TIMEZONE)
        ).astimezone(timezone.utc)


def _task_migration_state(engine: Engine) -> tuple[object, ...]:
    with engine.connect() as connection:
        timestamp_types = {
            column["name"]: bool(column["type"].timezone)
            for column in inspect(connection).get_columns(
                "async_presentation_generation_tasks"
            )
            if column["name"] in {"created_at", "updated_at"}
        }
        task_timestamps = [
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT created_at, updated_at
                    FROM async_presentation_generation_tasks
                    ORDER BY id
                    """
                )
            ).all()
        ]
        revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    return revision, timestamp_types, task_timestamps


def test_postgresql_populated_sidecar_cycle_and_child_first_delete(
    postgres_database,
    monkeypatch,
):
    engine, config = postgres_database
    command.upgrade(config, PRE_LOCAL_STATE_REVISION)
    (
        presentation_id,
        slide_id,
        template_id,
        import_id,
        legacy_task_created_at,
        legacy_task_updated_at,
    ) = _seed_populated_template_v2(engine)

    legacy_state = _task_migration_state(engine)

    monkeypatch.delenv("PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE", raising=False)
    with pytest.raises(
        RuntimeError,
        match="PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE is required",
    ):
        command.upgrade(config, "head")
    assert _task_migration_state(engine) == legacy_state

    monkeypatch.setenv("PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE", "Mars/Olympus")
    with pytest.raises(
        RuntimeError,
        match="must name a PostgreSQL timezone",
    ):
        command.upgrade(config, "head")
    assert _task_migration_state(engine) == legacy_state

    monkeypatch.setenv(
        "PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE",
        LEGACY_TASK_TIMEZONE,
    )
    command.upgrade(config, "head")
    _assert_populated_state(
        engine,
        presentation_id,
        template_id,
        import_id,
        legacy_task_created_at,
        legacy_task_updated_at,
    )
    _assert_head_constraints(engine)

    connection = engine.connect()
    transaction = connection.begin()
    try:
        with pytest.raises(IntegrityError):
            connection.execute(
                text("DELETE FROM presentations WHERE id = :id"),
                {"id": presentation_id},
            )
    finally:
        transaction.rollback()
        connection.close()
    _assert_populated_state(
        engine,
        presentation_id,
        template_id,
        import_id,
        legacy_task_created_at,
        legacy_task_updated_at,
    )

    aware_state = _task_migration_state(engine)
    monkeypatch.delenv("PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE", raising=False)
    with pytest.raises(
        RuntimeError,
        match="PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE is required",
    ):
        command.downgrade(config, PRE_LOCAL_STATE_REVISION)
    assert _task_migration_state(engine) == aware_state

    monkeypatch.setenv(
        "PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE",
        LEGACY_TASK_TIMEZONE,
    )
    command.downgrade(config, PRE_LOCAL_STATE_REVISION)
    with engine.connect() as connection:
        downgraded_inspector = inspect(connection)
        assert "template_v2_local_state" not in downgraded_inspector.get_table_names()
        downgraded_timestamp_columns = {
            column["name"]: column["type"]
            for column in downgraded_inspector.get_columns(
                "async_presentation_generation_tasks"
            )
            if column["name"] in {"created_at", "updated_at"}
        }
        assert downgraded_timestamp_columns["created_at"].timezone is False
        assert downgraded_timestamp_columns["updated_at"].timezone is False
        downgraded_task_timestamps = connection.execute(
            text(
                """
                SELECT task.created_at, task.updated_at
                FROM async_presentation_generation_tasks AS task
                JOIN template_v2_pptx_imports AS import_job
                  ON import_job.task_id = task.id
                WHERE import_job.id = :id
                """
            ),
            {"id": import_id},
        ).mappings().one()
        assert downgraded_task_timestamps["created_at"].tzinfo is None
        assert downgraded_task_timestamps["updated_at"].tzinfo is None
        assert downgraded_task_timestamps["created_at"] == legacy_task_created_at
        assert downgraded_task_timestamps["updated_at"] == legacy_task_updated_at
        assert (
            connection.scalar(
                text("SELECT revision FROM template_v2 WHERE id = :id"),
                {"id": template_id},
            )
            == 7
        )
        assert (
            connection.scalar(
                text(
                    """
                SELECT draft_template_id
                FROM template_v2_pptx_imports
                WHERE id = :id
                """
                ),
                {"id": import_id},
            )
            == template_id
        )

    command.upgrade(config, "head")
    _assert_populated_state(
        engine,
        presentation_id,
        template_id,
        import_id,
        legacy_task_created_at,
        legacy_task_updated_at,
    )

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM template_v2 WHERE id = :id"),
            {"id": template_id},
        )
        assert (
            connection.scalar(
                text(
                    """
                SELECT COUNT(*)
                FROM template_v2_local_state
                WHERE template_id = :id
                """
                ),
                {"id": template_id},
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    """
                SELECT draft_template_id
                FROM template_v2_pptx_imports
                WHERE id = :id
                """
                ),
                {"id": import_id},
            )
            is None
        )
        connection.execute(
            text("DELETE FROM presentations WHERE id = :id"),
            {"id": presentation_id},
        )

    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM presentations WHERE id = :id"),
                {"id": presentation_id},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM slides WHERE id = :id"),
                {"id": slide_id},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM template_v2_pptx_imports WHERE id = :id"),
                {"id": import_id},
            )
            == 1
        )


def test_postgresql_task_timestamp_recovery_is_type_idempotent(
    postgres_database,
    monkeypatch,
):
    engine, config = postgres_database
    monkeypatch.delenv("PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE", raising=False)
    command.upgrade(config, "head")

    created_at = datetime(2026, 8, 3, 6, 30, 45, tzinfo=timezone.utc)
    updated_at = created_at + timedelta(minutes=9, seconds=13)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO async_presentation_generation_tasks
                    (id, status, created_at, updated_at)
                VALUES
                    ('timestamp-recovery', 'SUCCESS', :created_at, :updated_at)
                """
            ),
            {"created_at": created_at, "updated_at": updated_at},
        )

    expected_head_state = _task_migration_state(engine)
    assert expected_head_state[0] == HEAD_REVISION

    # Defense in depth: a stale d6 stamp must not reinterpret already-aware
    # values when Alembic invokes e7 again.
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": migrations.REVISION_CHAT_FIRST_WORKSPACE},
        )
    command.upgrade(config, "head")
    assert _task_migration_state(engine) == expected_head_state

    database_url = engine.url.render_as_string(hide_password=False)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    migrations._stamp_legacy_database_if_needed(config, database_url)
    assert _task_migration_state(engine) == expected_head_state

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = 'orphaned_e7'")
        )
    migrations._repair_orphan_alembic_revision(config, database_url)
    assert _task_migration_state(engine) == expected_head_state

    # A hand-edited mixed state cannot be inferred safely. The orphan repair
    # must leave its revision, types, and values unchanged.
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE async_presentation_generation_tasks
                ALTER COLUMN created_at TYPE TIMESTAMP WITHOUT TIME ZONE
                USING created_at AT TIME ZONE 'UTC'
                """
            )
        )
        connection.execute(
            text(
                "UPDATE alembic_version "
                "SET version_num = 'orphaned_mixed_revision'"
            )
        )
    mixed_state = _task_migration_state(engine)
    with pytest.raises(RuntimeError, match="only partially applied"):
        migrations._repair_orphan_alembic_revision(config, database_url)
    assert _task_migration_state(engine) == mixed_state
