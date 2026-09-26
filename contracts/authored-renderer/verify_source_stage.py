"""Verify that the renderer build stage copies its exact import closure."""

from __future__ import annotations

import tempfile
from pathlib import Path

from import_closure import compute_closure
from source_manifest import FIRST_PARTY_ROOTS
from stage_sources import stage_sources


def verify_source_stage(
    dockerfile: str, image_sources: set[str], fastapi_root: Path
) -> tuple[bool, str]:
    closure = compute_closure(
        fastapi_root / "utils" / "authored_renderer_worker.py",
        fastapi_root,
        FIRST_PARTY_ROOTS,
    )
    expected = set(closure.files)
    with tempfile.TemporaryDirectory() as directory:
        staged_root = Path(directory) / "fastapi"
        staged_count = stage_sources(fastapi_root, staged_root)
        staged = {
            path.relative_to(staged_root).as_posix()
            for path in staged_root.rglob("*.py")
        }
        content_matches = all(
            (staged_root / relative).read_bytes()
            == (fastapi_root / relative).read_bytes()
            for relative in staged
        )
    missing_attestation = {
        relative for relative in expected
        if f"servers/fastapi/{relative}" not in image_sources
    }
    source_stage = dockerfile.split("AS authored-renderer-sources", 1)[1].split("\nFROM ", 1)[0]
    runtime_stage = dockerfile.split("AS authored-renderer-runtime", 1)[1].split("\nFROM ", 1)[0]
    valid = (
        staged == expected
        and staged_count == len(expected)
        and content_matches
        and not missing_attestation
        and "COPY servers/fastapi /src/servers/fastapi" in source_stage
        and "COPY contracts/authored-renderer/source_manifest.py" in source_stage
        and "COPY contracts/authored-renderer/stage_sources.py" in source_stage
        and "RUN python /tmp/renderer-source-manifest/stage_sources.py" in source_stage
        and "COPY --from=authored-renderer-sources /out/servers/fastapi /app/servers/fastapi"
        in runtime_stage
        and "COPY servers/fastapi /app/servers/fastapi" not in runtime_stage
    )
    return valid, (
        f"staged={len(staged)}, closure={len(expected)}, "
        f"missing attestation={len(missing_attestation)}"
    )
