"""Collect the real CI pytest baseline without turning test failures into success."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def summarize(report: Path, exit_code: int) -> dict:
    result = {
        "schemaVersion": "fastapi-pytest-baseline/v1",
        "sourceCommit": os.environ.get("BITBUCKET_COMMIT"),
        "pytestExitCode": exit_code,
        "state": "infrastructure_error",
        "tests": 0,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "failedTests": [],
    }
    try:
        cases = list(ET.parse(report).getroot().iter("testcase"))
    except (OSError, ET.ParseError) as error:
        result["reportError"] = type(error).__name__
        return result
    result["tests"] = len(cases)
    for case in cases:
        if case.find("error") is not None:
            status = "errors"
        elif case.find("failure") is not None:
            status = "failures"
        elif case.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        result[status] += 1
        if status in {"errors", "failures"}:
            result["failedTests"].append({
                "class": case.get("classname", ""),
                "name": case.get("name", ""),
                "status": status,
            })
    if exit_code in {0, 1} and cases:
        result["state"] = (
            "passed" if exit_code == 0 and not result["failures"] and not result["errors"]
            else "test_failures"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summarize-only", type=Path)
    parser.add_argument("--pytest-exit-code", type=int, default=0)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = args.summarize_only or output / "pytest.xml"
    exit_code = args.pytest_exit_code
    if args.summarize_only is None:
        # A missing/stale report must never certify a later failed invocation.
        if report.exists():
            parser.error("output already contains pytest.xml; choose a fresh output directory")
        backend = Path(__file__).resolve().parents[1] / "servers" / "fastapi"
        try:
            exit_code = subprocess.run(
                [sys.executable, "-m", "pytest", "--junitxml", str(report)],
                cwd=backend,
                check=False,
            ).returncode
        except OSError:
            exit_code = 3
    summary = summarize(report, exit_code)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "failedTests"}))
    if summary["state"] == "infrastructure_error":
        return exit_code if exit_code > 1 else 3
    return 0 if summary["state"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
