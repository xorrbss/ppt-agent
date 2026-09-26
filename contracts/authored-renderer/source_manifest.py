"""First-party source files required by the isolated renderer worker."""

from __future__ import annotations

from pathlib import Path

from import_closure import compute_closure

FIRST_PARTY_ROOTS = frozenset({"utils", "services", "models", "enums", "constants"})


def source_files(fastapi_root: Path) -> tuple[str, ...]:
    entrypoint = fastapi_root / "utils" / "authored_renderer_worker.py"
    closure = compute_closure(entrypoint, fastapi_root, FIRST_PARTY_ROOTS)
    return tuple(sorted(closure.files))
