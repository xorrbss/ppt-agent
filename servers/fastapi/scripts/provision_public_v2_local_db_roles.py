"""Provision isolated, least-privilege logins for an owned local PostgreSQL RC.

This script is intentionally limited to a PostgreSQL cluster the deployment
itself owns: the disposable one created by
``scripts/start-public-v2-rc.ps1 -UseOwnedPostgres`` and the ``postgres``
service of the docker-compose ``multi`` profile, whose one-shot ``migrate``
service runs it on every boot.  Secrets are read from the child process
environment and are never printed.
"""

from __future__ import annotations

import os

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from migrations import _run_migrations


API_GROUP = "presenton_public_api"
CONTROLLER_GROUP = "presenton_public_controller"
API_LOGIN = "presenton_public_api_login"
CONTROLLER_LOGIN = "presenton_public_controller_login"


def _required(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name.lower()}_required")
    return value


def _psycopg_dsn(raw_url: str) -> str:
    parsed = make_url(raw_url)
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("public_local_role_postgresql_required")
    return parsed.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _ensure_role(cursor: psycopg.Cursor, role_name: str, *, login: bool) -> None:
    cursor.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s",
        (role_name,),
    )
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL("CREATE ROLE {} {}").format(
                sql.Identifier(role_name),
                sql.SQL("LOGIN") if login else sql.SQL("NOLOGIN"),
            )
        )


def _configure_login(
    cursor: psycopg.Cursor,
    *,
    login_role: str,
    password: str,
    member_group: str,
    opposite_group: str,
) -> None:
    cursor.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(login_role), sql.Literal(password))
    )
    cursor.execute(
        sql.SQL("REVOKE {} FROM {}").format(
            sql.Identifier(opposite_group), sql.Identifier(login_role)
        )
    )
    cursor.execute(
        sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(member_group), sql.Identifier(login_role)
        )
    )


def _grant_runtime_privileges(cursor: psycopg.Cursor, group_role: str) -> None:
    role = sql.Identifier(group_role)
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
    cursor.execute(
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            "IN SCHEMA public TO {}"
        ).format(role)
    )
    cursor.execute(
        sql.SQL(
            "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES "
            "IN SCHEMA public TO {}"
        ).format(role)
    )


def main() -> None:
    database_url = _required("DATABASE_URL")
    api_password = _required("PUBLIC_V2_LOCAL_API_DB_PASSWORD")
    controller_password = _required("PUBLIC_V2_LOCAL_CONTROLLER_DB_PASSWORD")

    # Schema changes remain bootstrap-owner work.  Runtime logins are created
    # only after the complete Alembic graph (including RLS policies) exists.
    _run_migrations()
    with psycopg.connect(_psycopg_dsn(database_url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            _ensure_role(cursor, API_GROUP, login=False)
            _ensure_role(cursor, CONTROLLER_GROUP, login=False)
            _ensure_role(cursor, API_LOGIN, login=True)
            _ensure_role(cursor, CONTROLLER_LOGIN, login=True)
            for group in (API_GROUP, CONTROLLER_GROUP):
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH NOLOGIN INHERIT NOSUPERUSER "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(group))
                )
                _grant_runtime_privileges(cursor, group)
            # The public API may atomically enqueue and replay-read a deletion
            # request, but only the controller owns its claim/fence lifecycle.
            cursor.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ON TABLE "
                    "account_deletion_requests FROM {}"
                ).format(sql.Identifier(API_GROUP))
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT ON TABLE "
                    "account_deletion_requests TO {}"
                ).format(sql.Identifier(API_GROUP))
            )
            # The migration's global inventory is private. A fresh local-role
            # provisioning pass must narrow the preceding ALL TABLES grants.
            cursor.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ON TABLE "
                    "workspace_generation_lineage_state FROM {}, {}"
                ).format(sql.Identifier(API_GROUP), sql.Identifier(CONTROLLER_GROUP))
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT (id, complete) ON TABLE "
                    "workspace_generation_lineage_state TO {}"
                ).format(sql.Identifier(API_GROUP))
            )
            _configure_login(
                cursor,
                login_role=API_LOGIN,
                password=api_password,
                member_group=API_GROUP,
                opposite_group=CONTROLLER_GROUP,
            )
            _configure_login(
                cursor,
                login_role=CONTROLLER_LOGIN,
                password=controller_password,
                member_group=CONTROLLER_GROUP,
                opposite_group=API_GROUP,
            )


if __name__ == "__main__":
    main()
