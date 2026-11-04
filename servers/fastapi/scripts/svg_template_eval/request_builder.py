"""Build a ``generate/async`` request from a fixed input, a template pin and a canvas (pure, no I/O but file reads).

The request templates under ``inputs/requests/<input>.json`` carry no template pin and no canvas; a cell of an
evaluation matrix (one input x one template) adds them here. ``inputs/<input>.txt`` is the source of truth for
the ``content`` field.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"


def list_inputs() -> list[str]:
    return sorted(path.stem for path in (INPUTS / "requests").glob("*.json"))


def load_request_template(name: str) -> dict:
    body = json.loads((INPUTS / "requests" / f"{name}.json").read_text(encoding="utf-8"))
    body["content"] = (INPUTS / f"{name}.txt").read_text(encoding="utf-8")
    return body


def build_request(template: dict, pin: dict, canvas_emu: tuple[int, int] | None = None,
                  frame_map: dict[str, str] | None = None) -> dict:
    """A copy of ``template`` with the template pin, its canvas (exact EMU) and an optional frame map."""
    body = copy.deepcopy(template)
    body["svgTemplatePin"] = pin
    body.setdefault("svgTemplateMode", "template-redesign")
    if canvas_emu is not None:
        width, height = canvas_emu
        body["svgCanvas"] = {"widthEmu": int(width), "heightEmu": int(height)}
    if frame_map:
        body["svgTemplateFrameMap"] = dict(frame_map)
    return body


def _file_or_inline(value):
    return json.loads(Path(value).read_text(encoding="utf-8")) if isinstance(value, str) else value


def load_template_spec(path: str) -> dict:
    """A template manifest: ``{"id", "pin": <pin or path>, "canvas_emu": [w, h], "frame_map": <map or path>}``.
    Relative paths inside it resolve against the manifest's directory."""
    manifest_path = Path(path)
    spec = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    for key in ("pin", "frame_map"):
        if isinstance(spec.get(key), str):
            spec[key] = json.loads((base / spec[key]).read_text(encoding="utf-8"))
    spec.setdefault("id", manifest_path.stem)
    return spec


def build_cells(input_names: list[str], specs: list[dict]) -> list[dict]:
    """The input x template cross product: ``[{"cell", "input", "template", "payload"}]``."""
    cells = []
    for name in input_names:
        template = load_request_template(name)
        for spec in specs:
            canvas = tuple(spec["canvas_emu"]) if spec.get("canvas_emu") else None
            cells.append({"cell": f"{name}__{spec['id']}", "input": name, "template": spec["id"],
                          "payload": build_request(template, spec["pin"], canvas, spec.get("frame_map"))})
    return cells
