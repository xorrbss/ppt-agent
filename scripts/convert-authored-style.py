#!/usr/bin/env python3
"""CLI for converting NotebookLM prompt styles into authored styles."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = REPOSITORY_ROOT / "servers" / "fastapi"
sys.path.insert(0, str(FASTAPI_ROOT))

from utils.authored_style_converter import ConversionError, convert_path  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NotebookLM 스타일 YAML을 PPT-agent Authored 스타일 YAML로 변환합니다."
    )
    parser.add_argument("input", type=Path, help="입력 .yaml/.yml 파일 또는 디렉터리")
    parser.add_argument(
        "-o", "--output", type=Path, required=True, help="출력 .yaml 파일 또는 디렉터리"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="동일한 출력 파일이 이미 있으면 교체"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="검증과 변환 계획만 수행하고 파일은 기록하지 않음",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        results = convert_path(
            args.input, args.output, overwrite=args.overwrite, dry_run=args.dry_run
        )
    except (ConversionError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    action = "검증" if args.dry_run else "변환"
    for result in results:
        print(f"{action}: {result.source} -> {result.target} ({result.style_id})")
    print(f"총 {len(results)}개 스타일 {action} 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
