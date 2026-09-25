"""Aggregate content-free worker admission shadow lines into a parity table."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

PREFIX = "authored_admission_shadow "

#: The key ``series_metrics.py`` unpacks. Kept as the default so the numeric
#: mismatch rollup keeps working while this script's own table grows columns.
SUMMARY_COLUMNS = ("producer", "existingVerdict", "gateVerdict")
#: Everything the shadow line carries. ``predicate`` and ``blockCount`` name
#: *which* admission step produced a verdict and how many source blocks it
#: named, and ``gateError`` names the exception class behind an
#: ``authored_admission_internal_error`` row; without them a verdict difference
#: can only be attributed by reading the code and counting row adjacency.
COLUMNS = (
    "producer",
    "existingVerdict",
    "existingCode",
    "gateVerdict",
    "gateCode",
    "predicate",
    "blockCount",
    "gateError",
)
HEADINGS = {
    "producer": "producer",
    "existingVerdict": "existing verdict",
    "existingCode": "existing code",
    "gateVerdict": "gate verdict",
    "gateCode": "gate code",
    "predicate": "predicate",
    "blockCount": "blocks",
    "gateError": "gate error",
}
_NUMERIC = {"blockCount"}

# One worker log holds several operations back to back, and the shadow line is
# deliberately content-free -- it carries no operation id of its own. Every
# other structured line the worker writes does, so an operation is attributed by
# position: a shadow row belongs to the most recent operation id seen above it.
# That is exactly how a worker log is read by hand, and it is only valid because
# the generation worker runs one deck at a time.
_OPERATION = re.compile(r'"operation_id"\s*:\s*"([0-9a-fA-F-]{36})"')


def rows(lines, *, operation: str | None = None):
    """Yield each shadow row, optionally only those of one operation."""

    wanted = (operation or "").strip().lower().rstrip("….")
    current = ""
    for line in lines:
        match = _OPERATION.search(line)
        if match is not None:
            current = match.group(1).lower()
        if PREFIX not in line:
            continue
        try:
            row = json.loads(line.split(PREFIX, 1)[1])
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if wanted and not current.startswith(wanted):
            continue
        yield row


def aggregate(lines, *, operation: str | None = None, columns=SUMMARY_COLUMNS) -> Counter:
    counts: Counter = Counter()
    for row in rows(lines, operation=operation):
        if any(column not in row for column in SUMMARY_COLUMNS):
            continue
        # A line written before a column existed is still a parity row for
        # every column it does carry.
        counts[tuple(row.get(column, "") for column in columns)] += 1
    return counts


def markdown(counts: Counter, *, columns=SUMMARY_COLUMNS) -> str:
    header = "| " + " | ".join(HEADINGS.get(name, name) for name in columns) + " | count |"
    divider = "| " + " | ".join("---:" if name in _NUMERIC else "---" for name in columns) + " | ---: |"
    body = [
        "| " + " | ".join(str(value) for value in key) + f" | {count} |"
        for key, count in sorted(counts.items(), key=lambda item: tuple(str(v) for v in item[0]))
    ]
    return "\n".join([header, divider, *body]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--operation",
        help="Count only rows under this operation id (a leading prefix is enough). "
        "A worker log holds several operations; without this the table is a whole-log total.",
    )
    args = parser.parse_args()
    with args.log.open(encoding="utf-8", errors="replace") as stream:
        report = markdown(
            aggregate(stream, operation=args.operation, columns=COLUMNS), columns=COLUMNS
        )
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
