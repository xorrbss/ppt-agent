"""The checked-in canvas registry is generated from the canvas constructors."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import DocumentCanvasMetadata, document_canvas_for_orientation

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = FASTAPI_ROOT / "scripts" / "generate_canvas_registry.py"


@pytest.fixture(scope="module")
def generator():
    spec = spec_from_file_location("generate_canvas_registry", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def registry(generator) -> dict:
    return json.loads(generator.REGISTRY_PATH.read_text(encoding="utf-8"))


def _by_id(registry: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in registry["canvases"]}


def test_checked_in_registry_is_current(generator) -> None:
    assert generator.check(), "run: uv run python scripts/generate_canvas_registry.py --write"
    assert generator.main(["--check"]) == 0


@pytest.mark.parametrize(("canvas_id", "orientation"), [("a4-landscape", "landscape"), ("a4-portrait", "portrait")])
def test_a4_entries_equal_document_canvas(registry, canvas_id: str, orientation: str) -> None:
    entry = _by_id(registry)[canvas_id]
    expected = document_canvas_for_orientation(orientation)

    assert DocumentCanvasMetadata.model_validate(entry["canvas"]) == expected
    assert entry["canvas"] == expected.model_dump(mode="json", by_alias=True)
    assert entry["badge"] == "A4"
    assert entry["aliases"] == {"page_size": "A4", "orientation": orientation}


@pytest.mark.parametrize(
    ("canvas_id", "aspect_ratio", "shape", "factor"),
    [
        ("wide-16x9", "16:9", "baseline", "1.00"),
        ("tall-9x16", "9:16", "portrait", "1.00"),
        ("tall-9x13", "9:13", "portrait", "0.79"),
        ("square-1x1", "1:1", "square", "0.51"),
        ("ultrawide-21x9", "21:9", "ultrawide", "1.00"),
    ],
)
def test_preset_entries_keep_preset_geometry(registry, canvas_id, aspect_ratio, shape, factor) -> None:
    entry = _by_id(registry)[canvas_id]
    metadata = canvas_metadata_for_aspect_ratio(aspect_ratio)
    canvas = DocumentCanvasMetadata.model_validate(entry["canvas"])

    assert (canvas.width_px, canvas.height_px) == (metadata.width_px, metadata.height_px)
    assert (canvas.physical_size.width_emu, canvas.physical_size.height_emu) == (
        metadata.width_px * 9525, metadata.height_px * 9525)
    assert canvas.canvas_contract() == metadata.canvas_contract()
    assert canvas.safe_area.x == canvas.safe_area.y == 64
    assert entry["aliases"] == {"aspect_ratio": aspect_ratio}
    assert entry["badge"] is None
    assert entry["shape"] == shape
    assert entry["budget"] == {"charsFactor": factor}


def test_picker_order_and_hidden_rows(registry) -> None:
    order = [entry["id"] for entry in registry["canvases"]]

    assert order == ["wide-16x9", "tall-9x16", "a4-landscape", "a4-portrait", "square-1x1",
                     "ultrawide-21x9", "tall-9x13"]
    assert [entry["sortOrder"] for entry in registry["canvases"]] == list(range(1, 8))
    assert [entry["id"] for entry in registry["canvases"] if entry["hidden"]] == ["tall-9x13"]
