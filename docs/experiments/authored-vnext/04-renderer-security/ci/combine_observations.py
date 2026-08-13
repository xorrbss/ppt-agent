#!/usr/bin/env python3
"""Combine isolated-container and real-Electron observations without weakening either."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


CONTAINER_SCHEMA = "authored-security-observation/v1"
ELECTRON_SCHEMA = "authored-electron-security-observation/v1"


class ObservationError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ObservationError(f"{path.name}: top level must be an object")
    return value


def combine(container: dict[str, Any], electron: dict[str, Any]) -> dict[str, Any]:
    if container.get("schema_version") != CONTAINER_SCHEMA:
        raise ObservationError("container observation schema is unsupported")
    if electron.get("schema_version") != ELECTRON_SCHEMA:
        raise ObservationError("Electron observation schema is unsupported")
    electron_controls = electron.get("controls")
    if not isinstance(electron_controls, dict):
        raise ObservationError("Electron controls must be an object")

    combined = dict(container)
    combined["run_id"] = f"{container.get('run_id', 'container')}-{electron.get('run_id', 'electron')}"[:64]
    combined["electron"] = dict(electron_controls)

    containment = combined.get("containment")
    execution = combined.get("execution")
    if not isinstance(containment, dict) or not isinstance(execution, dict):
        raise ObservationError("container execution and containment must be objects")
    containment["preload_disabled"] = electron_controls.get("preload_absent") is True
    containment["ipc_disabled"] = electron_controls.get("privileged_bridge_absent") is True

    electron_errors = electron.get("observer_errors")
    if not isinstance(electron_errors, list) or not all(
        isinstance(item, str) for item in electron_errors
    ):
        raise ObservationError("Electron observer_errors must be a string array")
    existing_errors = execution.get("observer_errors")
    if not isinstance(existing_errors, list):
        raise ObservationError("container observer_errors must be an array")
    execution["observer_errors"] = [*existing_errors, *electron_errors]
    return combined


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", type=Path, required=True)
    parser.add_argument("--electron", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        combined = combine(_load(args.container), _load(args.electron))
        _write_atomic(args.output, combined)
    except (OSError, json.JSONDecodeError, ObservationError) as exc:
        print(f"observation merge failed: {type(exc).__name__}")
        return 3
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
