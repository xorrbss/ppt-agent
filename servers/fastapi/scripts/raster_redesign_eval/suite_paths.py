"""Shared config/path resolution for the raster-redesign eval suite CLI
(run_suite.py and its submodules).

Every deck-specific path in suite_config.json (source_pptx, ground_truth,
source_pages_dir, fixtures_dir) is relative to a "decks root" directory, not
to this file's own location. That root defaults to the `decks/` folder
shipped next to this script (which only carries the non-regenerable deck1-3
fixtures -- see README.md "Rebuilding deck assets"), but can be pointed at
any external location via `--decks-root` or the RASTER_EVAL_DECKS_ROOT env
var. This is how a customer/large reference deck (kept out of this repo
entirely) is plugged in: add its own deck entry to a private config file
(passed with `--config`) and point `--decks-root` at wherever its real
fixtures live.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

SUITE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SUITE_DIR / "suite_config.json"
DECKS_ROOT_ENV_VAR = "RASTER_EVAL_DECKS_ROOT"


def default_decks_root() -> Path:
    env = os.environ.get(DECKS_ROOT_ENV_VAR)
    return Path(env) if env else (SUITE_DIR / "decks")


def add_decks_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--decks-root", default=None,
        help=f"directory that deck-relative paths in --config resolve against "
             f"(default: {DECKS_ROOT_ENV_VAR} env var, else the built-in decks/ folder)",
    )


def resolve_decks_root(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "decks_root", None)
    return Path(explicit) if explicit else default_decks_root()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_deck_path(decks_root: Path, maybe_relative: str) -> Path:
    p = Path(maybe_relative)
    if p.is_absolute():
        return p
    return (decks_root / p).resolve()


def deck_by_id(cfg: dict, deck_id: str) -> dict:
    for d in cfg["decks"]:
        if d["id"] == deck_id:
            return d
    raise KeyError(f"unknown deck id: {deck_id}")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def add_concurrent_harness_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--concurrent-harness", action=argparse.BooleanOptionalAction, default=None,
        help="override/supply the concurrent_harness flag for this run (default: read from "
             "RUN_DIR/run_config.json if `launch` wrote one there, else omitted)",
    )


def resolve_concurrent_harness(args: argparse.Namespace, run_dir: Path):
    """CLI override wins; otherwise reads `run_dir/run_config.json` (written
    by `launch`, see suite_launch.py); None when neither is available (a
    run this suite didn't itself launch/score with the flag known)."""
    explicit = getattr(args, "concurrent_harness", None)
    if explicit is not None:
        return explicit
    config_path = Path(run_dir) / "run_config.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8")).get("concurrent_harness")
    return None
