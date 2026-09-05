"""Filesystem discovery for the authored-run contact sheet: turning a run
directory (or a reference directory) into slide-index -> file mappings, and
deriving a short column label from a run's folder name.

``index_by_pattern`` and ``extract_run_label`` are pure regex helpers and are
exercised directly (including the "some indices are missing" case) by
``tests/unit/test_authored_run_contact_sheet.py``. The ``Path``/``os``
wrappers around them are thin glue, only exercised by the manual smoke run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SLIDE_PNG_RE = re.compile(r"^s(\d+)\.png$", re.IGNORECASE)
_SLIDE_METRICS_RE = re.compile(r"^s(\d+)\.metrics\.json$", re.IGNORECASE)
_RUN_LABEL_RE = re.compile(r"(run\d+)", re.IGNORECASE)


def index_by_pattern(filenames: list[str], pattern: re.Pattern) -> dict[int, str]:
    """Map slide index -> filename for every name matching ``pattern``.
    An index whose file is absent from ``filenames`` is simply absent from
    the result -- this is the "gap" a missing slide leaves behind."""
    result: dict[int, str] = {}
    for name in filenames:
        m = pattern.match(name)
        if m:
            result[int(m.group(1))] = name
    return result


def extract_run_label(name: str) -> str:
    """Pull a short label like ``run7`` out of a folder name; fall back to
    the whole name when there's no ``runNN`` token in it."""
    m = _RUN_LABEL_RE.search(name)
    return m.group(1).lower() if m else name


def derive_run_label(run_dir: Path) -> str:
    """The label for a ``--run`` column. Callers commonly point ``--run`` at
    the ``slides/`` directory itself (as the artifact layout has it), so look
    at the parent folder name in that case -- that's where ``runNN`` lives.
    """
    source = run_dir.parent.name if run_dir.name.lower() == "slides" else run_dir.name
    return extract_run_label(source)


def discover_run_dir(run_dir: Path) -> dict[int, tuple[Path | None, Path | None]]:
    """slide index -> (png_path or None, metrics_path or None) for one run
    directory. A run with PNGs but no ``.metrics.json`` files (e.g. an older
    fixture run) still comes back with every slide, metrics_path=None."""
    names = [p.name for p in run_dir.iterdir()] if run_dir.is_dir() else []
    png_by_idx = index_by_pattern(names, _SLIDE_PNG_RE)
    metrics_by_idx = index_by_pattern(names, _SLIDE_METRICS_RE)
    indices = set(png_by_idx) | set(metrics_by_idx)
    return {
        idx: (
            run_dir / png_by_idx[idx] if idx in png_by_idx else None,
            run_dir / metrics_by_idx[idx] if idx in metrics_by_idx else None,
        )
        for idx in indices
    }


def discover_reference_dir(reference_dir: Path) -> dict[int, Path]:
    """Reference PNGs in name order, indexed 0..N-1 -- the reference set
    isn't assumed to follow the ``sNN.png`` naming convention."""
    if not reference_dir.is_dir():
        return {}
    names = sorted(p.name for p in reference_dir.iterdir() if p.suffix.lower() == ".png")
    return {i: reference_dir / name for i, name in enumerate(names)}


def load_metrics_json(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)
