#!/usr/bin/env python3
"""Build a validated Authored style YAML draft from one PPTX or PDF."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = REPOSITORY_ROOT / "servers" / "fastapi"
sys.path.insert(0, str(FASTAPI_ROOT))

from utils.artifact_style_analysis import ArtifactAnalysisError  # noqa: E402
from utils.artifact_style_builder import build_authored_style  # noqa: E402
from utils.authored_style_converter import ConversionError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze one PPTX/PDF offline and build a loader-validated PPT-agent "
            "Authored style YAML draft."
        )
    )
    parser.add_argument("input", type=Path, help="input .pptx or .pdf file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="output .yaml file or directory",
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        help="optionally save deterministic analysis JSON (not written during --dry-run)",
    )
    parser.add_argument(
        "--id",
        dest="style_id",
        help="optional style id; the existing converter enforces identifier safety",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing requested output files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="analyze, normalize, and loader-validate without writing files",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = build_authored_style(
            args.input,
            args.output,
            analysis_output=args.analysis_output,
            style_id=args.style_id,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except (ArtifactAnalysisError, ConversionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    action = "validated" if args.dry_run else "created"
    print(
        f"{action}: {result.conversion.source} -> {result.conversion.target} "
        f"({result.conversion.style_id})"
    )
    if result.analysis_target is not None:
        analysis_action = "planned" if args.dry_run else "created"
        print(f"analysis {analysis_action}: {result.analysis_target}")
    warning_count = len(result.analysis.get("warnings", []))
    print(f"analysis warnings: {warning_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
