"""The svg unit-test subset that Bitbucket runs on every pull request.

The subset is a checked-in list (``tests/svg_unit_subset.txt``) so the CI step is
deterministic and a reviewer sees a new test join it. The list is derived by one
rule, and ``--check`` fails when the list and the rule disagree, so a new svg
test cannot silently stay outside the gate.

Rule: a file directly under ``tests/unit`` whose name starts with ``test_`` and

* matches one of ``NAME_PATTERN``'s words (svg, canvas, author, quality, ...), or
* imports the svg generation driver or an ``services.svg_*`` module.

Usage (from ``servers/fastapi``)::

    uv run python scripts/svg_unit_subset.py --check   # CI: list matches the rule
    uv run python scripts/svg_unit_subset.py --write   # regenerate the list
    uv run python scripts/svg_unit_subset.py --print   # one path per line
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
UNIT_ROOT = FASTAPI_ROOT / "tests" / "unit"
LIST_PATH = FASTAPI_ROOT / "tests" / "svg_unit_subset.txt"

NAME_PATTERN = re.compile(
    r"svg|canvas|author|quality|fallback|icon|native|header|export_scene|renderer"
    r"|slide_capture|terminal|raster|provider_overload|anthropic_structured"
)
IMPORT_PATTERN = re.compile(
    r"generation_core\.drivers\.svg|services\.svg_|generation_core\.drivers import svg"
)


def derive_subset() -> list[str]:
    selected: list[str] = []
    for path in sorted(UNIT_ROOT.glob("test_*.py")):
        if NAME_PATTERN.search(path.name) or IMPORT_PATTERN.search(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            selected.append(path.relative_to(FASTAPI_ROOT).as_posix())
    return selected


def read_list() -> list[str]:
    return [
        line.strip()
        for line in LIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args(argv)

    derived = derive_subset()
    if args.write:
        LIST_PATH.write_text("\n".join(derived) + "\n", encoding="utf-8")
        print(f"wrote {len(derived)} files to {LIST_PATH.relative_to(FASTAPI_ROOT)}")
        return 0
    if args.show:
        print("\n".join(read_list()))
        return 0

    checked_in = read_list()
    missing = sorted(set(derived) - set(checked_in))
    stale = sorted(set(checked_in) - set(derived))
    absent = [item for item in checked_in if not (FASTAPI_ROOT / item).is_file()]
    if missing or stale or absent:
        for label, items in (("not in the list", missing), ("no longer matches the rule", stale),
                             ("file does not exist", absent)):
            for item in items:
                print(f"{label}: {item}", file=sys.stderr)
        print("run: uv run python scripts/svg_unit_subset.py --write", file=sys.stderr)
        return 1
    print(f"svg unit subset is current: {len(checked_in)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
