"""Owner row-level security migration: chain position and policy coverage."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import migration_revision_contract as revision_contract
from services.database_owner_scope import (
    catalog_shared_table_names,
    owner_scoped_table_names,
    tenant_scoped_table_names,
)

# The predicate is spelled out here on purpose.  Asserting that the policy
# merely "contains USING (" passed even when the predicate was inverted, so
# these expectations are written independently of the migration's constants.
EXPECTED_OWNER_PREDICATE = "public.presenton_owner_row_visible(owner_id, false)"
EXPECTED_SHARED_PREDICATE = "public.presenton_owner_row_visible(owner_id, true)"
EXPECTED_VISIBILITY_FUNCTION = (
    "CREATE OR REPLACE FUNCTION public.presenton_owner_row_visible( "
    "row_owner_id uuid, allow_shared boolean ) RETURNS boolean "
    "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog "
    "AS $presenton_owner_row_visible$ DECLARE "
    "actor_id text := nullif(current_setting('presenton.owner_id', true), ''); "
    "BEGIN IF actor_id IS NULL THEN "
    "IF coalesce(current_setting('presenton.request_scope', true), '') = 'on' "
    "THEN RAISE EXCEPTION 'owner_context_required' "
    "USING ERRCODE = 'insufficient_privilege'; END IF; RETURN true; END IF; "
    "IF allow_shared AND row_owner_id IS NULL THEN RETURN true; END IF; "
    "IF row_owner_id = actor_id::uuid THEN RETURN true; END IF; "
    'RETURN EXISTS ( SELECT 1 FROM public."user" AS actor '
    "WHERE actor.id = actor_id::uuid AND actor.is_superuser = true "
    "AND actor.is_active = true AND actor.deletion_state = 'active' ); "
    "END; $presenton_owner_row_visible$"
)

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    FASTAPI_ROOT
    / "alembic"
    / "versions"
    / "b5a0d3e7c9f1_enable_owner_row_level_security.py"
)
# The SVG user-template tables were created after the owner RLS migration and
# carry their own policies from the revision that created them.
SVG_TEMPLATE_MIGRATION_PATH = (
    FASTAPI_ROOT / "alembic" / "versions" / "d8f0a2b4c6e9_add_svg_user_templates.py"
)
# Spelled out for the same reason as the owner predicate above.  Owner AND
# tenant must both match the transaction principal; an empty or unset setting
# matches no row, so an identity-less connection that is not the controller
# role sees nothing.
EXPECTED_SVG_TEMPLATE_PREDICATE = (
    "owner_id::text = current_setting('presenton.owner_id', true) "
    "AND tenant_id = current_setting('presenton.tenant_id', true)"
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


@pytest.fixture(scope="module")
def svg_template_migration():
    spec = importlib.util.spec_from_file_location(
        "svg_template_rls_migration", SVG_TEMPLATE_MIGRATION_PATH
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


class _SchemaRecordingOp(_RecordingOp):
    """Also accepts the DDL helpers a table-creating revision calls.

    Only raw SQL is recorded; table and index creation are not under test.
    """

    def create_table(self, *_args, **_kwargs) -> None:
        pass

    def create_index(self, *_args, **_kwargs) -> None:
        pass

    def drop_table(self, *_args, **_kwargs) -> None:
        pass


def _run(
    module, direction: str, dialect: str, recorder_type: type = _RecordingOp
) -> list[str]:
    recorder = recorder_type(dialect)
    original = module.op
    module.op = recorder
    try:
        getattr(module, direction)()
    finally:
        module.op = original
    return recorder.statements


def test_owner_rls_revision_is_on_the_single_linear_head(migration_module) -> None:
    """One head, and this revision on its ancestry.

    It was the tip when it landed; a later revision may sit on top of it, and
    that is fine. What must not happen is a second head -- two migrations that
    both claim the same parent -- because Alembic then refuses to upgrade at
    all. So the head count is pinned, not this revision's position in it.
    """

    assert migration_module.revision == revision_contract.REVISION_OWNER_ROW_LEVEL_SECURITY
    assert migration_module.down_revision == (
        revision_contract.REVISION_COM_LEASE_TOKEN_VERIFIER_WIDTH
    )
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert script.get_heads() == [revision_contract.HEAD_REVISION]
    ancestry = {
        entry.revision
        for entry in script.iterate_revisions(revision_contract.HEAD_REVISION, "base")
    }
    assert migration_module.revision in ancestry


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
        # Upgrade to THIS revision rather than to head: the round trip under
        # test is this migration's own, and a revision that lands on top of it
        # later must not change what is being measured here.
        command.upgrade(config, revision_contract.REVISION_OWNER_ROW_LEVEL_SECURITY)
        assert _version() == revision_contract.REVISION_OWNER_ROW_LEVEL_SECURITY
        command.downgrade(config, "-1")
        assert _version() == revision_contract.REVISION_COM_LEASE_TOKEN_VERIFIER_WIDTH
        command.upgrade(config, "head")
        assert _version() == revision_contract.HEAD_REVISION
    finally:
        engine.dispose()


def test_owner_rls_policy_set_covers_every_owner_scoped_table(
    migration_module, svg_template_migration
) -> None:
    # ``template_v2`` had a policy from this migration but the table itself
    # was dropped by f432fc854225 on 2026-09-25; it can never reappear in the
    # live registry, so it is excluded here rather than from the frozen
    # historical migration set.
    tables_removed_after_this_migration = {"template_v2"}
    frozen_policy = (
        set(migration_module.policy_tables()) - tables_removed_after_this_migration
    )
    frozen_tenant = set(migration_module._TENANT_SCOPED_TABLES)
    assert frozen_policy.isdisjoint(frozen_tenant)
    # The tenant-scoped public tables were forced by ff2a6c8e0b14 with a
    # stricter predicate; every other owner-scoped table gets the owner policy
    # here.  ``owner_scoped_table_names`` is the full ORM-guarded set, so a
    # model added later fails this until a follow-up migration covers it --
    # which is how ``template_v2`` was found missing.
    #
    # The SVG user-template tables are such later models.  They are owner AND
    # tenant scoped, so d8f0a2b4c6e9 gave them the strict tenant+owner policy
    # when it created them (pinned by
    # ``test_svg_template_tables_carry_the_strict_tenant_owner_policy``).
    # They must not also receive the owner policy: permissive policies are
    # OR-ed, and the owner predicate would admit cross-tenant reads, admin
    # reads and identity-less worker reads that the strict one refuses.
    svg_tenant = set(svg_template_migration.TABLES)
    assert svg_tenant.isdisjoint(frozen_policy | frozen_tenant)
    assert frozen_tenant | svg_tenant == set(tenant_scoped_table_names())
    assert frozen_policy | frozen_tenant | svg_tenant == set(
        owner_scoped_table_names()
    )
    assert (
        set(migration_module._CATALOG_SHARED_TABLES) - tables_removed_after_this_migration
    ) == set(catalog_shared_table_names())


def test_svg_template_tables_carry_the_strict_tenant_owner_policy(
    svg_template_migration,
) -> None:
    """Forced RLS, the exact tenant+owner predicate, and nothing wider."""

    statements = _run(
        svg_template_migration, "upgrade", "postgresql", _SchemaRecordingOp
    )
    for table in svg_template_migration.TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in statements
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in statements
        policies = [
            statement
            for statement in statements
            if statement.startswith("CREATE POLICY")
            and f' ON "{table}" ' in statement
        ]
        assert sorted(policies) == sorted(
            [
                f'CREATE POLICY svg_template_principal ON "{table}" FOR ALL '
                f"USING ({EXPECTED_SVG_TEMPLATE_PREDICATE}) "
                f"WITH CHECK ({EXPECTED_SVG_TEMPLATE_PREDICATE})",
                f'CREATE POLICY svg_template_controller ON "{table}" FOR ALL '
                "TO presenton_public_controller USING (true) WITH CHECK (true)",
            ]
        )
    assert not any(
        "presenton_owner_row_visible" in statement for statement in statements
    )
    # SQLite has no row-level security: only the schema DDL runs there.
    assert (
        _run(svg_template_migration, "upgrade", "sqlite", _SchemaRecordingOp) == []
    )


def test_owner_rls_predicate_is_the_exact_visibility_call(
    migration_module,
) -> None:
    """Spelled out, so an inverted or renamed predicate cannot pass."""

    statements = _run(migration_module, "upgrade", "postgresql")
    for table in migration_module._OWNER_TABLES:
        assert migration_module.policy_predicate(table) == EXPECTED_OWNER_PREDICATE
    for table in migration_module._CATALOG_SHARED_TABLES:
        assert migration_module.policy_predicate(table) == EXPECTED_SHARED_PREDICATE
    for table in migration_module.policy_tables():
        predicate = (
            EXPECTED_SHARED_PREDICATE
            if table in migration_module._CATALOG_SHARED_TABLES
            else EXPECTED_OWNER_PREDICATE
        )
        assert (
            f'CREATE POLICY {migration_module._REQUEST_POLICY} ON "{table}" '
            f"FOR ALL USING ({predicate}) WITH CHECK ({predicate})"
        ) in statements


def test_owner_rls_visibility_function_body_is_pinned(migration_module) -> None:
    """The whole decision is one SECURITY DEFINER function; pin its semantics.

    A renamed setting, a flipped comparison, a dropped RAISE or a silently
    widened admin test all change this body.
    """

    statements = _run(migration_module, "upgrade", "postgresql")
    created = [
        statement
        for statement in statements
        if statement.lstrip().startswith("CREATE OR REPLACE FUNCTION")
    ]
    assert len(created) == 1
    assert " ".join(created[0].split()) == EXPECTED_VISIBILITY_FUNCTION
    # The querying role never needs SELECT on "user": only EXECUTE is granted.
    for role in ("presenton_public_api", "presenton_public_controller"):
        assert (
            "GRANT EXECUTE ON FUNCTION "
            f"public.presenton_owner_row_visible(uuid, boolean) TO {role}"
        ) in statements
    assert not any(
        "GRANT" in statement and '"user"' in statement for statement in statements
    )


    # Shared quota counters also require an owner-authorized fixed-query
    # boundary rather than the request principal's partial RLS view.
    quota_path = (
        FASTAPI_ROOT / "alembic" / "versions"
        / "f6c8e0a2b4d7_harden_public_quota_invariant_authority.py"
    )
    spec = importlib.util.spec_from_file_location("quota_authority_migration", quota_path)
    quota = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(quota)
    assert quota.revision == revision_contract.REVISION_PUBLIC_QUOTA_INVARIANT_AUTHORITY
    assert quota.down_revision == revision_contract.REVISION_SVG_HEADER_DESIGN_CHECKPOINTS
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert script.get_heads() == [revision_contract.HEAD_REVISION]
    assert quota.revision in {
        entry.revision
        for entry in script.iterate_revisions(revision_contract.HEAD_REVISION, "base")
    }
    statements = _run(quota, "upgrade", "postgresql")
    assert len(statements) == 3
    preflight, function, revoke = statements
    assert "p.proowner" in preflight
    assert "pg_catalog.pg_roles" in preflight
    assert "(r.rolsuper OR r.rolbypassrls)" in preflight
    assert "public_quota_invariant_owner_authority_required" in preflight
    assert "SECURITY DEFINER" in function
    assert "SET search_path = pg_catalog, pg_temp" in function
    assert "SET row_security = off" in function
    assert "public_quota_invariant_trigger_required" in function
    assert "TG_WHEN <> 'AFTER' OR TG_LEVEL <> 'ROW' OR TG_NARGS <> 0" in function
    assert "TG_OP NOT IN ('INSERT', 'UPDATE', 'DELETE')" in function
    for table, trigger in (
        ("public_resource_leases", "trg_public_resource_lease_counter_consistent"),
        ("public_quota_counters", "trg_public_quota_counter_consistent"),
    ):
        assert f"TG_RELID = 'public.{table}'::regclass" in function
        assert f"TG_NAME = '{trigger}'" in function
    assert revoke == (
        "REVOKE ALL ON FUNCTION public.public_validate_quota_counter_consistency() "
        "FROM PUBLIC, presenton_public_api, presenton_public_controller"
    )

    # Compare accounting with the frozen previous invariant: security changes
    # must preserve counts, resource units, lease-less admissions and scope joins.
    prior_path = (
        FASTAPI_ROOT / "alembic" / "versions"
        / "d4a7c9e1b3f6_count_lease_less_admissions_in_global_quota_invariant.py"
    )
    prior_spec = importlib.util.spec_from_file_location("quota_prior_migration", prior_path)
    prior = importlib.util.module_from_spec(prior_spec)
    prior_spec.loader.exec_module(prior)
    old = prior._consistency_function_sql(
        scope_types=prior._FIXED_SCOPE_TYPES, scope_match=prior._FIXED_SCOPE_MATCH
    )
    accounting_start = "IF TG_OP = 'DELETE' THEN"
    old_body = old[old.index(accounting_start):]
    new_body = function[function.index(accounting_start):]
    for table in ("public_resource_leases", "public_operation_admissions", "public_quota_counters"):
        new_body = new_body.replace(f"public.{table}", table)
    assert " ".join(new_body.split()) == " ".join(old_body.split())
    assert not any(
        token in "\n".join(statements)
        for token in ("ALTER ROLE", "CREATE ROLE", "ALTER TABLE", "POLICY", "OWNER TO")
    )
    assert _run(quota, "upgrade", "sqlite") == []
    assert _run(quota, "downgrade", "sqlite") == []
    assert _run(quota, "downgrade", "postgresql") == [
        "ALTER FUNCTION public.public_validate_quota_counter_consistency() SECURITY INVOKER",
        "ALTER FUNCTION public.public_validate_quota_counter_consistency() RESET ALL",
        "GRANT EXECUTE ON FUNCTION public.public_validate_quota_counter_consistency() TO PUBLIC",
    ]


def test_owner_rls_upgrade_emits_forced_policies_per_table(
    migration_module,
) -> None:
    statements = _run(migration_module, "upgrade", "postgresql")
    joined = "\n".join(statements)
    assert f"CREATE ROLE {migration_module._CONTROLLER_ROLE} NOLOGIN" in joined
    for table in migration_module.policy_tables():
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
    for table in migration_module.policy_tables():
        assert (
            f'DROP POLICY IF EXISTS {migration_module._REQUEST_POLICY} ON "{table}"'
        ) in statements
        assert (
            f'DROP POLICY IF EXISTS {migration_module._CONTROLLER_POLICY} '
            f'ON "{table}"'
        ) in statements
        assert f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY' in statements
        assert f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY' in statements
    assert (
        "DROP FUNCTION IF EXISTS public.presenton_owner_row_visible(uuid, boolean)"
    ) in statements


def _granted_tables(statements: list[str], verb: str) -> set[str]:
    return {
        statement.split('"')[1]
        for statement in statements
        if statement.startswith(f"{verb} SELECT, INSERT, UPDATE, DELETE ON TABLE ")
    }


def test_owner_rls_table_grants_are_symmetric(migration_module) -> None:
    """Downgrade must not strip privileges this migration never granted.

    a0d4f7c9e2b6 granted the controller DML ``ON ALL TABLES IN SCHEMA public``.
    Revoking per table on the way down left the controller worse off than
    before the upgrade and broke account deletion and the controller workers.
    """

    upgrade = _run(migration_module, "upgrade", "postgresql")
    downgrade = _run(migration_module, "downgrade", "postgresql")
    granted = _granted_tables(upgrade, "GRANT")
    revoked = _granted_tables(downgrade, "REVOKE")
    assert granted == revoked == set(migration_module._CONTROLLER_GRANT_TABLES)
    assert granted <= set(migration_module.policy_tables())


def test_controller_grant_set_is_exactly_the_post_blanket_grant_tables(
    migration_module, tmp_path
) -> None:
    """Derive the set from the schema instead of trusting the frozen tuple."""

    database_url = f"sqlite:///{(tmp_path / 'grant-parity.db').as_posix()}"
    config = _alembic_config(database_url)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, revision_contract.REVISION_ACCOUNT_DELETION_OUTBOX)
        before = set(inspect(engine).get_table_names())
        command.upgrade(config, revision_contract.REVISION_OWNER_ROW_LEVEL_SECURITY)
        after = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert set(migration_module.policy_tables()) <= after
    assert set(migration_module._CONTROLLER_GRANT_TABLES) == {
        table for table in migration_module.policy_tables() if table not in before
    }


def test_owner_rls_is_a_no_op_outside_postgresql(migration_module) -> None:
    assert _run(migration_module, "upgrade", "sqlite") == []
    assert _run(migration_module, "downgrade", "sqlite") == []
