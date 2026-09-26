"""Copy the renderer import closure into a minimal Docker build stage."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from source_manifest import source_files


def stage_sources(source_root: Path, target_root: Path) -> int:
    files = source_files(source_root)
    for relative in files:
        source = source_root / relative
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("target_root", type=Path)
    arguments = parser.parse_args()
    print(f"Staged {stage_sources(arguments.source_root, arguments.target_root)} renderer modules.")


if __name__ == "__main__":
    main()
