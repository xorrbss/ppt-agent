from __future__ import annotations

import pytest
from starlette.requests import Request

from utils import simple_auth


def _request(username: str | None = None) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/"})
    if username is not None:
        request.state.auth_username = username
    return request


def test_owner_scope_is_stable_private_and_separated_by_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(simple_auth, "is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(
        simple_auth,
        "_load_user_config",
        lambda: {"AUTH_SECRET_KEY": "private-signing-secret"},
    )

    alice = simple_auth.get_request_owner_scope(_request("alice@example.test"))
    repeated = simple_auth.get_request_owner_scope(
        _request("alice@example.test")
    )
    bob = simple_auth.get_request_owner_scope(_request("bob@example.test"))

    assert alice == repeated
    assert alice != bob
    assert len(alice) == 64
    assert "alice" not in alice
    assert "private-signing-secret" not in alice


def test_owner_scope_rotates_with_auth_secret(monkeypatch) -> None:
    monkeypatch.setattr(simple_auth, "is_disable_auth_enabled", lambda: False)
    config = {"AUTH_SECRET_KEY": "secret-a"}
    monkeypatch.setattr(simple_auth, "_load_user_config", lambda: config)

    first = simple_auth.get_request_owner_scope(_request("owner"))
    config["AUTH_SECRET_KEY"] = "secret-b"
    second = simple_auth.get_request_owner_scope(_request("owner"))

    assert first != second


def test_owner_scope_requires_authenticated_identity_and_secret(
    monkeypatch,
) -> None:
    monkeypatch.setattr(simple_auth, "is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(
        simple_auth,
        "_load_user_config",
        lambda: {"AUTH_SECRET_KEY": "secret"},
    )
    with pytest.raises(ValueError, match="identity"):
        simple_auth.get_request_owner_scope(_request())

    monkeypatch.setattr(simple_auth, "_load_user_config", lambda: {})
    with pytest.raises(ValueError, match="secret"):
        simple_auth.get_request_owner_scope(_request("owner"))


def test_disabled_auth_uses_one_fixed_local_scope(monkeypatch) -> None:
    monkeypatch.setattr(simple_auth, "is_disable_auth_enabled", lambda: True)
    monkeypatch.setattr(
        simple_auth,
        "_load_user_config",
        lambda: pytest.fail("disabled auth must not read a signing secret"),
    )

    assert (
        simple_auth.get_request_owner_scope(_request())
        == simple_auth.DISABLED_AUTH_OWNER_SCOPE
    )
    assert (
        simple_auth.get_request_owner_scope(_request("ignored"))
        == simple_auth.DISABLED_AUTH_OWNER_SCOPE
    )
