"""Regression test for the retained-reference repo-root resolution.

``_reference_path`` walks up from this module's own file location with
``Path(__file__).resolve().parents[N]`` to find the repository root, then
looks for retained-reference PPTX assets underneath it. An off-by-one in
``N`` silently resolves one directory above the repo root, so every
retained-reference lookup fails even when the asset is committed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.authored_vnext.generation.retained_reference import (
    DEVAX_RETAINED_REFERENCE_ID,
    _reference_path,
)


def test_unconfigured_reference_path_resolves_to_the_repository_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTHORED_RETAINED_REFERENCE_ROOT", raising=False)

    checked: list[Path] = []

    def _record_is_file(self: Path) -> bool:
        checked.append(self)
        return False

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "is_file", _record_is_file)
        result = _reference_path(DEVAX_RETAINED_REFERENCE_ID)

    assert result is None  # no asset checked out in this test environment
    assert len(checked) == 1
    candidate = checked[0]
    assert candidate.name == "reference.pptx"

    # candidate == repo_root / "assets" / "retained-references" / <skill> / "reference.pptx"
    repo_root = candidate.parents[3]

    # Assert against markers known to live at the repository root (per
    # CLAUDE.md's architecture section), not a hardcoded absolute path, so
    # the test travels with the repo regardless of checkout location.
    assert (repo_root / "docker-compose.yml").is_file()
    assert (repo_root / "package.json").is_file()
    assert (repo_root / "servers" / "fastapi").is_dir()
    assert (repo_root / "servers" / "nextjs").is_dir()
