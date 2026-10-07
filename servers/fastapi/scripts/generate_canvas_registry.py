"""Generate the canvas registry from the existing canvas constructors.

docs/aspect-ratio-expansion-design-20260928.md decision 8: the registry feeds
the picker, the prompt shape and the content-budget factor, never admission.
``--write`` regenerates ``constants/canvas_registry.json``; ``--check`` exits 1
when the checked-in file differs from what the constructors produce.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from services.authored_canvas import PRESET_CANVAS_IDS, canvas_metadata_for_aspect_ratio  # noqa: E402
from services.document_canvas import (  # noqa: E402
    DOCUMENT_CANVAS_CONTRACT_VERSION,
    DocumentCanvasMetadata,
    LogicalSize,
    PhysicalSize,
    SafeArea,
    canvas_physical_size,
    document_canvas_for_orientation,
)
from utils.llm_calls.author_canvas_shape import classify_canvas_shape  # noqa: E402
from utils.llm_calls.outline_canvas_budget import canvas_factor  # noqa: E402

REGISTRY_PATH = FASTAPI_ROOT / "constants" / "canvas_registry.json"
SCHEMA_VERSION = "canvas-registry/v1"
A4_CANVAS_IDS = {"landscape": "a4-landscape", "portrait": "a4-portrait"}
# Picker order from the decisions doc; 9:13 stays registered but hidden.
SORT_ORDER = {
    "wide-16x9": 1, "tall-9x16": 2, "a4-landscape": 3, "a4-portrait": 4,
    "square-1x1": 5, "ultrawide-21x9": 6, "tall-9x13": 7, "standard-4x3": 8,
}
HIDDEN_CANVAS_IDS = frozenset({"tall-9x13"})


def _preset_canvas(aspect_ratio: str) -> DocumentCanvasMetadata:
    metadata = canvas_metadata_for_aspect_ratio(aspect_ratio)
    width_emu, height_emu = canvas_physical_size(metadata)
    safe = metadata.safe_area
    return DocumentCanvasMetadata(
        contract_version=DOCUMENT_CANVAS_CONTRACT_VERSION,
        logical_size=LogicalSize(width=metadata.width_px, height=metadata.height_px),
        physical_size=PhysicalSize(width_emu=width_emu, height_emu=height_emu),
        safe_area=SafeArea(x=safe.x, y=safe.y, width=safe.width, height=safe.height),
    )


def _entry(canvas_id: str, canvas: DocumentCanvasMetadata, *, badge: str | None,
           aliases: dict[str, str]) -> dict[str, Any]:
    margin_px = int(canvas.safe_area.x)
    return {
        "id": canvas_id,
        "canvas": canvas.model_dump(mode="json", by_alias=True),
        "shape": classify_canvas_shape(canvas.width_px, canvas.height_px),
        "badge": badge,
        "aliases": aliases,
        "budget": {"charsFactor": str(canvas_factor(canvas.width_px, canvas.height_px, margin_px))},
        "sortOrder": SORT_ORDER[canvas_id],
        "hidden": canvas_id in HIDDEN_CANVAS_IDS,
    }


def build_registry() -> dict[str, Any]:
    entries = [
        _entry(canvas_id, document_canvas_for_orientation(orientation), badge="A4",
               aliases={"page_size": "A4", "orientation": orientation})
        for orientation, canvas_id in A4_CANVAS_IDS.items()
    ]
    entries += [
        _entry(canvas_id, _preset_canvas(aspect_ratio), badge=None, aliases={"aspect_ratio": aspect_ratio})
        for aspect_ratio, canvas_id in PRESET_CANVAS_IDS.items()
    ]
    from services.svg_canvas import svg_canvas_for_aspect_ratio

    entries.append(_entry("standard-4x3", svg_canvas_for_aspect_ratio("4:3"),
                          badge=None, aliases={"aspect_ratio": "4:3"}))
    if {entry["id"] for entry in entries} != set(SORT_ORDER):
        raise ValueError("canvas_registry_ids_mismatch")
    return {"schemaVersion": SCHEMA_VERSION, "canvases": sorted(entries, key=lambda entry: entry["sortOrder"])}


def render(registry: dict[str, Any]) -> str:
    return json.dumps(registry, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def check() -> bool:
    return REGISTRY_PATH.is_file() and REGISTRY_PATH.read_text(encoding="utf-8") == render(build_registry())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canvas registry generator")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="regenerate the registry file")
    action.add_argument("--check", action="store_true", help="exit 1 if the registry file is stale")
    args = parser.parse_args(argv)
    if args.write:
        REGISTRY_PATH.write_text(render(build_registry()), encoding="utf-8", newline="\n")
        print(f"wrote {REGISTRY_PATH}")
        return 0
    if check():
        print("canvas registry is current")
        return 0
    print(f"{REGISTRY_PATH} is stale; run scripts/generate_canvas_registry.py --write")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
