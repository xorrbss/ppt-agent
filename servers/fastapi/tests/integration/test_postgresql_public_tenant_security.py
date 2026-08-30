"""Live PostgreSQL contract for request RLS and controller DB authority.

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
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from utils.db_utils import to_sync_sqlalchemy_url


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_ROLE = "presenton_public_controller"


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
def postgres_tenant_security_database() -> tuple[Engine, Config]:
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


def test_postgresql_request_rls_and_controller_role_are_separate(
    postgres_tenant_security_database: tuple[Engine, Config],
) -> None:
    engine, config = postgres_tenant_security_database
    command.upgrade(config, "head")

    request_role = f"presenton_request_probe_{uuid.uuid4().hex[:12]}"
    owner_id = uuid.uuid4()
    operation_a = uuid.uuid4()
    operation_b = uuid.uuid4()
    artifact_a = uuid.uuid4()
    artifact_b = uuid.uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"CREATE ROLE {request_role} NOLOGIN"))
            connection.execute(
                text(f"GRANT SELECT ON public_artifacts TO {request_role}")
            )
            for artifact_id, operation_id, tenant_id, digest in (
                (artifact_a, operation_a, "tenant-a", "a" * 64),
                (artifact_b, operation_b, "tenant-b", "b" * 64),
            ):
                connection.execute(
                    text(
                        "INSERT INTO public_artifacts "
                        "(id, owner_id, tenant_id, kind, sha256, size_bytes, "
                        "media_type, storage_key, created_by_operation_id, "
                        "retention_class, lifecycle_state, refcount, lifecycle_revision) "
                        "VALUES (:id, :owner_id, :tenant_id, 'checkpoint', :digest, "
                        "1, 'application/json', :storage_key, :operation_id, "
                        "'checkpoint', 'committed', 0, 1)"
                    ),
                    {
                        "id": artifact_id,
                        "owner_id": owner_id,
                        "tenant_id": tenant_id,
                        "digest": digest,
                        "storage_key": f"probe/{tenant_id}/{digest}",
                        "operation_id": operation_id,
                    },
                )

            connection.execute(text(f"SET LOCAL ROLE {request_role}"))
            connection.execute(
                text(
                    "SELECT set_config('presenton.owner_id', :owner, true), "
                    "set_config('presenton.tenant_id', 'tenant-a', true)"
                ),
                {"owner": str(owner_id)},
            )
            assert connection.scalar(text("SELECT count(*) FROM public_artifacts")) == 1
            connection.execute(
                text("SELECT set_config('presenton.tenant_id', '', true)")
            )
            assert connection.scalar(text("SELECT count(*) FROM public_artifacts")) == 0

            connection.execute(text("RESET ROLE"))
            connection.execute(text(f"SET LOCAL ROLE {CONTROLLER_ROLE}"))
            connection.execute(
                text(
                    "SELECT set_config('presenton.owner_id', '', true), "
                    "set_config('presenton.tenant_id', '', true)"
                )
            )
            assert connection.scalar(text("SELECT count(*) FROM public_artifacts")) == 2
            connection.execute(text("RESET ROLE"))
        finally:
            transaction.rollback()
