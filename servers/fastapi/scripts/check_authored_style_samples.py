"""Enforce the authored style sample storage contract (docs/authored-style-samples-design-20260928.md §4).

Checks both the runtime-validated rules (reused from
``utils.authored_style_samples.load_style_samples``) and the check-only rules:
file presence/shape, WebP magic bytes, pixel dimensions, byte caps, no stray
files, one shared canvas across all decks, digest equality (§5), and the
all-or-nothing rule (D2). A missing or empty samples root is not a problem
(D9): a partial rollout never exists.

Usage: ``uv run --directory servers/fastapi python scripts/check_authored_style_samples.py``
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FASTAPI_ROOT))

from PIL import Image  # noqa: E402

from utils.authored_style_catalogue import AUTHORED_STYLE_ALIASES, authored_style_catalogue  # noqa: E402
from utils.authored_style_samples import (  # noqa: E402
    AUTHORED_STYLE_SAMPLES_DIRECTORY,
    load_style_samples,
    style_render_digest,
)
from utils.authored_styles import (  # noqa: E402
    AUTHORED_STYLE_ASSETS_DIRECTORY,
    AUTHORED_STYLES_DIRECTORY,
    load_authored_styles,
)

_IGNORED_FILE_NAMES = frozenset({"Thumbs.db", ".DS_Store"})
_STAGE_BYTE_CAP = 96 * 1024
_THUMB_BYTE_CAP = 14 * 1024
_THUMB_LONG_EDGE = 320


class _WarningCapture(logging.Handler):
    """Collect the single warning ``load_style_samples`` logs on failure."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _load_with_capture(style_id: str, samples_root: Path):
    capture = _WarningCapture()
    logger = logging.getLogger("utils.authored_style_samples")
    logger.addHandler(capture)
    try:
        samples = load_style_samples(style_id, samples_root)
    finally:
        logger.removeHandler(capture)
    return samples, capture.messages


def _thumb_size(width: int, height: int) -> tuple[int, int]:
    if width >= height:
        return _THUMB_LONG_EDGE, round(height * _THUMB_LONG_EDGE / width)
    return round(width * _THUMB_LONG_EDGE / height), _THUMB_LONG_EDGE


def _check_image(path: Path, expected_size: tuple[int, int], byte_cap: int, label: str) -> Optional[str]:
    if path.is_symlink() or not path.is_file():
        return f"{label} is missing or not a regular file"
    payload = path.read_bytes()
    if not (len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"):
        return f"{label} does not start with the WebP magic bytes"
    if len(payload) > byte_cap:
        return f"{label} exceeds the {byte_cap} byte cap ({len(payload)} bytes)"
    try:
        with Image.open(path) as image:
            size = image.size
    except OSError:
        return f"{label} could not be opened as an image"
    if size != expected_size:
        return f"{label} pixel size {size} does not match expected {expected_size}"
    return None


def _check_style_directory(
    style_id: str,
    directory: Path,
    styles_directory: Path,
    assets_directory: Path,
) -> tuple[list[str], Optional[tuple[int, int]]]:
    problems: list[str] = []
    samples, messages = _load_with_capture(style_id, directory.parent)
    if samples is None:
        detail = messages[-1] if messages else "manifest failed runtime validation"
        problems.append(f"{style_id}: {detail}")
        return problems, None

    canvas = (samples.canvas.width, samples.canvas.height)
    thumb_size = _thumb_size(*canvas)
    expected_files = {"manifest.json"}
    for slide in samples.slides:
        stage_name = f"{slide.index:02d}.webp"
        thumb_name = f"{slide.index:02d}.thumb.webp"
        expected_files.update({stage_name, thumb_name})
        stage_error = _check_image(directory / stage_name, canvas, _STAGE_BYTE_CAP, f"{style_id}/{stage_name}")
        if stage_error:
            problems.append(stage_error)
        thumb_error = _check_image(directory / thumb_name, thumb_size, _THUMB_BYTE_CAP, f"{style_id}/{thumb_name}")
        if thumb_error:
            problems.append(thumb_error)

    for entry in directory.iterdir():
        if entry.name in _IGNORED_FILE_NAMES:
            continue
        if entry.name not in expected_files:
            problems.append(f"{style_id}: unexpected file '{entry.name}' in the sample directory")

    style_yaml = styles_directory / f"{style_id}.yaml"
    try:
        digest = style_render_digest(style_yaml, assets_directory)
    except (OSError, ValueError) as exc:
        problems.append(f"{style_id}: could not compute style digest ({exc})")
    else:
        if digest != samples.style_digest:
            problems.append(f"{style_id}: styleDigest is stale (style YAML or references changed since generation)")

    return problems, canvas


def _generator_commit_age(commit: str, repo_root: Path) -> Optional[int]:
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{commit}..HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def run_check(
    samples_root: Optional[Path] = None,
    styles_directory: Optional[Path] = None,
    assets_directory: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """Run every rule in §4/§5/D2 and return (problems, report_lines)."""
    samples_root = samples_root or AUTHORED_STYLE_SAMPLES_DIRECTORY
    styles_directory = styles_directory or AUTHORED_STYLES_DIRECTORY
    assets_directory = assets_directory or AUTHORED_STYLE_ASSETS_DIRECTORY
    repo_root = repo_root or FASTAPI_ROOT.parent.parent

    if not samples_root.is_dir():
        return [], []
    entries = sorted(p.name for p in samples_root.iterdir() if p.is_dir())
    if not entries:
        return [], []

    catalogue = authored_style_catalogue(load_authored_styles(styles_directory))
    canonical_ids = sorted(style["id"] for style in catalogue)

    problems: list[str] = []
    report: list[str] = []
    canvases: dict[tuple[int, int], list[str]] = {}

    for style_id in entries:
        if style_id in AUTHORED_STYLE_ALIASES or style_id not in canonical_ids:
            problems.append(f"{style_id}: sample directory is not a public canonical style")
            continue
        dir_problems, canvas = _check_style_directory(
            style_id, samples_root / style_id, styles_directory, assets_directory
        )
        problems.extend(dir_problems)
        if canvas is not None:
            canvases.setdefault(canvas, []).append(style_id)
            manifest = json.loads((samples_root / style_id / "manifest.json").read_text(encoding="utf-8"))
            commit = manifest.get("generatorCommit", "")
            age = _generator_commit_age(commit, repo_root)
            age_text = f"{age} commits since" if age is not None else "age unknown"
            report.append(f"{style_id}: generatorCommit={commit} ({age_text})")

    if len(canvases) > 1:
        for canvas, ids in sorted(canvases.items()):
            problems.append(f"{', '.join(ids)}: canvas {canvas} does not match the samples root's other decks")

    missing = sorted(set(canonical_ids) - set(entries))
    for style_id in missing:
        problems.append(f"{style_id}: missing sample directory (D2 requires every public canonical style)")

    return problems, report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-root", type=Path, default=None)
    parser.add_argument("--styles-dir", type=Path, default=None)
    parser.add_argument("--assets-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    problems, report = run_check(args.samples_root, args.styles_dir, args.assets_dir, args.repo_root)
    if not problems and not report:
        print("no authored style samples")
        return 0

    for problem in problems:
        print(problem)
    for line in report:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
