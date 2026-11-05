"""Blind-rating helper: shuffled slide PNGs, a key file and a ratings sheet.

  python3 blind.py <out-dir>          create blind/<code>.png, blind_key.json, ratings.csv
  python3 blind.py <out-dir> --redo   rebuild (discards a filled ratings.csv; keep a copy)

Rate blind/*.png without opening blind_key.json (it names the run and arm). Fill ratings.csv
with 1-5 per axis (layout, hierarchy, readability, richness, consistency, korean), then rerun
summarize.py. Keep one rater per measurement; do not re-rate after seeing the arm.
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from pathlib import Path

AXES = ("layout", "hierarchy", "readability", "richness", "consistency", "korean")


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    root = Path(argv[0])
    blind, key_path = root / "blind", root / "blind_key.json"
    if key_path.exists():
        if "--redo" not in argv:
            print("blind set exists; pass --redo to rebuild it")
            return 0
        shutil.rmtree(blind, ignore_errors=True)
    items = sorted((run.name, png.name) for run in (root / "runs").glob("*") if (run / "png").is_dir()
                   for png in (run / "png").glob("slide*.png"))
    if not items:
        print("no slide PNGs to blind")
        return 0
    blind.mkdir(exist_ok=True)
    codes = random.SystemRandom().sample(range(1000, 10000), len(items))
    key = {}
    for (run, png), code in zip(items, codes, strict=True):
        name = f"{code}.png"
        shutil.copy(root / "runs" / run / "png" / png, blind / name)
        key[name] = {"run": run, "slide": png}
    key_path.write_text(json.dumps(key, indent=1))
    with open(root / "ratings.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["png", *AXES, "note"])
        for name in sorted(key):
            writer.writerow([name, *[""] * len(AXES), ""])
    print(f"{len(key)} blind PNGs in {blind}; fill {root / 'ratings.csv'}, then run summarize.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
