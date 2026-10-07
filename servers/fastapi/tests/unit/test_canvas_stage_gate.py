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
        if isinstance(document, dict) and set(document) == {"1:1", "21:9"}:
            document = {**document, "9:16": {"default": "candidate"},
                        "9:13": {"default": "candidate", "kbs-company-proposal": "candidate"}}
        path.write_text(json.dumps(document), encoding="utf-8")
        monkeypatch.setattr(authored_canvas, "CANVAS_STAGES_PATH", path)
        authored_canvas._canvas_stages.cache_clear()

    yield install
    authored_canvas._canvas_stages.cache_clear()


def _request(aspect_ratio: str, *, style: str = "default", restyle: bool = False, **extra: object):
    fields: dict[str, object] = {
        "content": "Stage gate", "n_slides": 5, "export_as": "pptx",
        "authored_style": style, "aspect_ratio": aspect_ratio, "strategy": "authored", **extra,
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

    assert shipped == {"1:1": {"default": "candidate"}, "21:9": {"default": "candidate"},
                       "9:16": {"default": "candidate"},
                       "9:13": {"default": "candidate", "kbs-company-proposal": "candidate"}}
    for ratio in GATED_ASPECT_RATIOS:
        assert canvas_stage(ratio, "default") == "candidate"
        assert canvas_stage(ratio, "osb-smart-banking") == "disabled"
    assert supported_aspect_ratios_for_style("default") == ["16:9", "9:16", "9:13", "1:1", "21:9"]
    assert supported_aspect_ratios_for_style("kbs-company-proposal") == ["16:9", "9:13"]


def test_canvas_stage_rejects_ungated_ratio() -> None:
    with pytest.raises(ValueError, match="canvas_stage_ratio_not_gated"):
        canvas_stage("16:9", "default")


@pytest.mark.parametrize(
    "document",
    [{"1:1": {}}, {"1:1": {}, "21:9": {}, "4:3": {}}, {"1:1": {"default": "shipped"}, "21:9": {}}],
)
def test_malformed_stage_file_fails_loudly(stages, document) -> None:
    stages(document)
    with pytest.raises(ValueError, match="canvas_stages_"):
        canvas_stage("1:1", "default")


@pytest.mark.parametrize("aspect_ratio", ("1:1", "21:9"))
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
    for ratio in ("1:1", "21:9"):
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
        {"id": "tall-9x16", "enabled": False, "reason": "canvas_stage_disabled"},
        {"id": "tall-9x13", "enabled": False, "reason": "canvas_stage_disabled"},
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


@pytest.mark.parametrize("ratio", ["9:16", "9:13"])
@pytest.mark.parametrize("style", ["default", "kbs-company-proposal", "osb-smart-banking", "hyundai-culture-center"])
def test_portrait_exposure_and_restyle_are_preserved(ratio, style):
    enabled = style == "default" or (ratio == "9:13" and style == "kbs-company-proposal")
    assert (ratio in supported_aspect_ratios_for_style(style)) == enabled
    request = _request(ratio, style=style)
    if enabled:
        validate_authored_canvas_admission(request, style_id=style)
    else:
        with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
            validate_authored_canvas_admission(request, style_id=style)
    validate_authored_canvas_admission(_request(ratio, style=style, restyle=True), style_id=style)


def test_stage_path_environment_override(monkeypatch, tmp_path):
    path = tmp_path / "isolated-stages.json"
    path.write_text(json.dumps({ratio: {} for ratio in GATED_ASPECT_RATIOS}))
    monkeypatch.setenv("CANVAS_STAGES_PATH", str(path))
    authored_canvas._canvas_stages.cache_clear()
    try:
        assert canvas_stage("9:16", "default") == "disabled"
        assert supported_aspect_ratios_for_style("default") == ["16:9"]
        validate_authored_canvas_admission(_request("9:16", restyle=True), style_id="default")
    finally:
        authored_canvas._canvas_stages.cache_clear()


def test_stage_keys_are_packaged_styles():
    from utils.authored_styles import load_authored_styles

    known = {style.id for style in load_authored_styles()} | {"default"}
    assert all(set(styles) <= known for styles in authored_canvas._canvas_stages().values())


@pytest.mark.parametrize("ratio", ["16:9", "4:3", "9:16", "9:13", "1:1", "21:9"])
def test_svg_all_presets_ignore_authored_style_gate(stages, ratio):
    stages({"1:1": {}, "21:9": {}})
    request = _request(ratio, style="osb-smart-banking", strategy="svg")
    validate_authored_canvas_admission(request, style_id="osb-smart-banking")
    assert request._authored_canvas.width_px > 0


@pytest.mark.parametrize("size", [(210, 297), (297, 210), (50, 200), (200, 50), (157.13, 493.27)])
def test_svg_custom_canvas_round_trip_and_physical_size(size):
    from services.authored_canvas import request_canvas_metadata
    from services.document_canvas import canvas_physical_size
    from decimal import Decimal

    request = GeneratePresentationRequest(content="custom", n_slides=1, strategy="svg",
                                         svg_canvas={"width_mm": size[0], "height_mm": size[1]})
    validate_authored_canvas_admission(request, style_id="default")
    wire = request_canvas_metadata(request)
    assert canvas_physical_size(wire) == tuple(int(Decimal(str(value)) * 36000) for value in size)
    payload = request.model_dump(mode="json")
    assert "aspect_ratio" not in payload and "page_size" not in payload
    restored = GeneratePresentationRequest.model_validate(payload)
    restore_request_canvas_metadata(restored, wire)
    assert restored._authored_canvas == request._authored_canvas


@pytest.mark.parametrize("custom", [dict(width_mm=49, height_mm=100), dict(width_mm=1001, height_mm=500),
                                     dict(width_mm=50, height_mm=201), dict(width_mm=100.001, height_mm=100)])
def test_svg_custom_rejects_unbounded_dimensions(custom):
    with pytest.raises(ValueError):
        GeneratePresentationRequest(content="custom", n_slides=1, strategy="svg", svg_canvas=custom)


def test_svg_only_selectors_rejected_for_authored_and_conflicting_selectors():
    for selectors in ({"aspect_ratio": "4:3"}, {"svg_canvas": {"width_mm": 100, "height_mm": 100}}):
        with pytest.raises(ValueError, match="svg_canvas_strategy_required"):
            GeneratePresentationRequest(content="custom", n_slides=1, strategy="authored", **selectors)
    with pytest.raises(ValueError, match="document_canvas_selectors_conflict"):
        GeneratePresentationRequest(content="custom", n_slides=1, strategy="svg", aspect_ratio="16:9",
                                    svg_canvas={"width_mm": 100, "height_mm": 100})



def test_svg_uploaded_reference_selectors_are_exclusive_and_round_trip():
    import uuid
    from models.public_v2_contracts import PresentationGenerationRequestV2

    snapshot_id = uuid.uuid4()
    request = GeneratePresentationRequest(content="reference", n_slides=1, strategy="svg",
                                         svgDesignReferenceSnapshotId=snapshot_id)
    assert GeneratePresentationRequest.model_validate(request.model_dump(mode="json")).svg_design_reference_snapshot_id == snapshot_id
    public = PresentationGenerationRequestV2(content="reference", strategy="svg", svgDesignReferenceSnapshotId=str(snapshot_id))
    assert public.svg_design_reference_snapshot_id == snapshot_id
    for strategy in (None, "authored"):
        with pytest.raises(ValueError, match="svg_design_reference_strategy_required"):
            GeneratePresentationRequest(content="reference",n_slides=1,strategy=strategy,
                                        svg_design_reference_snapshot_id=snapshot_id)
    with pytest.raises(ValueError, match="svg_design_reference_conflict"):
        GeneratePresentationRequest(content="reference",n_slides=1,strategy="svg",
            svg_design_reference_snapshot_id=snapshot_id,
            svg_design_reference_pin=dict(design_pack_id="test-pack",version=1,digest="sha256:"+"a"*64))
