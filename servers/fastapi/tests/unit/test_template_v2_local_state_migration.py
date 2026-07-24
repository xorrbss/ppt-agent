import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import migrations
from models.sql.template_v2 import (
    TEMPLATE_V2_CANONICAL_COLUMNS as MODEL_CANONICAL_COLUMNS,
    TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS as MODEL_TRANSITIONAL_COLUMNS,
)
from template_v2_schema_contract import (
    TEMPLATE_V2_CANONICAL_COLUMNS,
    TEMPLATE_V2_IMPORT_LIFECYCLE_COLUMNS,
    TEMPLATE_V2_LEGACY_DROP_REQUIREMENT,
    TEMPLATE_V2_PRESENTATION_DELETE_POLICY,
    TEMPLATE_V2_PRESENTATION_OWNERSHIP_POLICY,
    TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS,
    _default_is,
    require_template_v2_legacy_provenance_drop_data_ready,
    validate_template_v2_local_sidecars,
)
from services.template_v2_service import TemplateV2Service


EXPECTED_UPSTREAM_TEMPLATE_V2_COLUMNS = frozenset(
    {
        "id",
        "name",
        "description",
        "raw_layouts",
        "components",
        "merged_components",
        "layouts",
        "assets",
        "is_default",
        "created_at",
        "updated_at",
    }
)


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _create_database_at_retention_head(database_url: str):
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE presentations (
                    id TEXT PRIMARY KEY,
                    share_token TEXT,
                    version TEXT NOT NULL DEFAULT 'v1-standard',
                    mode TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE slides (
                    id TEXT PRIMARY KEY,
                    html_content TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES (:revision)"
            ),
            {"revision": migrations.REVISION_SHARE_TOKEN},
        )
    command.upgrade(
        _alembic_config(database_url),
        migrations.REVISION_TEMPLATE_V2_SOURCE_RETENTION,
    )
    return engine


def _insert_template_before_sidecar(engine, *, revision: int = 7) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO presentations (id, version, mode)
                VALUES (:presentation_id, 'v2-standard', 'template')
                """
            ),
            {"presentation_id": "11111111111111111111111111111111"},
        )
        connection.execute(
            text(
                """
                INSERT INTO template_v2
                    (id, presentation_id, name, revision)
                VALUES
                    (:template_id, :presentation_id, 'Backfilled', :revision)
                """
            ),
            {
                "template_id": "template-local-state",
                "presentation_id": "11111111111111111111111111111111",
                "revision": revision,
            },
        )


def test_pinned_upstream_columns_are_separated_from_local_state():
    assert TEMPLATE_V2_CANONICAL_COLUMNS == EXPECTED_UPSTREAM_TEMPLATE_V2_COLUMNS
    assert MODEL_CANONICAL_COLUMNS == EXPECTED_UPSTREAM_TEMPLATE_V2_COLUMNS
    assert TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS == {
        "presentation_id",
        "revision",
    }
    assert MODEL_TRANSITIONAL_COLUMNS == TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS
    assert not (
        TEMPLATE_V2_CANONICAL_COLUMNS & TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS
    )
    assert _default_is({"default": "'1'::integer"}, "one")


def test_local_state_migration_backfills_and_validates_sidecars(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-local-state.db'}"
    engine = _create_database_at_retention_head(database_url)
    config = _alembic_config(database_url)
    try:
        _insert_template_before_sidecar(engine)

        command.upgrade(config, "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_TEMPLATE_V2_DELETE_SAFETY
            )
            assert set(inspector.get_table_names()) >= {
                "template_v2",
                "template_v2_local_state",
                "template_v2_pptx_imports",
            }
            assert {
                column["name"]
                for column in inspector.get_columns("template_v2")
            } == (
                TEMPLATE_V2_CANONICAL_COLUMNS
                | TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS
            )
            assert {
                column["name"]
                for column in inspector.get_columns(
                    "template_v2_pptx_imports"
                )
            } == TEMPLATE_V2_IMPORT_LIFECYCLE_COLUMNS
            row = connection.execute(
                text(
                    """
                    SELECT
                        template_id,
                        presentation_id,
                        revision,
                        created_at,
                        updated_at
                    FROM template_v2_local_state
                    """
                )
            ).mappings().one()
            assert row["template_id"] == "template-local-state"
            assert (
                row["presentation_id"]
                == "11111111111111111111111111111111"
            )
            assert row["revision"] == 7
            assert row["created_at"] is not None
            assert row["updated_at"] is not None

            report = validate_template_v2_local_sidecars(connection)
            report.require_compatible()
            assert report.complete
            require_template_v2_legacy_provenance_drop_data_ready(connection)
    finally:
        engine.dispose()


def test_complete_schema_inference_recognizes_local_state_head(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-full-chain.db'}"
    engine = create_engine(database_url)
    try:
        command.upgrade(_alembic_config(database_url), "head")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                migrations._infer_revision_from_schema(
                    inspector,
                    set(inspector.get_table_names()),
                    "ignored-future-head",
                )
                == migrations.REVISION_TEMPLATE_V2_DELETE_SAFETY
            )
    finally:
        engine.dispose()


def test_parent_first_delete_is_restricted_and_downgrade_restores_cascade(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'template-v2-cascade.db'}"
    engine = _create_database_at_retention_head(database_url)
    config = _alembic_config(database_url)
    try:
        _insert_template_before_sidecar(engine, revision=1)
        command.upgrade(config, "head")

        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys = ON"))
            assert (
                TEMPLATE_V2_PRESENTATION_OWNERSHIP_POLICY
                == "presentation-owned"
            )
            assert (
                TEMPLATE_V2_PRESENTATION_DELETE_POLICY
                == "explicit-child-first"
            )
            assert "child-to-parent" in TEMPLATE_V2_LEGACY_DROP_REQUIREMENT
            template_fk = next(
                foreign_key
                for foreign_key in inspect(connection).get_foreign_keys(
                    "template_v2"
                )
                if foreign_key["name"]
                == "fk_template_v2_presentation_id_presentations"
            )
            local_state_fk = next(
                foreign_key
                for foreign_key in inspect(connection).get_foreign_keys(
                    "template_v2_local_state"
                )
                if foreign_key["name"]
                == (
                    "fk_template_v2_local_state_presentation_id_"
                    "presentations"
                )
            )
            assert template_fk["options"]["ondelete"] == "RESTRICT"
            assert local_state_fk["options"]["ondelete"] == "RESTRICT"
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "DELETE FROM presentations "
                        "WHERE id = :presentation_id"
                    ),
                    {
                        "presentation_id": (
                            "11111111111111111111111111111111"
                        )
                    },
                )
            connection.rollback()
            assert connection.execute(
                text("SELECT COUNT(*) FROM template_v2")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM template_v2_local_state")
            ).scalar_one() == 1

        command.downgrade(
            config,
            migrations.REVISION_TEMPLATE_V2_LOCAL_STATE,
        )
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys = ON"))
            template_fk = next(
                foreign_key
                for foreign_key in inspect(connection).get_foreign_keys(
                    "template_v2"
                )
                if foreign_key["name"]
                == "fk_template_v2_presentation_id_presentations"
            )
            local_state_fk = next(
                foreign_key
                for foreign_key in inspect(connection).get_foreign_keys(
                    "template_v2_local_state"
                )
                if foreign_key["name"]
                == (
                    "fk_template_v2_local_state_presentation_id_"
                    "presentations"
                )
            )
            assert template_fk["options"]["ondelete"] == "CASCADE"
            assert local_state_fk["options"]["ondelete"] == "CASCADE"
            assert connection.execute(
                text(
                    """
                    SELECT id, presentation_id, name, revision
                    FROM template_v2
                    """
                )
            ).one() == (
                "template-local-state",
                "11111111111111111111111111111111",
                "Backfilled",
                1,
            )
            assert connection.execute(
                text(
                    """
                    SELECT template_id, presentation_id, revision
                    FROM template_v2_local_state
                    """
                )
            ).one() == (
                "template-local-state",
                "11111111111111111111111111111111",
                1,
            )
            connection.execute(
                text(
                    "DELETE FROM presentations WHERE id = :presentation_id"
                ),
                {
                    "presentation_id": (
                        "11111111111111111111111111111111"
                    )
                },
            )
            connection.commit()
            assert connection.execute(
                text("SELECT COUNT(*) FROM template_v2")
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT COUNT(*) FROM template_v2_local_state")
            ).scalar_one() == 0

        command.downgrade(
            config,
            migrations.REVISION_TEMPLATE_V2_SOURCE_RETENTION,
        )
        with engine.connect() as connection:
            assert "template_v2_local_state" not in inspect(
                connection
            ).get_table_names()
    finally:
        engine.dispose()


def test_delete_safety_migration_rejects_divergent_sidecar_ownership(
    tmp_path,
):
    database_url = (
        f"sqlite:///{tmp_path / 'template-v2-delete-safety-guard.db'}"
    )
    engine = _create_database_at_retention_head(database_url)
    config = _alembic_config(database_url)
    try:
        _insert_template_before_sidecar(engine, revision=2)
        command.upgrade(
            config,
            migrations.REVISION_TEMPLATE_V2_LOCAL_STATE,
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (id, version, mode)
                    VALUES (:presentation_id, 'v2-standard', 'template')
                    """
                ),
                {
                    "presentation_id": (
                        "22222222222222222222222222222222"
                    )
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE template_v2_local_state
                    SET presentation_id = :presentation_id
                    WHERE template_id = 'template-local-state'
                    """
                ),
                {
                    "presentation_id": (
                        "22222222222222222222222222222222"
                    )
                },
            )

        with pytest.raises(
            RuntimeError,
            match="requires complete, matching local-state ownership",
        ):
            command.upgrade(
                config,
                migrations.REVISION_TEMPLATE_V2_DELETE_SAFETY,
            )

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_TEMPLATE_V2_LOCAL_STATE
            )
            template_fk = next(
                foreign_key
                for foreign_key in inspector.get_foreign_keys("template_v2")
                if foreign_key["name"]
                == "fk_template_v2_presentation_id_presentations"
            )
            assert template_fk["options"]["ondelete"] == "CASCADE"
            assert connection.execute(
                text("SELECT COUNT(*) FROM template_v2")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM template_v2_local_state")
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_drop_precondition_rejects_orphan_sidecar_rows(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-orphan-guard.db'}"
    engine = _create_database_at_retention_head(database_url)
    config = _alembic_config(database_url)
    try:
        _insert_template_before_sidecar(engine)
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO template_v2_local_state
                        (template_id, presentation_id, revision)
                    VALUES
                        ('missing-template', :presentation_id, 1)
                    """
                ),
                {
                    "presentation_id": (
                        "22222222222222222222222222222222"
                    )
                },
            )

        with engine.connect() as connection:
            report = validate_template_v2_local_sidecars(connection)
            assert (
                "template_v2 local-state contains orphan ownership rows"
                in report.incompatible
            )
            with pytest.raises(
                RuntimeError,
                match="contains orphan ownership rows",
            ):
                require_template_v2_legacy_provenance_drop_data_ready(
                    connection
                )
    finally:
        engine.dispose()


def test_local_state_downgrade_refuses_divergent_sidecar_data(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'template-v2-local-state-guard.db'}"
    engine = _create_database_at_retention_head(database_url)
    config = _alembic_config(database_url)
    try:
        _insert_template_before_sidecar(engine, revision=3)
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE template_v2_local_state
                    SET revision = revision + 1
                    WHERE template_id = 'template-local-state'
                    """
                )
            )

        with pytest.raises(
            RuntimeError,
            match="downgrade would discard newer local state",
        ):
            command.downgrade(
                config,
                migrations.REVISION_TEMPLATE_V2_SOURCE_RETENTION,
            )

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == migrations.REVISION_TEMPLATE_V2_LOCAL_STATE
            )
            assert "template_v2_local_state" in inspect(
                connection
            ).get_table_names()
    finally:
        engine.dispose()


def test_service_update_preserves_sidecar_downgrade_invariant(tmp_path):
    database_path = tmp_path / "template-v2-local-state-service.db"
    database_url = f"sqlite:///{database_path}"
    engine = _create_database_at_retention_head(database_url)
    config = _alembic_config(database_url)
    try:
        _insert_template_before_sidecar(engine, revision=3)
        command.upgrade(config, "head")

        async def update_template() -> int:
            async_engine = create_async_engine(
                f"sqlite+aiosqlite:///{database_path}"
            )
            session_factory = async_sessionmaker(
                async_engine,
                expire_on_commit=False,
            )
            try:
                async with session_factory() as session:
                    updated = await TemplateV2Service(session).update(
                        "template-local-state",
                        changes={"name": "Updated through service"},
                        expected_revision=3,
                    )
                    return updated.revision
            finally:
                await async_engine.dispose()

        assert asyncio.run(update_template()) == 4

        with engine.connect() as connection:
            revisions = connection.execute(
                text(
                    """
                    SELECT template.revision, local_state.revision
                    FROM template_v2 AS template
                    JOIN template_v2_local_state AS local_state
                      ON local_state.template_id = template.id
                    WHERE template.id = 'template-local-state'
                    """
                )
            ).one()
            assert revisions == (4, 4)
            validate_template_v2_local_sidecars(
                connection
            ).require_compatible()

        command.downgrade(
            config,
            migrations.REVISION_TEMPLATE_V2_SOURCE_RETENTION,
        )
        with engine.connect() as connection:
            assert "template_v2_local_state" not in inspect(
                connection
            ).get_table_names()
            assert connection.execute(
                text(
                    "SELECT revision FROM template_v2 "
                    "WHERE id = 'template-local-state'"
                )
            ).scalar_one() == 4
    finally:
        engine.dispose()
