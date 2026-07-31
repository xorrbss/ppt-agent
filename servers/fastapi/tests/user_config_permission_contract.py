"""Assertions for user-config file permission hardening across platforms."""

import os
import stat
from os import PathLike
from typing import Any


PRIVATE_USER_CONFIG_MODE = 0o600


def track_private_permission_requests(
    monkeypatch: Any, user_config_store: Any
) -> list[tuple[str, int]]:
    """Record the restrictive modes requested by the config store.

    Windows does not report POSIX file modes through ``stat``.  Recording the
    store's chmod request lets Windows tests still require the hardening action,
    while POSIX tests below require its effective 0600 result.
    """
    chmod_requests: list[tuple[str, int]] = []
    original_chmod = user_config_store.os.chmod

    def record_chmod(path: str | PathLike[str], mode: int) -> None:
        chmod_requests.append((os.fspath(path), mode))
        original_chmod(path, mode)

    monkeypatch.setattr(user_config_store.os, "chmod", record_chmod)
    return chmod_requests


def assert_private_user_config_file(
    file_path: str | PathLike[str], chmod_requests: list[tuple[str, int]]
) -> None:
    """Assert the cross-platform contract for a credential-bearing config file."""
    expected_request = (os.fspath(file_path), PRIVATE_USER_CONFIG_MODE)
    assert expected_request in chmod_requests

    if os.name != "nt":
        assert stat.S_IMODE(os.stat(file_path).st_mode) == PRIVATE_USER_CONFIG_MODE
