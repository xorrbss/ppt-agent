"""Live PostgreSQL contract for the owner row-level security policies.

These assertions are behavioural on purpose.  Substring checks on the policy
text passed even when the predicate was inverted, so every claim here is made
by reading rows back as a non-superuser role.

The database is destructive and must be explicitly provided through
PPT_AGENT_POSTGRES_TEST_URL with a database name ending in test/tests.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

from utils.db_utils import to_sync_sqlalchemy_url


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_ROLE = "presenton_public_controller"
OWNER_RLS_REVISION = "b5a0d3e7c9f1"
PREVIOUS_REVISION = "a4f9c2e7b1d3"
GRANTED_TABLES = ("presentations", "template_v2")


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _reset_public_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def owner_rls_database() -> tuple[Engine, Config]:
    require_postgres = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured_url = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured_url:
        if require_postgres:
            pytest.fail("PPT_AGENT_POSTGRES_TEST_URL is required")
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL security")
    database_url = to_sync_sqlalchemy_url(configured_url)
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    if not re.search(r"(?:^|[_-])tests?$", parsed.database or "", re.IGNORECASE):
        pytest.fail("refusing destructive security test outside a test database")

    engine = create_engine(database_url, poolclass=NullPool)
    with engine.connect() as connection:
        controller_role_preexisting = bool(
            connection.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": CONTROLLER_ROLE},
            )
        )
    try:
        _reset_public_schema(engine)
        yield engine, _alembic_config(database_url)
    finally:
        _reset_public_schema(engine)
        if not controller_role_preexisting:
            with engine.begin() as connection:
                connection.execute(text(f"DROP ROLE IF EXISTS {CONTROLLER_ROLE}"))
        engine.dispose()


def _principal(connection: Connection, owner: str, request_scope: str) -> None:
    connection.execute(
        text(
            "SELECT set_config('presenton.owner_id', :owner, true), "
            "set_config('presenton.request_scope', :scope, true)"
        ),
        {"owner": owner, "scope": request_scope},
    )


def _decks(connection: Connection) -> list[str]:
    return sorted(
        connection.execute(text("SELECT content FROM presentations")).scalars().all()
    )


def _seed(connection: Connection, alice, bob, root) -> None:
    for user_id, username, superuser in (
        (alice, "alice", False),
        (bob, "bob", False),
        (root, "root", True),
    ):
        connection.execute(
            text(
                'INSERT INTO "user" (id, username, hashed_password, is_active, '
                "is_superuser, is_verified, deletion_state) VALUES "
                "(:id, :username, 'x', true, :superuser, true, 'active')"
            ),
            {"id": user_id, "username": username, "superuser": superuser},
        )
    for owner_id, content in ((alice, "alice-deck"), (bob, "bob-deck")):
        connection.execute(
            text(
                "INSERT INTO presentations (id, owner_id, content, n_slides, "
                "language, created_at, updated_at) VALUES "
                "(:id, :owner_id, :content, 1, 'en', now(), now())"
            ),
            {"id": uuid.uuid4(), "owner_id": owner_id, "content": content},
        )
    connection.execute(
        text(
            "INSERT INTO template_v2 (id, owner_id, name) VALUES "
            "('shared', NULL, 'Shared catalog'), "
            "('alice-private', :owner_id, 'Alice private')"
        ),
        {"owner_id": alice},
    )


def test_owner_policies_isolate_owners_and_refuse_identity_less_requests(
    owner_rls_database: tuple[Engine, Config],
) -> None:
    engine, config = owner_rls_database
    command.upgrade(config, "head")

    probe_role = f"presenton_owner_probe_{uuid.uuid4().hex[:12]}"
    alice, bob, root = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"CREATE ROLE {probe_role} NOLOGIN"))
            # A schema recreated by the fixture carries no implicit PUBLIC
            # grant, unlike the one initdb creates.
            connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {probe_role}"))
            for table in GRANTED_TABLES:
                connection.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} "
                        f"TO {probe_role}"
                    )
                )
            _seed(connection, alice, bob, root)

            connection.execute(text(f"SET LOCAL ROLE {probe_role}"))

            _principal(connection, str(alice), "on")
            assert _decks(connection) == ["alice-deck"]
            _principal(connection, str(bob), "on")
            assert _decks(connection) == ["bob-deck"]
            # An active superuser row keeps its cross-owner authority.
            _principal(connection, str(root), "on")
            assert _decks(connection) == ["alice-deck", "bob-deck"]
            # Workers never publish a request scope, so they still read across.
            _principal(connection, "", "")
            assert _decks(connection) == ["alice-deck", "bob-deck"]

            # The predicate must never need SELECT on "user" from the caller.
            savepoint = connection.begin_nested()
            with pytest.raises(ProgrammingError) as denied:
                connection.execute(text('SELECT count(*) FROM "user"'))
            assert "permission denied" in str(denied.value)
            savepoint.rollback()

            # An authenticated request that lost its identity is an error, not
            # an empty result set: the ORM guard and the policy agree.
            savepoint = connection.begin_nested()
            _principal(connection, "", "on")
            with pytest.raises(ProgrammingError) as refused:
                connection.execute(text("SELECT content FROM presentations"))
            assert "owner_context_required" in str(refused.value)
            assert refused.value.orig.sqlstate == "42501"
            savepoint.rollback()

            connection.execute(text("RESET ROLE"))
        finally:
            transaction.rollback()


def test_owner_policies_bound_writes_and_share_the_template_catalog(
    owner_rls_database: tuple[Engine, Config],
) -> None:
    engine, config = owner_rls_database
    command.upgrade(config, "head")

    probe_role = f"presenton_owner_probe_{uuid.uuid4().hex[:12]}"
    alice, bob, root = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"CREATE ROLE {probe_role} NOLOGIN"))
            # A schema recreated by the fixture carries no implicit PUBLIC
            # grant, unlike the one initdb creates.
            connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {probe_role}"))
            for table in GRANTED_TABLES:
                connection.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} "
                        f"TO {probe_role}"
                    )
                )
            _seed(connection, alice, bob, root)
            connection.execute(text(f"SET LOCAL ROLE {probe_role}"))

            # template_v2 carries ownerless shared catalog rows by design.
            _principal(connection, str(alice), "on")
            assert sorted(
                connection.execute(text("SELECT id FROM template_v2")).scalars().all()
            ) == ["alice-private", "shared"]
            _principal(connection, str(bob), "on")
            assert (
                connection.execute(text("SELECT id FROM template_v2")).scalars().all()
                == ["shared"]
            )

            _principal(connection, str(alice), "on")
            assert (
                connection.execute(
                    text(
                        "UPDATE presentations SET content = 'hijacked' "
                        "WHERE content = 'bob-deck'"
                    )
                ).rowcount
                == 0
            )
            assert (
                connection.execute(
                    text("DELETE FROM presentations WHERE content = 'bob-deck'")
                ).rowcount
                == 0
            )

            savepoint = connection.begin_nested()
            with pytest.raises(ProgrammingError) as rejected:
                connection.execute(
                    text(
                        "INSERT INTO presentations (id, owner_id, content, "
                        "n_slides, language, created_at, updated_at) VALUES "
                        "(:id, :owner_id, 'stolen', 1, 'en', now(), now())"
                    ),
                    {"id": uuid.uuid4(), "owner_id": bob},
                )
            assert "row-level security" in str(rejected.value)
            savepoint.rollback()

            connection.execute(text("RESET ROLE"))
            connection.execute(text(f"SET LOCAL ROLE {CONTROLLER_ROLE}"))
            _principal(connection, "", "")
            assert _decks(connection) == ["alice-deck", "bob-deck"]
            connection.execute(text("RESET ROLE"))
        finally:
            transaction.rollback()


def _controller_dml(connection: Connection) -> dict[str, bool]:
    return {
        table: bool(
            connection.scalar(
                text(
                    "SELECT has_table_privilege(:role, :table, "
                    "'SELECT, INSERT, UPDATE, DELETE')"
                ),
                {"role": CONTROLLER_ROLE, "table": table},
            )
        )
        for table in ("presentations", "slides", "authored_slide_editor_events")
    }


def test_owner_rls_downgrade_restores_the_controller_privileges_it_found(
    owner_rls_database: tuple[Engine, Config],
) -> None:
    """a0d4f7c9e2b6 granted the controller DML on all tables in the schema.

    Revoking per table on the way down left account deletion and the
    controller-role workers without the privileges they had before.
    """

    engine, config = owner_rls_database
    command.upgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        before = _controller_dml(connection)
    command.upgrade(config, OWNER_RLS_REVISION)
    with engine.connect() as connection:
        upgraded = _controller_dml(connection)
    command.downgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        downgraded = _controller_dml(connection)
        remaining_policies = connection.scalar(
            text(
                "SELECT count(*) FROM pg_policies WHERE policyname IN "
                "('presenton_owner_scope', 'presenton_owner_controller_authority')"
            )
        )
        remaining_functions = connection.scalar(
            text(
                "SELECT count(*) FROM pg_proc "
                "WHERE proname = 'presenton_owner_row_visible'"
            )
        )

    assert before["presentations"] is True
    assert upgraded["authored_slide_editor_events"] is True
    assert downgraded == before
    assert remaining_policies == 0
    assert remaining_functions == 0
