"""Aggregate content-free worker admission shadow lines into a parity table."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

PREFIX = "authored_admission_shadow "


def aggregate(lines) -> Counter:
    counts = Counter()
    for line in lines:
        if PREFIX not in line:
            continue
        try:
            row = json.loads(line.split(PREFIX, 1)[1])
            counts[(row["producer"], row["existingVerdict"], row["gateVerdict"])] += 1
        except (ValueError, KeyError, TypeError):
            continue
    return counts


def markdown(counts: Counter) -> str:
    rows = ["| producer | existing verdict | gate verdict | count |", "| --- | --- | --- | ---: |"]
    rows.extend(
        f"| {producer} | {existing} | {gate} | {count} |"
        for (producer, existing, gate), count in sorted(counts.items())
    )
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.log.open(encoding="utf-8", errors="replace") as stream:
        report = markdown(aggregate(stream))
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
