"""Per-style stage gate for the 1:1 and 21:9 presets (admission spec §3, §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import services.authored_canvas as authored_canvas
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_canvas import (
    GATED_ASPECT_RATIOS,
    canvas_metadata_for_aspect_ratio,
    canvas_stage,
    merge_presentation_canvas,
    presentation_canvas_metadata,
    restore_request_canvas_metadata,
    supported_aspect_ratios_for_style,
    validate_authored_canvas_admission,
)
from utils.authored_style_catalogue import supported_canvases_for_style

_DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def stages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def install(document: object) -> None:
        path = tmp_path / "canvas_stages.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        monkeypatch.setattr(authored_canvas, "CANVAS_STAGES_PATH", path)
        authored_canvas._canvas_stages.cache_clear()

    yield install
    authored_canvas._canvas_stages.cache_clear()


def _request(aspect_ratio: str, *, style: str = "default", restyle: bool = False, **extra: object):
    fields: dict[str, object] = {
        "content": "Stage gate", "n_slides": 5, "export_as": "pptx",
        "authored_style": style, "aspect_ratio": aspect_ratio, **extra,
    }
    if restyle:
        fields.update(
            sourceMode="source_content_restyle",
            sourceContentManifestPin={"manifestId": "manifest-stage", "digest": _DIGEST},
            contentPolicy={"text": "verbatim"},
        )
    return GeneratePresentationRequest.model_validate(fields)


def test_shipped_stage_file_enables_only_the_bench_promoted_style() -> None:
    # Promoted by the pooled canvas bench (docs/canvas-bench-pooled-candidate-20260929.md).
    authored_canvas._canvas_stages.cache_clear()
    shipped = json.loads(authored_canvas.CANVAS_STAGES_PATH.read_text(encoding="utf-8"))

    assert shipped == {"1:1": {"default": "candidate"}, "21:9": {"default": "candidate"}}
    for ratio in GATED_ASPECT_RATIOS:
        assert canvas_stage(ratio, "default") == "candidate"
        assert canvas_stage(ratio, "osb-smart-banking") == "disabled"
    assert supported_aspect_ratios_for_style("default") == ["16:9", "9:16", "9:13", "1:1", "21:9"]
    assert supported_aspect_ratios_for_style("kbs-company-proposal") == ["16:9", "9:13"]


def test_canvas_stage_rejects_ungated_ratio() -> None:
    with pytest.raises(ValueError, match="canvas_stage_ratio_not_gated"):
        canvas_stage("9:16", "default")


@pytest.mark.parametrize(
    "document",
    [{"1:1": {}}, {"1:1": {}, "21:9": {}, "4:3": {}}, {"1:1": {"default": "shipped"}, "21:9": {}}],
)
def test_malformed_stage_file_fails_loudly(stages, document) -> None:
    stages(document)
    with pytest.raises(ValueError, match="canvas_stages_"):
        canvas_stage("1:1", "default")


@pytest.mark.parametrize("aspect_ratio", GATED_ASPECT_RATIOS)
@pytest.mark.parametrize("restyle", [False, True])
def test_disabled_stage_rejects_before_either_preset_branch(stages, aspect_ratio: str, restyle: bool) -> None:
    stages({"1:1": {}, "21:9": {}})
    request = _request(aspect_ratio, restyle=restyle)

    with pytest.raises(ValueError, match="^authored_canvas_stage_disabled$"):
        validate_authored_canvas_admission(request, style_id="default")


@pytest.mark.parametrize("stage", ["candidate", "verified"])
def test_enabled_stage_admits_like_portrait(stages, stage: str) -> None:
    stages({"1:1": {"default": stage}, "21:9": {"default": stage}})

    assert supported_aspect_ratios_for_style("default") == ["16:9", "9:16", "9:13", "1:1", "21:9"]
    assert supported_aspect_ratios_for_style("kbs-company-proposal") == ["16:9", "9:13"]
    for ratio in GATED_ASPECT_RATIOS:
        request = _request(ratio)
        validate_authored_canvas_admission(request, style_id="default")
        width, height = authored_canvas._CANVAS_SIZE_BY_ASPECT_RATIO[ratio]
        assert (request._authored_canvas.width_px, request._authored_canvas.height_px) == (width, height)
        with pytest.raises(ValueError, match="^authored_canvas_stage_disabled$"):
            validate_authored_canvas_admission(_request(ratio), style_id="kbs-company-proposal")
        pinned = _request(ratio, masterPin={"masterId": "master:stage", "revision": 1, "digest": _DIGEST})
        with pytest.raises(ValueError, match="^authored_portrait_combination_not_supported$"):
            validate_authored_canvas_admission(pinned, style_id="default")


def test_supported_canvases_reasons(stages) -> None:
    stages({"1:1": {"osb-smart-banking": "candidate"}, "21:9": {}})

    assert supported_canvases_for_style("default") == [
        {"id": "wide-16x9", "enabled": True, "reason": None},
        {"id": "tall-9x16", "enabled": True, "reason": None},
        {"id": "tall-9x13", "enabled": True, "reason": None},
        {"id": "square-1x1", "enabled": False, "reason": "canvas_stage_disabled"},
        {"id": "ultrawide-21x9", "enabled": False, "reason": "canvas_stage_disabled"},
    ]
    assert supported_canvases_for_style("osb-smart-banking") == [
        {"id": "wide-16x9", "enabled": True, "reason": None},
        {"id": "tall-9x16", "enabled": False, "reason": "style_not_supported"},
        {"id": "tall-9x13", "enabled": False, "reason": "style_not_supported"},
        {"id": "square-1x1", "enabled": True, "reason": None},
        {"id": "ultrawide-21x9", "enabled": False, "reason": "canvas_stage_disabled"},
    ]


def test_styles_response_keeps_null_reason() -> None:
    from api.v1.ppt.endpoints.authored import AuthoredStyleSummary
    from pydantic import TypeAdapter

    style = {
        "id": "default", "name": "n", "description": "d", "category": "general", "tags": [],
        "use_cases": [], "preview": {"bg": "#fff", "accent": "#000"},
        "supportedAspectRatios": ["16:9", "1:1"], "supportedCanvases": supported_canvases_for_style("default"),
    }
    dumped = TypeAdapter(list[AuthoredStyleSummary]).dump_python(
        TypeAdapter(list[AuthoredStyleSummary]).validate_python([style]), mode="json", by_alias=True,
        exclude_none=True,
    )

    assert dumped[0]["supportedCanvases"][0] == {"id": "wide-16x9", "enabled": True, "reason": None}
    assert "samples" not in dumped[0]


@pytest.mark.parametrize("aspect_ratio", GATED_ASPECT_RATIOS)
def test_new_preset_round_trips_through_persisted_theme(aspect_ratio: str) -> None:
    wire = canvas_metadata_for_aspect_ratio(aspect_ratio).model_dump(mode="json", by_alias=True)
    theme = merge_presentation_canvas({}, wire)

    assert "documentCanvas" not in theme
    assert theme["authored_vnext"]["canvas"] == wire
    assert presentation_canvas_metadata(theme) == wire

    request = _request(aspect_ratio)
    restore_request_canvas_metadata(request, presentation_canvas_metadata(theme))
    assert request._authored_canvas == canvas_metadata_for_aspect_ratio(aspect_ratio).canvas_contract()
    with pytest.raises(ValueError, match="authored_canvas_request_mismatch"):
        restore_request_canvas_metadata(_request("16:9"), wire)
