"""Focused contract test for the public 100-slide policy migration pair."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

import migration_revision_contract as revision_contract


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_public_slide_limit_migrations_create_v2_and_raise_v1(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'public-slide-limit.db').as_posix()}"
    config = _alembic_config(database_url)

    command.upgrade(config, "b7c4e2d91f60")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public_policy_profiles
                    SET owner_queued_resource_units = 3001
                    WHERE version = 'public-default/v1'
                    """
                )
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT version, max_slides, global_active_generation,
                           owner_queued_resource_units
                    FROM public_policy_profiles
                    WHERE version IN ('public-default/v1', 'public-default/v2')
                    ORDER BY version
                    """
                )
            ).mappings().all()
            assert rows == [
                {
                    "version": "public-default/v1",
                    "max_slides": 100,
                    "global_active_generation": 20,
                    "owner_queued_resource_units": 3001,
                },
                {
                    "version": "public-default/v2",
                    "max_slides": 100,
                    "global_active_generation": 20,
                    "owner_queued_resource_units": 3001,
                },
            ]
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == revision_contract.HEAD_REVISION

        command.downgrade(config, "b7c4e2d91f60")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT max_slides FROM public_policy_profiles "
                    "WHERE version = 'public-default/v1'"
                )
            ).scalar_one() == 30
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public_policy_profiles "
                    "WHERE version = 'public-default/v2'"
                )
            ).scalar_one() == 0
    finally:
        engine.dispose()

    assert ScriptDirectory.from_config(config).get_heads() == [
        revision_contract.HEAD_REVISION
    ]
