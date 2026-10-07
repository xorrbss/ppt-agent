import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.database as database
from services.database import validate_public_database_authority
from services.public_operation_admission import public_prometheus_metrics_text
from utils.db_utils import resolve_runtime_database_url


def test_public_database_roles_are_process_separated_and_never_bypass_rls() -> None:
    request_role = {
        "role_name": "presenton_public_api_login",
        "is_superuser": False,
        "bypass_rls": False,
        "inherits_role_privileges": True,
        "session_is_superuser": False,
        "session_bypass_rls": False,
        "session_inherits_role_privileges": True,
        "controller_group_is_superuser": False,
        "controller_group_bypass_rls": False,
        "controller_group_can_login": False,
        "api_group_is_superuser": False,
        "api_group_bypass_rls": False,
        "api_group_can_login": False,
        "is_controller": False,
        "is_api": True,
    }
    controller_role = {
        "role_name": "presenton_public_controller_login",
        "is_superuser": False,
        "bypass_rls": False,
        "inherits_role_privileges": True,
        "session_is_superuser": False,
        "session_bypass_rls": False,
        "session_inherits_role_privileges": True,
        "controller_group_is_superuser": False,
        "controller_group_bypass_rls": False,
        "controller_group_can_login": False,
        "api_group_is_superuser": False,
        "api_group_bypass_rls": False,
        "api_group_can_login": False,
        "is_controller": True,
        "is_api": False,
    }

    validate_public_database_authority(request_role, controller=False)
    validate_public_database_authority(controller_role, controller=True)

    with pytest.raises(RuntimeError, match="public_api_database_role_must_not_be_controller"):
        validate_public_database_authority(controller_role, controller=False)
    with pytest.raises(RuntimeError, match="public_controller_database_role_required"):
        validate_public_database_authority(request_role, controller=True)
    with pytest.raises(RuntimeError, match="public_database_role_bypasses_rls"):
        validate_public_database_authority(
            {**request_role, "is_superuser": True},
            controller=False,
        )
    with pytest.raises(RuntimeError, match="public_database_role_bypasses_rls"):
        validate_public_database_authority(
            {**controller_role, "bypass_rls": True},
            controller=True,
        )
    with pytest.raises(RuntimeError, match="public_database_role_bypasses_rls"):
        validate_public_database_authority(
            {**controller_role, "controller_group_bypass_rls": True},
            controller=True,
        )
    with pytest.raises(RuntimeError, match="public_database_group_role_must_not_login"):
        validate_public_database_authority(
            {**request_role, "api_group_can_login": True},
            controller=False,
        )
    with pytest.raises(RuntimeError, match="public_database_role_must_inherit"):
        validate_public_database_authority(
            {**controller_role, "inherits_role_privileges": False},
            controller=True,
        )


def test_owned_postgres_strict_api_authority_is_not_skipped(monkeypatch) -> None:
    authority = {
        "role_name": "presenton_public_api_login",
        "is_superuser": False,
        "bypass_rls": False,
        "inherits_role_privileges": True,
        "session_is_superuser": False,
        "session_bypass_rls": False,
        "session_inherits_role_privileges": True,
        "controller_group_is_superuser": False,
        "controller_group_bypass_rls": False,
        "controller_group_can_login": False,
        "api_group_is_superuser": False,
        "api_group_bypass_rls": False,
        "api_group_can_login": False,
        "is_controller": False,
        "is_api": True,
    }
    probe = AsyncMock(return_value=authority)
    # The engines below are role-authority stand-ins with no connection; the
    # schema-revision half of the gate is exercised on real databases in
    # ``test_database_revision_authority.py``.
    monkeypatch.setattr(
        database, "require_database_schema_revision_authority", AsyncMock()
    )
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_DATABASE_PROCESS_ROLE", "api")
    monkeypatch.setenv("PUBLIC_V2_INTEGRATION_PROFILE", "postgresql-strict")
    monkeypatch.setattr(database, "_postgres_role_authority", probe)

    postgres = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    asyncio.run(database.require_public_api_database_authority(postgres))
    probe.assert_awaited_once_with(postgres)

    probe.reset_mock()
    sqlite = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    asyncio.run(database.require_public_api_database_authority(sqlite))
    probe.assert_not_awaited()


def test_public_database_urls_are_role_scoped_and_production_is_explicit() -> None:
    api_url = "postgresql://api-login:secret@db.invalid/presenton"
    controller_url = "postgresql://controller-login:secret@db.invalid/presenton"

    assert resolve_runtime_database_url(
        {
            "ENVIRONMENT": "production",
            "PUBLIC_DATABASE_PROCESS_ROLE": "api",
            "PUBLIC_API_DATABASE_URL": api_url,
            "MIGRATE_DATABASE_ON_STARTUP": "false",
        }
    ) == api_url
    assert resolve_runtime_database_url(
        {
            "ENVIRONMENT": "production",
            "PUBLIC_DATABASE_PROCESS_ROLE": "controller",
            "PUBLIC_CONTROLLER_DATABASE_URL": controller_url,
            "MIGRATE_DATABASE_ON_STARTUP": "false",
        }
    ) == controller_url

    with pytest.raises(RuntimeError, match="public_environment_required"):
        resolve_runtime_database_url({"PPT_PUBLIC_OIDC_ISSUER": "https://issuer"})
    with pytest.raises(RuntimeError, match="opposite_role"):
        resolve_runtime_database_url(
            {
                "ENVIRONMENT": "production",
                "PUBLIC_DATABASE_PROCESS_ROLE": "api",
                "PUBLIC_API_DATABASE_URL": api_url,
                "PUBLIC_CONTROLLER_DATABASE_URL": controller_url,
            }
        )
    with pytest.raises(RuntimeError, match="runtime_database_migrations_forbidden"):
        resolve_runtime_database_url(
            {
                "ENVIRONMENT": "production",
                "PUBLIC_DATABASE_PROCESS_ROLE": "controller",
                "PUBLIC_CONTROLLER_DATABASE_URL": controller_url,
                "MIGRATE_DATABASE_ON_STARTUP": "true",
            }
        )

    # The migration entrypoint must reject fallback before executing Alembic.
    import os
    import sys
    from unittest.mock import Mock, patch
    from scripts.run_public_v2_rc_migrations import main as migrate_public_rc

    migration = Mock()
    migration_environment = {
        "ENVIRONMENT": "production",
        "PUBLIC_DATABASE_PROCESS_ROLE": "migration",
        "PUBLIC_MIGRATION_DATABASE_URL": "postgresql://migration:fixture@localhost/fixture",
        "MIGRATE_DATABASE_ON_STARTUP": "false",
    }
    with patch.dict(sys.modules, {"migrations": SimpleNamespace(_run_migrations=migration)}):
        with patch.dict(os.environ, migration_environment, clear=True):
            migrate_public_rc()
        migration.assert_called_once_with()
        for invalid in (
            {},
            {**migration_environment, "PUBLIC_DATABASE_PROCESS_ROLE": "api"},
            {**migration_environment, "PUBLIC_MIGRATION_DATABASE_URL": ""},
            {**migration_environment, "PUBLIC_MIGRATION_DATABASE_URL": "sqlite:///fallback.db"},
            {**migration_environment, "DATABASE_URL": migration_environment["PUBLIC_MIGRATION_DATABASE_URL"]},
            {**migration_environment, "PUBLIC_API_DATABASE_URL": "postgresql://api:fixture@localhost/fixture"},
            {**migration_environment, "ENVIRONMENT": "development"},
        ):
            with patch.dict(os.environ, invalid, clear=True), pytest.raises(RuntimeError):
                migrate_public_rc()
            migration.assert_called_once_with()


def test_api_role_never_reports_force_rls_partial_rows_as_global_metrics(
    monkeypatch,
) -> None:
    class ApiRoleSession:
        bind = object()

        async def execute(self, _statement):
            raise AssertionError("API-role aggregate query must not execute")

    async def reject_api_role(_engine):
        raise RuntimeError("public_controller_database_process_role_required")

    monkeypatch.setattr(
        database,
        "require_public_controller_database_authority",
        reject_api_role,
    )
    assert asyncio.run(public_prometheus_metrics_text(ApiRoleSession())) == (
        "presenton_public_metrics_available 0\n"
    )
