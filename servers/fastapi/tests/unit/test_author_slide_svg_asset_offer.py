"""author_slide_svg with AssetOffer slots: prompt kinds and in-place icon flattening."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from services.generation_core.drivers.svg.embed_assets import (
    apply_vector_icon_quality, flatten_icon_svg,
)
from services.svg_contract import validate_svg
from utils.llm_calls import author_slide_svg as module

ICON = (Path(module.__file__).resolve().parents[4] / "assets" / "infographics" / "monochrome-v1"
        / "originals" / "ai-01-neural-network.svg")


def _data_url(color=(10, 20, 30, 255)) -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 40), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _stub_provider(monkeypatch, response_svg: str, seen: dict):
    monkeypatch.setattr(module, "get_client", lambda **_: object())
    monkeypatch.setattr(module, "get_llm_config", lambda: None)
    monkeypatch.setattr(module, "get_model", lambda: "m")
    monkeypatch.setattr(module, "authoring_model", lambda model: model)
    monkeypatch.setattr(module, "get_llm_provider", lambda: SimpleNamespace(value="p"))
    monkeypatch.setattr(module, "_authoring_output_profile", lambda **_: (1000, None))
    monkeypatch.setattr(module, "_authoring_extra_body", lambda *_a, **_k: None)

    def kwargs(**values):
        seen["messages"] = values["messages"]
        return {}

    monkeypatch.setattr(module, "get_generate_kwargs", kwargs)

    async def no_wait():
        return None

    async def record(**_kwargs):
        return None

    monkeypatch.setattr(module, "_wait_for_author_rate_slot", no_wait)
    monkeypatch.setattr(module, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(module, "supports_provider_idempotency", lambda *_a, **_k: False)
    monkeypatch.setattr(module, "provider_call_admission", lambda **_k: contextlib.nullcontext())
    monkeypatch.setattr(module, "provider_generate_call",
                        lambda *_a, **_k: (lambda: SimpleNamespace(content=response_svg)))
    monkeypatch.setattr(module, "record_provider_call", record)
    monkeypatch.setattr(module, "extract_text", lambda content: content)
    monkeypatch.setattr("services.document_canvas_generation.generation_font_floor", lambda *_a: 18)


def _style():
    return {"contract_version": module.SVG_CONTRACT_VERSION, "digest": "d", "references": {}}


def _svg(images: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="40" y="80" font-family="Inter" font-size="32" fill="#111111">제목</text>'
            f"{images}</svg>")


def _run(monkeypatch, response, manifest):
    seen: dict = {}
    _stub_provider(monkeypatch, response, seen)
    result = asyncio.run(module.author_slide_svg("# 제목", _style(), "content", 1, 3, source_images=manifest,
                                                 canonical_source_images=True))
    return result, seen


def test_vector_icon_slot_is_flattened_in_place_and_reported(monkeypatch):
    icon = flatten_icon_svg(ICON.read_text(encoding="utf-8"), color="#1E5EFF")
    manifest = {
        "source-slot-illus": {"data_url": _data_url(), "description": "현장", "required": False,
                              "allowOmit": True, "kind": "illustration"},
        "source-slot-icon1": {"data_url": _data_url((0, 0, 0, 0)), "description": "icon: 분석",
                              "required": False, "allowOmit": True, "kind": "icon", "vector": icon},
    }
    response = _svg('<image href="source-slot-illus" x="700" y="120" width="400" height="400" '
                    'preserveAspectRatio="xMidYMid meet"/>'
                    '<image href="source-slot-icon1" x="40" y="200" width="48" height="48" '
                    'preserveAspectRatio="xMidYMid meet"/>')
    result, seen = _run(monkeypatch, response, manifest)
    assert result.receipt["validation_error"] is None
    assert result.receipt["vector_icons_used"] == ["source-slot-icon1"]
    assert 'href="source-slot-icon1"' not in result.svg_content and "<polyline" in result.svg_content
    assert 'href="source-slot-illus"' in result.svg_content  # canonical raster slot stays a handle
    validate_svg(result.svg_content, source_manifest={"source-slot-illus": {"required": False}})
    prompt = json.dumps([str(message) for message in seen["messages"]], ensure_ascii=False)
    assert '\\"kind\\": \\"icon\\"' in prompt or '"kind": "icon"' in prompt


def test_source_only_decks_keep_their_receipt_shape(monkeypatch):
    manifest = {"source-slot-a": {"data_url": _data_url(), "description": "사진", "required": False,
                                  "allowOmit": True}}
    response = _svg('<image href="source-slot-a" x="40" y="200" width="200" height="200" '
                    'preserveAspectRatio="xMidYMid meet"/>')
    result, seen = _run(monkeypatch, response, manifest)
    assert "vector_icons_used" not in result.receipt
    prompt = json.dumps([str(message) for message in seen["messages"]], ensure_ascii=False)
    assert "kind" not in prompt.split("source_images", 1)[1][:200]


def test_omitted_vector_icon_flags_the_page():
    quality = {"passed": True}
    apply_vector_icon_quality(quality, {"assetOffer": {"vectorIcons": {"offered": ["a", "b"], "used": ["a"]}}})
    assert quality["passed"] is False
    assert quality["assetOffer"]["omitted"] == ["b"]
    assert quality["failures"] == [{"kind": "asset_icon_omitted", "slotIds": ["b"]}]
