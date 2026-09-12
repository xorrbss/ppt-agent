"""Turn a PostgreSQL insufficient-privilege failure during Alembic into a diagnosis.

Migrations are bootstrap-owner work: ``scripts/provision_public_v2_local_db_roles.py``
runs the whole Alembic graph with the bootstrap ``DATABASE_URL`` *before* it creates the
runtime logins, and the runtime logins deliberately hold no DDL rights.  When somebody
runs the graph with a runtime login instead, the driver raises a bare
``permission denied for schema public`` and the operator has to reverse-engineer which
revision was running and which role was used.

This module answers those questions at the moment of failure.  It is deliberately driven
by the SQLSTATE the server returns (``42501``), never by a per-migration annotation: a
revision cannot opt out of the diagnosis, and nobody has to remember to opt in.  The
failure is re-raised, never downgraded to a warning or a skip.
"""

import re
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

MIGRATION_INSUFFICIENT_PRIVILEGE_CODE = "migration_insufficient_privilege"
INSUFFICIENT_PRIVILEGE_SQLSTATE = "42501"

_OWNER_DSN_HINT = (
    "Schema changes are bootstrap-owner work: re-run the migration graph with the "
    "bootstrap owner DATABASE_URL (the DSN that "
    "servers/fastapi/scripts/provision_public_v2_local_db_roles.py uses before it "
    "creates the runtime logins), not the runtime login."
)
_NOT_SKIPPED_HINT = (
    "Nothing was skipped or downgraded: the upgrade failed and its transaction rolled back."
)
_SCHEMA_IN_MESSAGE = re.compile(r"permission denied for schema \"?([^\s\"]+)\"?")
_UNKNOWN = "unknown"


class MigrationInsufficientPrivilegeError(RuntimeError):
    """A migration failed because the connected role lacks the required privilege."""

    code = MIGRATION_INSUFFICIENT_PRIVILEGE_CODE


def _error_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` and every wrapped error reachable from it, without cycles."""

    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        # Breadth-first from the outermost error, so the driver error a wrapper
        # carries in ``orig`` is reached before anything merely in scope.
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)


def _sqlstate_of(error: BaseException) -> Optional[str]:
    """Read the SQLSTATE from psycopg 3, asyncpg or psycopg2 style errors."""

    # psycopg 3 and asyncpg both expose ``sqlstate``; psycopg2 exposes ``pgcode``.
    for attribute in ("sqlstate", "pgcode"):
        value = getattr(error, attribute, None)
        if isinstance(value, str) and value:
            return value
    diagnostics = getattr(error, "diag", None)
    value = getattr(diagnostics, "sqlstate", None)
    return value if isinstance(value, str) and value else None


def find_insufficient_privilege_error(exc: BaseException) -> Optional[BaseException]:
    """Return the driver error in ``exc``'s chain that reports SQLSTATE 42501."""

    for error in _error_chain(exc):
        if _sqlstate_of(error) == INSUFFICIENT_PRIVILEGE_SQLSTATE:
            return error
    return None


def _diag(error: BaseException, field: str) -> Optional[str]:
    diagnostics = getattr(error, "diag", None)
    value = getattr(diagnostics, field, None)
    if isinstance(value, str) and value:
        return value
    # asyncpg exposes the same fields directly on the exception object.
    value = getattr(error, field, None)
    return value if isinstance(value, str) and value else None


def _target_schema(error: BaseException) -> Optional[str]:
    schema = _diag(error, "schema_name")
    if schema:
        return schema
    match = _SCHEMA_IN_MESSAGE.search(str(error))
    return match.group(1) if match else None


def _primary_message(error: BaseException) -> str:
    message = _diag(error, "message_primary") or str(error)
    return " ".join(message.split()) or _UNKNOWN


_ROLE_FACTS_SQL = text(
    """
    SELECT current_user AS role_name,
           current_database() AS database_name,
           namespace.nspname IS NOT NULL AS schema_exists,
           pg_catalog.pg_get_userbyid(namespace.nspowner) AS schema_owner,
           CASE WHEN namespace.oid IS NULL THEN NULL
                ELSE pg_catalog.has_schema_privilege(
                    current_user, namespace.oid, 'CREATE')
           END AS can_create,
           CASE WHEN namespace.oid IS NULL THEN NULL
                ELSE pg_catalog.has_schema_privilege(
                    current_user, namespace.oid, 'USAGE')
           END AS can_use
      FROM (SELECT CAST(:schema_name AS name) AS wanted) AS request
      LEFT JOIN pg_catalog.pg_namespace AS namespace
             ON namespace.nspname = request.wanted
    """
)

_OBJECT_OWNER_SQL = text(
    """
    SELECT pg_catalog.pg_get_userbyid(relation.relowner) AS object_owner
      FROM pg_catalog.pg_class AS relation
      JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
     WHERE namespace.nspname = :schema_name AND relation.relname = :object_name
    """
)


def _probe_database(database_url: str, schema: str, object_name: Optional[str]) -> dict[str, Any]:
    """Collect the facts that make the failure actionable, on a fresh connection.

    The migration connection is inside a failed transaction by the time we get here, so
    it cannot answer any further query; we open our own and never let a probe failure
    replace the migration failure.
    """

    facts: dict[str, Any] = {}
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            row = connection.execute(_ROLE_FACTS_SQL, {"schema_name": schema}).mappings().first()
            if row is not None:
                facts.update(dict(row))
            if object_name:
                owner = connection.execute(
                    _OBJECT_OWNER_SQL,
                    {"schema_name": schema, "object_name": object_name},
                ).scalar()
                if owner:
                    facts["object_owner"] = owner
    finally:
        engine.dispose()
    return facts


def _yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return _UNKNOWN


def build_message(
    *,
    revision: Optional[str],
    last_applied: Optional[str],
    schema: Optional[str],
    facts: dict[str, Any],
    driver_message: str,
) -> str:
    """Render the single, stable diagnosis text."""

    role = facts.get("role_name") or _UNKNOWN
    schema_name = schema or _UNKNOWN
    lines = [
        f"{MIGRATION_INSUFFICIENT_PRIVILEGE_CODE}: Alembic revision "
        f"{revision or _UNKNOWN} failed because role \"{role}\" lacks the privilege "
        f"it needs on schema \"{schema_name}\".",
        f"  database: {facts.get('database_name') or _UNKNOWN}; "
        f"role \"{role}\" on schema \"{schema_name}\": "
        f"CREATE={_yes_no(facts.get('can_create'))}, USAGE={_yes_no(facts.get('can_use'))}",
        f"  schema \"{schema_name}\" owner: {facts.get('schema_owner') or _UNKNOWN}",
    ]
    if facts.get("object_owner"):
        lines.append(f"  altered object owner: {facts['object_owner']}")
    lines.extend(
        [
            f"  driver error: SQLSTATE {INSUFFICIENT_PRIVILEGE_SQLSTATE}: {driver_message}",
            f"  last revision applied before the failure: {last_applied or 'none'}",
            f"  {_OWNER_DSN_HINT}",
            f"  {_NOT_SKIPPED_HINT}",
        ]
    )
    return "\n".join(lines)


class MigrationPrivilegeWatch:
    """Watch one Alembic run and explain an insufficient-privilege failure.

    Installed once in ``alembic/env.py``, so every entry point into the graph -- the
    ``alembic`` CLI, ``migrations.py`` on startup, and ``command.stamp`` -- is covered
    without any revision having to declare anything about itself.
    """

    def __init__(self, config: Config, database_url: str) -> None:
        self._config = config
        self._database_url = database_url
        self._starting_heads: tuple[str, ...] = ()
        self._applied: list[str] = []

    def record_starting_heads(self, migration_context: Any) -> None:
        try:
            self._starting_heads = tuple(migration_context.get_current_heads())
        except Exception:
            self._starting_heads = ()

    def record_applied(self, *, ctx: Any, step: Any, heads: Any, run_args: Any) -> None:
        """``on_version_apply`` hook; Alembic calls it after each successful step."""

        if getattr(step, "is_upgrade", False) and getattr(step, "up_revision_id", None):
            self._applied.append(step.up_revision_id)

    @property
    def last_applied(self) -> Optional[str]:
        return self._applied[-1] if self._applied else None

    def failing_revision(self) -> Optional[str]:
        """Return the revision that was running when the failure happened.

        Alembic applies the pending revisions in order and reports each success through
        ``record_applied``; the first pending revision that never reported success is the
        one that raised.
        """

        lower = self.last_applied or (self._starting_heads[0] if self._starting_heads else "base")
        try:
            script_directory = ScriptDirectory.from_config(self._config)
            pending = list(script_directory.iterate_revisions("heads", lower))
        except Exception:
            return None
        # ``iterate_revisions`` walks down from the head, so the next revision to be
        # applied is the last element.
        return pending[-1].revision if pending else None

    @contextmanager
    def explain_privilege_failures(self) -> Iterator[None]:
        try:
            yield
        except Exception as exc:
            error = find_insufficient_privilege_error(exc)
            if error is None:
                raise
            raise self._diagnose(exc, error) from exc

    def _diagnose(
        self, exc: Exception, error: BaseException
    ) -> MigrationInsufficientPrivilegeError:
        schema = _target_schema(error) or _default_schema(self._database_url)
        facts: dict[str, Any] = {}
        if schema:
            try:
                facts = _probe_database(self._database_url, schema, _diag(error, "table_name"))
            except Exception as probe_error:  # pragma: no cover - probe is best effort
                facts = {"probe_error": str(probe_error)}
        return MigrationInsufficientPrivilegeError(
            build_message(
                revision=self.failing_revision(),
                last_applied=self.last_applied,
                schema=schema,
                facts=facts,
                driver_message=_primary_message(error),
            )
        )


def _default_schema(database_url: str) -> Optional[str]:
    """Fall back to the schema the DSN targets when the server did not name one."""

    try:
        url = make_url(database_url)
    except Exception:
        return "public"
    if url.get_backend_name() != "postgresql":
        return None
    options = str(url.query.get("options") or "")
    match = re.search(r"search_path\s*=\s*([^,\s]+)", options)
    return match.group(1) if match else "public"
