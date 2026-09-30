"""Capture the 16:9 svg ``design`` string of every canonical authored style.

Writes ``tests/fixtures/svg_design_16x9_baseline/design_<style.id>.txt`` for
each ``authored_styles/*.yaml`` id that resolves to itself (aliases are skipped
because their own brief text never reaches the svg prompt; the exact-
reconstruction style is skipped because svg rejects it at resolution). The
string is exactly what ``services.generation_core.authoring.author_slide_svg.svg_design_text``
builds, so it pins ``common_guidance``, ``overrides`` and the receipt-visible
``digest`` (docs/svg-canvas-brief-rewrite-and-experiment-plan-20260929.md §1.6).

Recapture is a deliberate act: rerun, review the diff, and commit with the
reason (contract version bump, font bundle change, reference asset re-encode,
YAML edit). Run with no ``AUTHORED_*``/``SVG_*`` environment set:

    cd servers/fastapi
    uv run python scripts/capture_svg_design_baseline.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

FIXTURE_DIR = FASTAPI_ROOT / "tests" / "fixtures" / "svg_design_16x9_baseline"
STYLES_DIR = FASTAPI_ROOT / "authored_styles"


def main() -> int:
    stray = sorted(k for k in os.environ if k.startswith(("AUTHORED_", "SVG_")))
    if stray:
        print(f"refusing to capture with knobs set: {', '.join(stray)}", file=sys.stderr)
        return 2
    from utils.authored_styles import AuthoredExactReconstructionUnavailableError, resolve_authored_style
    from services.generation_core.authoring.author_slide_svg import svg_design_text
    from utils.svg_style_adapter import adapt_svg_style

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for source in sorted(STYLES_DIR.glob("*.yaml")):
        try:
            style = resolve_authored_style(source.stem)
        except AuthoredExactReconstructionUnavailableError:
            print(f"skip {source.stem}: rejected at resolution")
            continue
        if style.id != source.stem:
            print(f"skip {source.stem}: alias of {style.id}")
            continue
        text = svg_design_text(adapt_svg_style(style, width=1280, height=720))
        target = FIXTURE_DIR / f"design_{style.id}.txt"
        target.write_bytes(text.encode("utf-8"))
        written.append(target.name)
    for path in sorted(FIXTURE_DIR.iterdir()):
        if path.name not in written:
            print(f"stale fixture (not a canonical style any more): {path.name}", file=sys.stderr)
    print(f"wrote {len(written)} fixtures to {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
