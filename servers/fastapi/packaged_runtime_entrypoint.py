"""Frozen entry point shared by the packaged API and workspace worker binaries."""

from __future__ import annotations

from pathlib import Path
import sys


API_EXECUTABLE_STEM = "fastapi"
WORKER_EXECUTABLE_STEM = "workspace-operation-worker"


def resolve_packaged_runtime_role(executable: str | Path) -> str:
    stem = Path(executable).stem.lower()
    if stem == API_EXECUTABLE_STEM:
        return "api"
    if stem == WORKER_EXECUTABLE_STEM:
        return "worker"
    raise RuntimeError(f"packaged_runtime_executable_unknown:{stem}")


def run_api() -> None:
    from server import load_repository_environment, parse_args, run_server

    load_repository_environment()
    from api.main import app  # noqa: F401  fail fast on an unimportable app

    run_server(parse_args())


def run_worker() -> None:
    from services.workspace_operation_worker import main

    main()


def main(executable: str | Path | None = None) -> None:
    role = resolve_packaged_runtime_role(executable or sys.executable)
    if role == "worker":
        run_worker()
        return
    run_api()


if __name__ == "__main__":
    main()
