"""Producer side of the PostgreSQL RLS contract.

The row-level security policies are only as good as the settings the ORM
publishes for them.  Nothing asserted that
``_set_postgres_transaction_principal`` emits ``presenton.request_scope`` at
all, so renaming or dropping it would have silently turned every owner policy
into a permissive one while the migration tests stayed green.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
import uuid

import pytest

from services.database_owner_scope import _set_postgres_transaction_principal
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_tenant_id,
    reset_request_scope_active,
    set_current_owner_id,
    set_current_tenant_id,
    set_request_scope_active,
)


EXPECTED_PRINCIPAL_SQL = (
    "SELECT "
    "set_config('presenton.owner_id', :owner_id, true), "
    "set_config('presenton.tenant_id', :tenant_id, true), "
    "set_config('presenton.request_scope', :request_scope, true)"
)


@pytest.fixture(autouse=True)
def _auth_enabled(monkeypatch):
    monkeypatch.delenv("DISABLE_AUTH", raising=False)


class _RecordingConnection:
    def __init__(self, dialect: str) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))


@contextlib.contextmanager
def _principal(owner_id, tenant_id, request_scope):
    owner_token = set_current_owner_id(owner_id)
    tenant_token = set_current_tenant_id(tenant_id)
    scope_token = set_request_scope_active(request_scope)
    try:
        yield
    finally:
        reset_request_scope_active(scope_token)
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


def _principal_parameters(connection: _RecordingConnection) -> dict:
    """Parameters of the statement that publishes the principal (other SET LOCAL stamps are ignored)."""
    return next(p for statement, p in connection.calls if statement == EXPECTED_PRINCIPAL_SQL)


def _emit(dialect: str) -> _RecordingConnection:
    connection = _RecordingConnection(dialect)
    _set_postgres_transaction_principal(None, None, connection)
    return connection


def test_principal_is_not_published_outside_postgresql() -> None:
    assert _emit("sqlite").calls == []


def test_request_publishes_owner_tenant_and_request_scope() -> None:
    owner_id = uuid.uuid4()
    with _principal(owner_id, "default", True):
        connection = _emit("postgresql")
    assert connection.calls[0][0] == "SET LOCAL row_security = on"
    assert connection.calls[1][0] == "SET LOCAL presenton.generation_lineage_writer_revision = 'e4b7c9d2a1f0'"
    parameters = _principal_parameters(connection)
    assert parameters == {
        "owner_id": str(owner_id),
        "tenant_id": "default",
        "request_scope": "on",
    }


def test_identity_less_request_still_declares_the_request_scope() -> None:
    """This is the state the policies must refuse rather than read openly."""

    with _principal(None, "default", True):
        parameters = _principal_parameters(_emit("postgresql"))
    assert parameters == {
        "owner_id": "",
        "tenant_id": "default",
        "request_scope": "on",
    }


def test_worker_context_publishes_an_empty_request_scope() -> None:
    with _principal(None, None, False):
        parameters = _principal_parameters(_emit("postgresql"))
    assert parameters == {"owner_id": "", "tenant_id": "", "request_scope": ""}


def test_disabled_auth_keeps_the_fail_open_profile(monkeypatch) -> None:
    monkeypatch.setenv("DISABLE_AUTH", "true")
    with _principal(None, "default", True):
        parameters = _principal_parameters(_emit("postgresql"))
    assert parameters["request_scope"] == ""
