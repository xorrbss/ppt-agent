"""A directly launched backend must see the repository-root ``.env``.

The repository keeps a single ``.env`` at its root for the backend, the proxy
and the frontend.  Nothing hands it to ``python server.py``, so a backend
started without it answers the chat-first capability probe with
``chat_first_runtime_profile_required`` and the UI silently falls back to the
legacy surface.  ``run-next-build.mjs`` already solves the same problem for the
client bundle; these tests pin the backend half of that contract.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


SERVER_PATH = Path(__file__).resolve().parents[2] / "server.py"


def _server_module():
    """Import ``server.py`` for its definitions only.

    The loader runs from the entry point, not at import time, so importing this
    module must not touch ``os.environ`` or pull in the FastAPI app.
    """

    specification = importlib.util.spec_from_file_location(
        "presenton_server_under_test", SERVER_PATH
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _load_repository_environment():
    return _server_module().load_repository_environment


@pytest.fixture
def restore_environment():
    original = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original)


def test_it_loads_the_repository_env_into_the_process(
    tmp_path: Path, restore_environment
):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# the runtime declaration that decides chat-first",
                "CHAT_FIRST_WORKSPACE_ENABLED=true",
                'CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE="sqlite-local"',
                "  SPACED_NAME = spaced value  ",
                "",
                "not an assignment",
            ]
        ),
        encoding="utf-8",
    )
    for name in (
        "CHAT_FIRST_WORKSPACE_ENABLED",
        "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE",
        "SPACED_NAME",
    ):
        os.environ.pop(name, None)

    loaded = _load_repository_environment()(tmp_path)

    assert loaded["CHAT_FIRST_WORKSPACE_ENABLED"] == "true"
    # Quoted values must arrive unquoted or the profile cross-check rejects them.
    assert loaded["CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"] == "sqlite-local"
    assert loaded["SPACED_NAME"] == "spaced value"
    assert os.environ["CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"] == "sqlite-local"
    assert "not an assignment" not in loaded


def test_an_existing_value_wins_over_the_file(tmp_path: Path, restore_environment):
    """Docker, Electron and explicit overrides must keep precedence."""

    (tmp_path / ".env").write_text(
        "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE=sqlite-local\n", encoding="utf-8"
    )
    os.environ["CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"] = "postgresql-cluster"

    loaded = _load_repository_environment()(tmp_path)

    assert "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE" not in loaded
    assert os.environ["CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"] == "postgresql-cluster"


def test_a_missing_env_file_is_not_an_error(tmp_path: Path, restore_environment):
    assert _load_repository_environment()(tmp_path / "absent") == {}


def test_importing_the_server_module_does_not_touch_the_environment():
    """Loading must happen at the entry point, never as an import side effect.

    ``test_server_cli.py`` executes this module to exercise the CLI; if the
    import applied the repository ``.env``, every later test in the session
    would inherit it and ambient-environment assertions elsewhere would flip.
    """

    before = dict(os.environ)
    _server_module()
    assert dict(os.environ) == before


def test_the_real_repository_env_reaches_the_chat_first_gate(restore_environment):
    """Pin the actual failure: the deployed .env must be able to host the workspace.

    Skipped when the checkout has no ``.env`` (CI), because the file is
    gitignored and carries provider credentials.

    The workspace is no longer behind a rollout flag -- every mutation goes
    through the operation store -- so the enable/disable pair this used to read
    is gone. What the deployed environment still has to satisfy is the runtime
    check that otherwise fails every write closed with 501.
    """

    repository_directory = SERVER_PATH.resolve().parents[2]
    if not (repository_directory / ".env").exists():
        pytest.skip("repository .env is not present in this checkout")

    os.environ.pop("DATABASE_URL", None)
    _load_repository_environment()(repository_directory)

    from services.workspace_runtime_profile import (
        require_supported_workspace_runtime,
    )

    # An unset DATABASE_URL leaves the app on its bundled SQLite file.
    profile = require_supported_workspace_runtime(
        os.environ.get("DATABASE_URL", "sqlite")
    )
    assert profile.supported, profile.dialect
