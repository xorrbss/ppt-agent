"""Metric epochs for the canvas bench.

A page flag rate is comparable only between decks measured by the same
flagging rules.  The epoch is a label stored in each receipt (``metricEpoch``)
and pools never mix epochs.

Epochs, oldest first:

* ``LEGACY_EPOCH``: svg ``text_overlap`` measured on CSS line boxes.  Every
  receipt without a ``metricEpoch`` (the pilot and the sq-uw rounds) is this.
* ``INK_EPOCH``: ``text_overlap`` measured on glyph ink boxes.  That fix records
  ``overlap_basis`` in the svg measurements and does not bump
  ``SVG_RENDERER_VERSION``, so the renderer version cannot tell the epochs apart.

Per-deck precedence: explicit --metric-epoch, operation result / persisted
page overlap_basis.version, then git ancestry (with a warning). Every page
version must agree; mixed or partial evidence leaves the primary metric
unmeasured. A run without a fallback starts unmeasured until page evidence
resolves its epoch, so dirty/detached backends need no ancestry guess.
"""

from __future__ import annotations

import subprocess
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LEGACY_EPOCH = "svg-overlap-linebox-v1"
INK_EPOCH = "svg-overlap-ink-v1"
REPO_ROOT = Path(__file__).resolve().parents[3]


def receipt_epoch(receipt: Mapping[str, Any]) -> str:
    return str(receipt.get("metricEpoch") or LEGACY_EPOCH)


def find_overlap_basis(payload: Any, _depth: int = 0) -> str | None:
    """First ``overlap_basis`` string anywhere in a published operation result."""

    if _depth > 8:
        return None
    if isinstance(payload, Mapping):
        value = payload.get("overlap_basis", payload.get("overlapBasis"))
        if isinstance(value, Mapping):
            value = value.get("version")
        if isinstance(value, str) and value:
            return value
        children: Any = payload.values()
    elif isinstance(payload, list):
        children = payload
    else:
        return None
    for child in children:
        found = find_overlap_basis(child, _depth + 1)
        if found:
            return found
    return None


def observed_epoch(overlap_basis: str | Mapping[str, Any] | None) -> str | None:
    if isinstance(overlap_basis, Mapping):
        overlap_basis = overlap_basis.get("version")
    if not overlap_basis:
        return None
    return INK_EPOCH if "ink" in overlap_basis.lower() else LEGACY_EPOCH


def is_ancestor(ancestor: str, commit: str, repo: Path = REPO_ROOT) -> bool | None:
    """True/False from git, ``None`` when git cannot tell (unknown sha, no git)."""

    try:
        done = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, commit], cwd=repo,
                              capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(done.returncode)


def resolve_run_epoch(explicit: str | None, backend_commit: str | None, ink_fix_commit: str | None) -> str:
    """The epoch a run labels its decks with, or a refusal naming what is missing."""

    if explicit:
        return explicit
    if backend_commit and ink_fix_commit:
        verdict = is_ancestor(ink_fix_commit, backend_commit)
        if verdict is not None:
            warnings.warn("metric epoch uses git ancestry fallback; prefer per-page overlap_basis evidence",
                          RuntimeWarning, stacklevel=2)
            return INK_EPOCH if verdict else LEGACY_EPOCH
    raise ValueError(
        "cannot determine the metric epoch: pass --metric-epoch, or both --backend-commit and "
        "--ink-fix-commit (a commit both known to git)"
    )


def deck_overlap_basis(result: Any, presentation: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Check every persisted page, even when an operation also advertises a basis."""
    pages = ((presentation.get("theme") or {}).get("svg") or {}).get("quality") or []
    versions = [find_overlap_basis(page) for page in pages]
    measured = {version for version in versions if version}
    if len(measured) > 1:
        return {"version": None, "pages": len(pages), "versions": sorted(measured)}, "mixed_overlap_basis"
    if pages and len(measured) == 1 and any(version is None for version in versions):
        return {"version": None, "pages": len(pages)}, "missing_overlap_basis"
    version = find_overlap_basis(result) or next(iter(measured), None)
    if version and measured and version not in measured:
        return {"version": None, "pages": len(pages)}, "mixed_overlap_basis"
    return ({"version": version, "pages": len(pages)} if version else None), None
