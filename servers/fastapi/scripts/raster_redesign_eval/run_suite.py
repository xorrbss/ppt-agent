"""Raster-redesign eval suite driver: launch product-path runs for the
decks configured in suite_config.json, poll their progress, score finished
outputs against ground truth, and compare two scored runs.

This script is stdlib-only EXCEPT for the `score`/`baseline`/`compare`
subcommands, which import python-pptx (from the fastapi .venv) and, for any
deck configured with `"checker": "checker"` (OCR-based fixtures -- see
README.md), PyMuPDF (`fitz`) via checker_pdf_report.py's check_pdf/
check_exactly_once. Run those subcommands as:

    cd <repo>\\servers\\fastapi
    uv run --with pymupdf python scripts\\raster_redesign_eval\\run_suite.py score --out RUN_DIR

`launch`/`status`/`_scheduler` are pure stdlib (matching generate_raster_deck.py)
and can be run with any python, including the plain venv python.

Subcommands: launch, status, score, baseline, compare, _scheduler (internal,
spawned by launch -- do not call directly).

This module is a thin facade over suite_launch.py, suite_status.py,
suite_score_cmd.py, suite_baseline_cmd.py, and suite_compare.py (plus
suite_paths.py for shared config/path resolution and deck_scoring.py /
wrong_number_filter.py for the scoring logic itself). Split out of a single
824-line run_suite.py (2026-09-25 eval suite) to keep each module under this
repo's file-size limit; behavior is unchanged except where noted in
README.md (decks-root / --map genericization).
"""
from __future__ import annotations

import argparse

from suite_baseline_cmd import add_baseline_subparser
from suite_compare import add_compare_subparser
from suite_launch import add_launch_subparser
from suite_score_cmd import add_score_subparser
from suite_status import add_status_subparser


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    add_launch_subparser(sub)
    add_status_subparser(sub)
    add_score_subparser(sub)
    add_baseline_subparser(sub)
    add_compare_subparser(sub)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
