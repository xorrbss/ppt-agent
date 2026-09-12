"""Refuse to run this code against a database at a different Alembic revision.

A database whose ``alembic_version`` does not match the revision this code was
written against is not a degraded mode, it is a wrong mode: every assumption the
ORM models, the check constraints, the counter triggers and the policy rows make
about the schema is unverified.  The failures that follow are late and
expensive.  The observed one was a counter-consistency trigger aborting the
COMMIT after a thirty-five slide deck had been authored, rendered and persisted
-- 761 seconds of provider spend, nothing published.  The same mismatch is
detectable at startup in milliseconds, so it is refused there instead.

Two directions, two remedies:

* **behind** -- the stamped revision is an ancestor of this code's head, so the
  schema can be brought forward.  ``uv run alembic upgrade head``, or a restart
  with ``MIGRATE_DATABASE_ON_STARTUP=true`` which migrates before this check --
  *if* the connected role can apply schema changes.  Both remedies reconnect
  with the same runtime credential, so when it cannot, the message instead
  names the role and sends the operator to re-run the graph with the
  bootstrap owner DSN (see ``_behind_remedy``); it says plainly that
  ``MIGRATE_DATABASE_ON_STARTUP=true`` will not help there, since startup uses
  that same runtime credential.
* **ahead (or diverged)** -- the stamped revision is not an ancestor of this
  code's head, so the database was migrated by newer or different code.
  Upgrading cannot reconcile it: either the code moves to match the database,
  or the database is downgraded by the code that stamped it.

A database with no ``alembic_version`` table was never under Alembic authority
-- ``create_db_and_tables()`` builds exactly that when
``MIGRATE_DATABASE_ON_STARTUP`` is unset -- so there is no stamped revision to
disagree with and this module says nothing.  The moment a stamp exists, it has
to agree.

There is deliberately no environment variable that skips this check.  An escape
hatch is how a schema mismatch becomes permanent.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from migration_privilege_diagnostics import (
    default_schema_for_database_url,
    probe_schema_privileges,
)
from migration_revision_contract import HEAD_REVISION
from utils.db_utils import to_sync_sqlalchemy_url


# ``services/`` sits directly under the FastAPI package root, which is the
# directory ``migrations.py`` resolves Alembic's ``script_location`` against.
_ALEMBIC_DIRECTORY = Path(__file__).resolve().parents[1] / "alembic"

REVISION_BEHIND_CODE = "database_schema_revision_behind_code"
REVISION_AHEAD_OF_CODE = "database_schema_revision_ahead_of_code"

_UPGRADE_COMMAND = "uv run alembic upgrade head"


class DatabaseRevisionMismatch(RuntimeError):
    """Startup refusal naming both revisions and the command that fixes them."""

    def __init__(self, code: str, database_revision: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.database_revision = database_revision
        self.code_revision = HEAD_REVISION


_ORIGINAL_BEHIND_REMEDY = (
    f"Run `{_UPGRADE_COMMAND}` in servers/fastapi, or restart this process with "
    "MIGRATE_DATABASE_ON_STARTUP=true so startup migrates the database before "
    "this check."
)


async def _behind_remedy(engine: AsyncEngine) -> str:
    """Pick the remedy the connected role can actually perform.

    Both remedies in ``_ORIGINAL_BEHIND_REMEDY`` reconnect with the same
    runtime credential the caller is already using, so they only work when
    that role can apply schema changes. This probes that -- reusing
    ``migration_privilege_diagnostics.probe_schema_privileges``, the same
    query ``MigrationPrivilegeWatch`` runs after a migration fails, so there
    is exactly one implementation of "can this role change the schema" -- and
    substitutes a bootstrap-owner remedy when the answer is no.

    The probe is best-effort and never allowed to fail this check: on SQLite
    there is no such catalog to query, and a runtime role can plausibly lack
    even read access to ``pg_namespace``. Either way this falls back to the
    original two-remedy text, which is still correct advice for an unknown
    role/backend.
    """

    facts = await _behind_privilege_facts(engine)
    if facts is not None and facts.get("can_create") is False:
        role = facts.get("role_name") or "unknown"
        schema = facts.get("schema_name") or "unknown"
        return (
            f'Role "{role}" does not have the CREATE privilege on schema '
            f'"{schema}" and cannot apply this migration itself. '
            "MIGRATE_DATABASE_ON_STARTUP=true will NOT help: startup connects "
            "with this same runtime credential and will hit the same "
            f"privilege error. Re-run `{_UPGRADE_COMMAND}` in servers/fastapi "
            "with the bootstrap owner DATABASE_URL instead -- the DSN "
            "servers/fastapi/scripts/provision_public_v2_local_db_roles.py "
            "uses before it creates the runtime logins."
        )
    return _ORIGINAL_BEHIND_REMEDY


async def _behind_privilege_facts(engine: AsyncEngine) -> Optional[dict[str, Any]]:
    """Probe whether the connected role can change the schema, best-effort.

    Returns ``None`` when the probe cannot run at all -- a non-PostgreSQL
    backend (``default_schema_for_database_url`` returns ``None`` for those),
    or any error reading the privilege catalogs, including a permissions
    error on the catalogs themselves. ``None`` means "unknown", never "no";
    the caller falls back to the original text rather than guess.
    """

    try:
        database_url = to_sync_sqlalchemy_url(
            engine.url.render_as_string(hide_password=False)
        )
        schema = default_schema_for_database_url(database_url)
        if schema is None:
            return None
        facts = await asyncio.to_thread(probe_schema_privileges, database_url, schema)
    except Exception:
        return None
    facts = dict(facts)
    facts.setdefault("schema_name", schema)
    return facts


async def _behind_message(engine: AsyncEngine, database_revision: str) -> str:
    remedy = await _behind_remedy(engine)
    return (
        f"{REVISION_BEHIND_CODE}: the database is stamped at Alembic revision "
        f"'{database_revision}' but this code requires '{HEAD_REVISION}'. "
        "Refusing to start now instead of discarding a finished deck at COMMIT "
        f"later. {remedy}"
    )


def _ahead_message(database_revision: str) -> str:
    return (
        f"{REVISION_AHEAD_OF_CODE}: the database is stamped at Alembic revision "
        f"'{database_revision}', which is not an ancestor of this code's head "
        f"'{HEAD_REVISION}' -- the database was migrated by newer or different "
        f"code. `{_UPGRADE_COMMAND}` cannot reconcile this and "
        "MIGRATE_DATABASE_ON_STARTUP=true will not either. Check out the code "
        f"that stamped '{database_revision}', or, from that code, downgrade the "
        f"database with `uv run alembic downgrade {HEAD_REVISION}`."
    )


@lru_cache(maxsize=1)
def _revisions_reachable_from_head() -> frozenset[str]:
    """Every revision on the path from this code's head back to base.

    Consulted only once a mismatch is already known, so the normal startup path
    never reads the migration scripts.  ``HEAD_REVISION`` is machine-checked
    against the real Alembic head in ``tests/unit/test_compatibility_readiness``;
    if that contract is broken the resolution error surfaces here, loudly.
    """

    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_DIRECTORY))
    script = ScriptDirectory.from_config(config)
    return frozenset(
        revision.revision
        for revision in script.iterate_revisions(HEAD_REVISION, "base")
    )


async def _stamped_revisions(engine: AsyncEngine) -> list[str]:
    """Return the sorted ``alembic_version`` rows, or ``[]`` when unstamped."""

    async with engine.connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )
        if "alembic_version" not in table_names:
            return []
        result = await connection.execute(
            text("SELECT version_num FROM alembic_version")
        )
        return sorted(str(value) for value in result.scalars().all())


async def require_database_schema_revision_authority(engine: AsyncEngine) -> None:
    """Raise ``DatabaseRevisionMismatch`` unless the schema is at the code's head."""

    stamped = await _stamped_revisions(engine)
    if not stamped or stamped == [HEAD_REVISION]:
        return
    # More than one stamped head means the database carries schema this code's
    # single-head history does not describe; that is the "ahead" remedy.
    database_revision = "+".join(stamped)
    if len(stamped) == 1 and stamped[0] in _revisions_reachable_from_head():
        raise DatabaseRevisionMismatch(
            REVISION_BEHIND_CODE,
            database_revision,
            await _behind_message(engine, database_revision),
        )
    raise DatabaseRevisionMismatch(
        REVISION_AHEAD_OF_CODE,
        database_revision,
        _ahead_message(database_revision),
    )


__all__ = [
    "REVISION_AHEAD_OF_CODE",
    "REVISION_BEHIND_CODE",
    "DatabaseRevisionMismatch",
    "require_database_schema_revision_authority",
]
