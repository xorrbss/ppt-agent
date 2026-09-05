"""Owner row-level security migration: chain position and policy coverage."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

import migration_revision_contract as revision_contract
from services.database_owner_scope import (
    owner_scoped_table_names,
    tenant_scoped_table_names,
)

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    FASTAPI_ROOT
    / "alembic"
    / "versions"
    / "b5a0d3e7c9f1_enable_owner_row_level_security.py"
)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="module")
def migration_module():
    spec = importlib.util.spec_from_file_location(
        "owner_rls_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingOp:
    """Stand-in for ``alembic.op`` that records SQL for one dialect."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.statements: list[str] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


def _run(module, direction: str, dialect: str) -> list[str]:
    recorder = _RecordingOp(dialect)
    original = module.op
    module.op = recorder
    try:
        getattr(module, direction)()
    finally:
        module.op = original
    return recorder.statements


def test_owner_rls_revision_is_the_linear_head(migration_module) -> None:
    assert migration_module.revision == revision_contract.REVISION_OWNER_ROW_LEVEL_SECURITY
    assert migration_module.down_revision == (
        revision_contract.REVISION_COM_LEASE_TOKEN_VERIFIER_WIDTH
    )
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert script.get_heads() == [revision_contract.HEAD_REVISION]
    assert revision_contract.HEAD_REVISION == migration_module.revision


def test_owner_rls_revision_round_trips_on_sqlite(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'owner-rls.db').as_posix()}"
    config = _alembic_config(database_url)
    engine = create_engine(database_url)

    def _version() -> str:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    try:
        command.upgrade(config, "head")
        assert _version() == revision_contract.REVISION_OWNER_ROW_LEVEL_SECURITY
        command.downgrade(config, "-1")
        assert _version() == revision_contract.REVISION_COM_LEASE_TOKEN_VERIFIER_WIDTH
        command.upgrade(config, "head")
        assert _version() == revision_contract.HEAD_REVISION
    finally:
        engine.dispose()


def test_owner_rls_policy_set_covers_every_strict_owner_model(
    migration_module,
) -> None:
    frozen_owner = set(migration_module._OWNER_TABLES)
    frozen_tenant = set(migration_module._TENANT_SCOPED_TABLES)
    assert frozen_owner.isdisjoint(frozen_tenant)
    # The tenant-scoped public tables were forced by ff2a6c8e0b14 with a
    # stricter predicate; everything else in _STRICT_OWNER_MODELS gets the
    # owner policy here.  A model added later fails this until covered.
    assert frozen_tenant == set(tenant_scoped_table_names())
    assert frozen_owner | frozen_tenant == set(owner_scoped_table_names())


def test_owner_rls_upgrade_emits_forced_policies_per_table(
    migration_module,
) -> None:
    statements = _run(migration_module, "upgrade", "postgresql")
    joined = "\n".join(statements)
    assert f"CREATE ROLE {migration_module._CONTROLLER_ROLE} NOLOGIN" in joined
    for table in migration_module._OWNER_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in statements
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in statements
        policies = [
            statement
            for statement in statements
            if statement.startswith(
                f'CREATE POLICY {migration_module._REQUEST_POLICY} ON "{table}" '
            )
        ]
        assert len(policies) == 1
        policy = policies[0]
        assert "FOR ALL" in policy
        assert "USING (" in policy and "WITH CHECK (" in policy
        assert "current_setting('presenton.owner_id', true)" in policy
        assert "current_setting('presenton.request_scope', true)" in policy
        assert "actor.is_superuser = true" in policy
        assert (
            f'CREATE POLICY {migration_module._CONTROLLER_POLICY} ON "{table}" '
            f"FOR ALL TO {migration_module._CONTROLLER_ROLE} "
            "USING (true) WITH CHECK (true)"
        ) in statements
    for table in migration_module._TENANT_SCOPED_TABLES:
        assert f'"{table}"' not in joined


def test_owner_rls_downgrade_drops_policies_and_disables_rls(
    migration_module,
) -> None:
    statements = _run(migration_module, "downgrade", "postgresql")
    for table in migration_module._OWNER_TABLES:
        assert (
            f'DROP POLICY IF EXISTS {migration_module._REQUEST_POLICY} ON "{table}"'
        ) in statements
        assert (
            f'DROP POLICY IF EXISTS {migration_module._CONTROLLER_POLICY} '
            f'ON "{table}"'
        ) in statements
        assert f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY' in statements
        assert f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY' in statements


def test_owner_rls_is_a_no_op_outside_postgresql(migration_module) -> None:
    assert _run(migration_module, "upgrade", "sqlite") == []
    assert _run(migration_module, "downgrade", "sqlite") == []
