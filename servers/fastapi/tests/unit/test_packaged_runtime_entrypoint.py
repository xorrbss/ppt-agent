from pathlib import Path
from unittest.mock import Mock

import pytest

import packaged_runtime_entrypoint as entrypoint


@pytest.mark.parametrize(
    ("executable", "role"),
    (
        (Path("C:/bundle/fastapi.exe"), "api"),
        (Path("/bundle/fastapi"), "api"),
        (Path("C:/bundle/workspace-operation-worker.exe"), "worker"),
        (Path("/bundle/workspace-operation-worker"), "worker"),
    ),
)
def test_packaged_runtime_role_is_bound_to_executable_name(executable, role) -> None:
    assert entrypoint.resolve_packaged_runtime_role(executable) == role


def test_packaged_runtime_rejects_unknown_executable_name() -> None:
    with pytest.raises(RuntimeError, match="packaged_runtime_executable_unknown"):
        entrypoint.resolve_packaged_runtime_role("presenton-helper.exe")


def test_packaged_worker_entrypoint_cannot_fall_through_to_api(monkeypatch) -> None:
    run_api = Mock()
    run_worker = Mock()
    monkeypatch.setattr(entrypoint, "run_api", run_api)
    monkeypatch.setattr(entrypoint, "run_worker", run_worker)

    entrypoint.main("workspace-operation-worker.exe")

    run_worker.assert_called_once_with()
    run_api.assert_not_called()
