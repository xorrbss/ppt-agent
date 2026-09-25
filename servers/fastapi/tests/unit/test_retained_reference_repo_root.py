"""Pin where ``_reference_path`` looks for the packaged OSB reference.

The retained OSB template is a deployable resource that lives BESIDE the
repository checkout, not inside it -- see the ``Frozen from
C:\\project\\PPT-agent\\osb-template-24-layouts.pptx`` note in
``retained_reference.py``. ``_reference_path`` therefore walks up to the
checkout's parent for that one candidate.

This test exists because the index was "corrected" to the repository root on
the assumption that the parent was an off-by-one; that change resolved to a
directory with no asset and turned three passing
``test_osb_retained_native_scene`` cases red. Assert the relationship in both
directions so neither index can be changed silently again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.authored_vnext.generation.retained_reference import (
    DEVAX_RETAINED_REFERENCE_ID,
    OSB_RETAINED_REFERENCE_ID,
    _reference_path,
)


def _candidates(
    monkeypatch: pytest.MonkeyPatch, retained_reference_id: str
) -> list[Path]:
    """Return every path ``_reference_path`` probes, without touching disk."""

    monkeypatch.delenv("AUTHORED_RETAINED_REFERENCE_ROOT", raising=False)
    seen: list[Path] = []

    def _record_is_file(self: Path) -> bool:
        seen.append(self)
        return False

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "is_file", _record_is_file)
        assert _reference_path(retained_reference_id) is None
    return seen


def _repository_root() -> Path:
    """Locate the checkout by its own markers, never a hardcoded path."""

    root = Path(__file__).resolve()
    while root != root.parent:
        if (root / "docker-compose.yml").is_file() and (
            root / "servers" / "fastapi"
        ).is_dir():
            return root
        root = root.parent
    raise AssertionError("repository root not found from the test file")


def test_the_osb_reference_is_probed_beside_the_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository_root()
    probed = _candidates(monkeypatch, OSB_RETAINED_REFERENCE_ID)

    packaged = next(
        path for path in probed if path.name == "osb-template-24-layouts.pptx"
    )
    assert packaged.parent == repo_root.parent, (
        "the packaged OSB template is deployed next to the checkout; "
        "resolving it inside the checkout finds nothing"
    )
    assert packaged.parent != repo_root


def test_the_assets_tree_is_probed_inside_the_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository_root()
    probed = _candidates(monkeypatch, DEVAX_RETAINED_REFERENCE_ID)

    assert len(probed) == 1
    candidate = probed[0]
    assert candidate.name == "reference.pptx"
    # candidate == <root>/assets/retained-references/<skill>/reference.pptx
    assert candidate.parents[3] == repo_root.parent
