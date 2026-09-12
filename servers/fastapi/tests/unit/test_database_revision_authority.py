"""Startup refuses a schema revision that does not match the code.

The defect this file guards against: a database one revision behind the code
kept accepting work.  ``services/public_policy_versions.py`` logged
``public_policy_version_fallback`` and generated under older ceilings, while a
counter-consistency trigger aborted the COMMIT of a thirty-five slide deck
after 761 seconds of authoring, rendering and persistence -- full provider
spend, nothing published.  Both are replaced by one refusal at startup.

Every case below builds a real SQLite database with Alembic and drives the same
entry point ``api/lifespan.py`` calls.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import migration_revision_contract as revision_contract
import services.database as database
from services.database_revision_authority import (
    REVISION_AHEAD_OF_CODE,
    REVISION_BEHIND_CODE,
    DatabaseRevisionMismatch,
    require_database_schema_revision_authority,
)


ALEMBIC_DIRECTORY = Path(__file__).resolve().parents[2] / "alembic"

# A real ancestor of head, far enough back that the schema genuinely differs.
BEHIND_REVISION = revision_contract.REVISION_PUBLIC_DEADLINE_CEILING_V4

# A revision this code has never heard of: what a database migrated by newer
# code looks like from here.
UNKNOWN_REVISION = "0000deadbeef"


def _alembic_config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIRECTORY))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    return config


def _upgrade(database_path: Path, revision: str) -> None:
    command.upgrade(_alembic_config(database_path), revision)


async def _restamp(database_path: Path, revision: str) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": revision},
            )
    finally:
        await engine.dispose()


async def _check(database_path: Path) -> None:
    """Run the gate exactly as ``api/lifespan.py`` does, on its own engine."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    try:
        await require_database_schema_revision_authority(engine)
    finally:
        await engine.dispose()


def test_a_database_at_the_code_revision_starts(tmp_path) -> None:
    database_path = tmp_path / "at-head.db"
    _upgrade(database_path, "head")

    asyncio.run(_check(database_path))


def test_a_database_behind_the_code_is_refused_by_name(tmp_path) -> None:
    database_path = tmp_path / "behind.db"
    _upgrade(database_path, BEHIND_REVISION)

    with pytest.raises(DatabaseRevisionMismatch) as refusal:
        asyncio.run(_check(database_path))

    assert refusal.value.code == REVISION_BEHIND_CODE
    assert refusal.value.database_revision == BEHIND_REVISION
    assert refusal.value.code_revision == revision_contract.HEAD_REVISION

    message = str(refusal.value)
    # An operator must be able to act on the message without reading source:
    # both revisions, and the command that reconciles them.
    assert REVISION_BEHIND_CODE in message
    assert BEHIND_REVISION in message
    assert revision_contract.HEAD_REVISION in message
    assert "uv run alembic upgrade head" in message
    assert "MIGRATE_DATABASE_ON_STARTUP=true" in message


def test_a_database_ahead_of_the_code_is_refused_with_the_other_remedy(
    tmp_path,
) -> None:
    database_path = tmp_path / "ahead.db"
    _upgrade(database_path, "head")
    asyncio.run(_restamp(database_path, UNKNOWN_REVISION))

    with pytest.raises(DatabaseRevisionMismatch) as refusal:
        asyncio.run(_check(database_path))

    assert refusal.value.code == REVISION_AHEAD_OF_CODE
    assert refusal.value.database_revision == UNKNOWN_REVISION

    message = str(refusal.value)
    assert UNKNOWN_REVISION in message
    assert revision_contract.HEAD_REVISION in message
    # Upgrading forward cannot fix a database the code does not know, so the
    # message must not send the operator to run it.
    assert f"uv run alembic downgrade {revision_contract.HEAD_REVISION}" in message
    assert "cannot reconcile this" in message


def test_an_unstamped_database_is_out_of_scope(tmp_path) -> None:
    """``create_db_and_tables()`` builds a schema with no ``alembic_version``.

    That database was never under Alembic authority, so there is no stamped
    revision to disagree with and the gate must stay silent -- refusing here
    would break every default self-hosted start.
    """

    database_path = tmp_path / "unstamped.db"

    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE TABLE presentations (id TEXT)"))
        finally:
            await engine.dispose()
        await _check(database_path)

    asyncio.run(scenario())


def test_migrate_database_on_startup_still_self_heals_a_behind_database(
    monkeypatch, tmp_path
) -> None:
    """The local stack's startup order has to keep working.

    ``api/lifespan.py`` runs ``migrate_database_on_startup()`` before the gate,
    so a behind database that is allowed to migrate reaches head and passes
    rather than being refused.
    """

    import migrations

    database_path = tmp_path / "self-heal.db"
    _upgrade(database_path, BEHIND_REVISION)

    with pytest.raises(DatabaseRevisionMismatch):
        asyncio.run(_check(database_path))

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("MIGRATE_DATABASE_ON_STARTUP", "true")
    monkeypatch.delenv("PUBLIC_DATABASE_PROCESS_ROLE", raising=False)
    monkeypatch.delenv("PUBLIC_API_DATABASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_CONTROLLER_DATABASE_URL", raising=False)
    monkeypatch.delenv("PPT_PUBLIC_OIDC_ISSUER", raising=False)

    asyncio.run(migrations.migrate_database_on_startup())
    asyncio.run(_check(database_path))


def test_the_public_api_gate_is_the_single_enforcement_point(
    monkeypatch, tmp_path
) -> None:
    """The refusal reaches startup through the function lifespan calls.

    Local development returns early from the role half of the gate, so the
    revision half has to run before that early return or the runtime that
    actually lost the deck would never see it.
    """

    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("PUBLIC_DATABASE_PROCESS_ROLE", raising=False)
    monkeypatch.delenv("PPT_PUBLIC_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("PUBLIC_V2_INTEGRATION_PROFILE", raising=False)

    behind_path = tmp_path / "lifespan-behind.db"
    _upgrade(behind_path, BEHIND_REVISION)
    head_path = tmp_path / "lifespan-head.db"
    _upgrade(head_path, "head")

    async def scenario(database_path: Path) -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        try:
            await database.require_public_api_database_authority(engine)
        finally:
            await engine.dispose()

    with pytest.raises(DatabaseRevisionMismatch) as refusal:
        asyncio.run(scenario(behind_path))
    assert refusal.value.code == REVISION_BEHIND_CODE

    asyncio.run(scenario(head_path))


def test_there_is_no_environment_variable_that_skips_the_gate() -> None:
    """No bypass knob: an escape hatch is how a mismatch becomes permanent."""

    source = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "database_revision_authority.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Prose may discuss the absence of a knob; the code may not read one.
    names = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"getenv", "environ", "environb"}
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "os" not in imported
