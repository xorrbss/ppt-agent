"""The migration runner must explain a 42501 failure without any per-revision flag."""

from pathlib import Path

import asyncpg
import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import ProgrammingError

from migration_privilege_diagnostics import (
    MIGRATION_INSUFFICIENT_PRIVILEGE_CODE,
    MigrationInsufficientPrivilegeError,
    MigrationPrivilegeWatch,
    build_message,
    find_insufficient_privilege_error,
)

FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    return config


def _wrapped_psycopg_error() -> ProgrammingError:
    driver_error = psycopg.errors.InsufficientPrivilege(
        "permission denied for schema public"
    )
    return ProgrammingError("ALTER FUNCTION ...", {}, driver_error)


def test_finds_psycopg_insufficient_privilege_through_sqlalchemy_wrapper():
    found = find_insufficient_privilege_error(_wrapped_psycopg_error())
    assert isinstance(found, psycopg.errors.InsufficientPrivilege)


def test_finds_asyncpg_insufficient_privilege_through_cause_chain():
    driver_error = asyncpg.exceptions.InsufficientPrivilegeError(
        "permission denied for schema public"
    )
    try:
        try:
            raise driver_error
        except asyncpg.exceptions.InsufficientPrivilegeError as inner:
            raise RuntimeError("migration failed") from inner
    except RuntimeError as outer:
        found = find_insufficient_privilege_error(outer)
    assert found is driver_error


def test_other_sqlstates_are_not_claimed():
    driver_error = psycopg.errors.UndefinedTable('relation "nope" does not exist')
    assert find_insufficient_privilege_error(driver_error) is None


def test_unrelated_failures_are_re_raised_untouched():
    watch = MigrationPrivilegeWatch(_alembic_config(), "sqlite:///:memory:")
    with pytest.raises(ValueError):
        with watch.explain_privilege_failures():
            raise ValueError("unrelated")


def test_privilege_failure_is_re_raised_as_a_diagnosis_not_a_skip():
    watch = MigrationPrivilegeWatch(_alembic_config(), "sqlite:///:memory:")
    with pytest.raises(MigrationInsufficientPrivilegeError) as failure:
        with watch.explain_privilege_failures():
            raise _wrapped_psycopg_error()
    message = str(failure.value)
    assert message.startswith(MIGRATION_INSUFFICIENT_PRIVILEGE_CODE)
    assert "permission denied for schema public" in message
    assert "bootstrap owner DATABASE_URL" in message
    assert failure.value.code == MIGRATION_INSUFFICIENT_PRIVILEGE_CODE


def test_failing_revision_is_the_first_unapplied_step():
    """No revision declares anything; the runner derives the step from the graph."""

    config = _alembic_config()
    script_directory = ScriptDirectory.from_config(config)
    head = script_directory.get_current_head()
    assert head is not None
    previous = script_directory.get_revision(head).down_revision
    assert isinstance(previous, str)

    watch = MigrationPrivilegeWatch(config, "sqlite:///:memory:")
    watch.record_starting_heads(_FakeMigrationContext((previous,)))
    assert watch.failing_revision() == head
    assert watch.last_applied is None


def test_applied_steps_move_the_failing_revision_forward():
    config = _alembic_config()
    script_directory = ScriptDirectory.from_config(config)
    head = script_directory.get_current_head()
    previous = script_directory.get_revision(head).down_revision

    watch = MigrationPrivilegeWatch(config, "sqlite:///:memory:")
    watch.record_starting_heads(_FakeMigrationContext((previous,)))
    watch.record_applied(ctx=None, step=_FakeStep(head), heads=None, run_args={})
    assert watch.last_applied == head
    assert watch.failing_revision() is None


def test_message_names_the_revision_the_role_and_the_missing_privilege():
    message = build_message(
        revision="d4a7c9e1b3f6",
        last_applied="c6e8a0f2b4d7",
        schema="public",
        facts={
            "role_name": "presenton_public_api",
            "database_name": "presenton",
            "schema_owner": "presenton_owner",
            "can_create": False,
            "can_use": True,
        },
        driver_message="permission denied for schema public",
    )
    assert "d4a7c9e1b3f6" in message
    assert "presenton_public_api" in message
    assert "CREATE=no" in message
    assert "presenton_owner" in message
    assert "c6e8a0f2b4d7" in message


class _FakeMigrationContext:
    def __init__(self, heads):
        self._heads = heads

    def get_current_heads(self):
        return self._heads


class _FakeStep:
    def __init__(self, revision):
        self.is_upgrade = True
        self.up_revision_id = revision
