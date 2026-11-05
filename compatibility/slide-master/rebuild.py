"""Rebuild the converter from a verified baseline and reviewed local patches.

Use --source for an unpacked baseline vendor tree or --baseline-revision for a
local Git commit. Output must not exist; the active vendor tree is never replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
VENDOR_PREFIX = "servers/fastapi/vendor/slide_master/"
#: Applied in order; each entry is (patch file, manifest version suffix, note).
PATCHES = (
    (HERE / "patches/document-canvas-1.patch", "document-canvas.2",
     "document-canvas.2: invocation-local physical units and bounded source-canvas rounding"),
    (HERE / "patches/text-advance-1.patch", "text-advance.1",
     "text-advance.1: single-line text frames never narrower than the caller's exact font advance"),
    (HERE / "patches/chart-furniture-16.patch", "chart-furniture.16",
     "chart-furniture.16: native chart manual layouts, per-series data labels, gap/overlap/vary-colors options, title bold/alignment; table paragraph spacing, soft breaks and cell padding"),
    (HERE / "patches/native-fixed-text-1.patch", "native-fixed-text.1",
     "native-fixed-text.1: trusted invocation-local fixed-text adapter on the conversion context"),
    (HERE / "patches/xfrm-bounds-1.patch", "xfrm-bounds.1",
     "xfrm-bounds.1: axis-aligned xfrm shapes trace the exact off+ext frame their xfrm declares"),
)


def rebuild(output: Path, *, source: Path | None = None, revision: str | None = None):
    if output.exists():
        raise ValueError("converter_rebuild_output_already_exists")
    baseline = json.loads((HERE / "baseline-manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="slide-master-rebuild-") as temporary:
        tree = Path(temporary)
        for relative, expected in baseline["files"].items():
            if source is not None:
                payload = (source / relative).read_bytes()
            else:
                payload = subprocess.check_output(
                    ["git", "show", f"{revision}:{VENDOR_PREFIX}{relative}"], cwd=REPOSITORY
                )
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError(f"converter_baseline_mismatch:{relative}")
            destination = tree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        for patch, _suffix, _note in PATCHES:
            subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                            "apply", "--no-index", "-p5", str(patch)], cwd=tree, check=True)
        manifest = dict(baseline)
        manifest["version"] = "+".join(["vendored-20260916", *(suffix for _patch, suffix, _note in PATCHES)])
        names = sorted(set(baseline["files"]) | {"svg_to_pptx/conversion_context.py"})
        manifest["files"] = {name: hashlib.sha256((tree / name).read_bytes()).hexdigest() for name in names}
        manifest["presentonPatches"] = baseline["presentonPatches"] + [note for _patch, _suffix, note in PATCHES]
        manifest["patches"] = {patch.name: hashlib.sha256(patch.read_bytes()).hexdigest()
                               for patch, _suffix, _note in PATCHES}
        (tree / "MANIFEST.json").write_bytes((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        shutil.copytree(tree, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--source", type=Path)
    origin.add_argument("--baseline-revision")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rebuild(args.output.resolve(), source=args.source, revision=args.baseline_revision)
