"""Canvas ids a design-pack variant can target, read from the canvas registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


CANVAS_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "constants" / "canvas_registry.json"
EMU_PER_PX = 9525


@dataclass(frozen=True, slots=True)
class RegisteredCanvas:
    canvas_id: str
    width: int
    height: int
    width_emu: int
    height_emu: int
    safe_x: float
    safe_y: float
    safe_width: float
    safe_height: float


@lru_cache(maxsize=1)
def registered_canvases() -> dict[str, RegisteredCanvas]:
    document = json.loads(CANVAS_REGISTRY_PATH.read_text(encoding="utf-8"))
    canvases: dict[str, RegisteredCanvas] = {}
    for entry in document["canvases"]:
        canvas = entry["canvas"]
        logical, physical, safe = canvas["logicalSize"], canvas["physicalSize"], canvas["safeArea"]
        canvases[entry["id"]] = RegisteredCanvas(
            canvas_id=entry["id"],
            width=int(logical["width"]),
            height=int(logical["height"]),
            width_emu=int(physical["widthEmu"]),
            height_emu=int(physical["heightEmu"]),
            safe_x=float(safe["x"]),
            safe_y=float(safe["y"]),
            safe_width=float(safe["width"]),
            safe_height=float(safe["height"]),
        )
    return canvases


def canvas_id_for_size(width: float, height: float, unit: str) -> str | None:
    """Exact size match only; a near-miss is not the same canvas."""

    for canvas in registered_canvases().values():
        expected = (canvas.width_emu, canvas.height_emu) if unit == "emu" else (canvas.width, canvas.height)
        if (float(width), float(height)) == (float(expected[0]), float(expected[1])):
            return canvas.canvas_id
    return None


__all__ = [
    "CANVAS_REGISTRY_PATH",
    "EMU_PER_PX",
    "RegisteredCanvas",
    "canvas_id_for_size",
    "registered_canvases",
]
