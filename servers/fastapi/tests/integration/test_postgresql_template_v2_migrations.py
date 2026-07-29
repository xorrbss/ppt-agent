"""Live PostgreSQL coverage for the additive Template V2 migration lineage.

The test database is destructive and must be explicitly provided through
PPT_AGENT_POSTGRES_TEST_URL. Dedicated CI also sets
PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1 so a missing URL cannot become a
false-green skip.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from utils.db_utils import to_sync_sqlalchemy_url


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
PRE_TEMPLATE_V2_REVISION = "f3a4b5c6d7e8"
PRE_LOCAL_STATE_REVISION = "e8f9a0b1c2d3"
HEAD_REVISION = "3d4e5f6a7b8c"


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
) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID]:
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()
    template_id = "postgres-preserved-template"
    import_id = uuid.uuid4()
    task_id = "postgres-preserved-import"
    now = datetime.now(timezone.utc)
    task_now = now.replace(tzinfo=None)
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
                "created_at": task_now,
                "updated_at": task_now,
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
    return presentation_id, slide_id, template_id, import_id


def _assert_populated_state(
    engine: Engine,
    presentation_id: uuid.UUID,
    template_id: str,
    import_id: uuid.UUID,
) -> None:
    with engine.connect() as connection:
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


def test_postgresql_populated_sidecar_cycle_and_child_first_delete(
    postgres_database,
):
    engine, config = postgres_database
    command.upgrade(config, PRE_LOCAL_STATE_REVISION)
    presentation_id, slide_id, template_id, import_id = _seed_populated_template_v2(
        engine
    )

    command.upgrade(config, "head")
    _assert_populated_state(
        engine,
        presentation_id,
        template_id,
        import_id,
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
    )

    command.downgrade(config, PRE_LOCAL_STATE_REVISION)
    with engine.connect() as connection:
        assert "template_v2_local_state" not in inspect(connection).get_table_names()
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
