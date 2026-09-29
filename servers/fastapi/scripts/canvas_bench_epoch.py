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

A run resolves its epoch with this precedence, and refuses to start when none
applies (a silently mislabelled deck would poison a pool):

1. ``--metric-epoch`` (explicit operator decision, e.g. for a dirty worktree);
2. per deck, an ``overlap_basis`` value found in the generation operation's
   published result (``observed_epoch``), which overrides 1 for that deck's
   label and is stored beside it as ``overlapBasis``;
3. ancestry: ``--ink-fix-commit <sha>`` is an ancestor of ``--backend-commit``
   (``git merge-base --is-ancestor``) means ``INK_EPOCH``, otherwise
   ``LEGACY_EPOCH``.
"""

from __future__ import annotations

import subprocess
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


def observed_epoch(overlap_basis: str | None) -> str | None:
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
            return INK_EPOCH if verdict else LEGACY_EPOCH
    raise ValueError(
        "cannot determine the metric epoch: pass --metric-epoch, or both --backend-commit and "
        "--ink-fix-commit (a commit both known to git)"
    )
