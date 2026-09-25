"""Independent wire fixtures and mode-neutral canvas boundary checks."""
import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_canvas import (
    merge_presentation_canvas,
    presentation_canvas_metadata,
    request_canvas_metadata,
    restore_request_canvas_metadata,
    validate_authored_canvas_admission,
)
from services.document_canvas import (
    DocumentCanvasMetadata,
    canvas_digest,
    canvas_physical_size,
    document_canvas_for_orientation,
    document_canvas_from_source_slide_size,
    parse_canvas_metadata,
    preserve_document_canvas,
    require_canvas_capability,
)


FIXTURES = json.loads((Path(__file__).resolve().parents[4] /
    "contracts/document-canvas/v1/fixtures.json").read_text(encoding="utf-8"))


def test_generation_units_preserve_physical_typography_and_reject_canvas_drift():
    from services.document_canvas_generation import export_size_points, generation_font_floor
    from utils.authored_font_policy import requested_font_floor_px

    canvas = document_canvas_for_orientation("landscape")
    assert export_size_points(canvas.canvas_contract(), canvas) == pytest.approx(
        (841.8897637795, 595.2755905512)
    )
    assert generation_font_floor("body 9.5pt", canvas) == pytest.approx(13.405555555556)
    assert generation_font_floor("body 14px", canvas) == 14
    assert requested_font_floor_px("body 9.5pt") == pytest.approx(12.666666666667)
    with pytest.raises(ValueError, match="geometry_mismatch"):
        export_size_points(document_canvas_for_orientation("portrait").canvas_contract(), canvas)


@pytest.mark.parametrize("fixture", FIXTURES["a4"])
def test_fixed_canvas_fixture(fixture):
    canvas = document_canvas_for_orientation(fixture["orientation"])
    assert canvas.model_dump(mode="json", by_alias=True) == fixture["canvas"]
    assert canvas_digest(canvas) == fixture["digest"]
    assert list(canvas_physical_size(canvas)) == [
        fixture["canvas"]["physicalSize"]["widthEmu"],
        fixture["canvas"]["physicalSize"]["heightEmu"],
    ]
    assert canvas.point_per_unit == pytest.approx(9000 / 12700)


@pytest.mark.parametrize("change", [
    {"contractVersion": "document-canvas/v99"},
    {"extra": True},
    {"logicalSize": {"width": True, "height": 840}},
    {"logicalSize": {"width": "1188", "height": 840}},
    {"physicalSize": {"widthEmu": 10692000, "heightEmu": 7550000}},
    {"safeArea": {"x": 0.0000001, "y": 60, "width": 1068, "height": 720}},
    {"safeArea": {"x": 121, "y": 60, "width": 1068, "height": 720}},
    {"safeArea": {"x": True, "y": 60, "width": 1068, "height": 720}},
    {"safeArea": {"x": float("nan"), "y": 60, "width": 1068, "height": 720}},
])
def test_strict_canvas_rejects_bad_wire(change):
    wire = copy.deepcopy(FIXTURES["a4"][0]["canvas"])
    wire.update(change)
    with pytest.raises(ValueError):
        parse_canvas_metadata(wire)


def test_digest_normalizes_numbers_without_losing_precision():
    wire = copy.deepcopy(FIXTURES["a4"][0]["canvas"])
    wire["safeArea"]["x"] = 60.0
    assert canvas_digest(wire) == FIXTURES["a4"][0]["digest"]
    wire["safeArea"]["x"] = -0.0
    negative_zero_digest = canvas_digest(wire)
    wire["safeArea"]["x"] = 0
    assert canvas_digest(wire) == negative_zero_digest
    wire["safeArea"]["x"] = 0.000001
    DocumentCanvasMetadata.model_validate(wire)


def test_source_canvas_preserves_exact_pptx_emu_with_bounded_raster_projection():
    canvas = document_canvas_from_source_slide_size(
        width_emu=10691813,
        height_emu=7559675,
    )
    assert (canvas.width_px, canvas.height_px) == (1188, 840)
    assert canvas_physical_size(canvas) == (10691813, 7559675)
    assert canvas.model_dump(mode="json", by_alias=True)["physicalSize"] == {
        "widthEmu": 10691813,
        "heightEmu": 7559675,
    }


@pytest.mark.parametrize("selectors", [
    {"page_size": "A4"}, {"orientation": "portrait"},
    {"page_size": None, "orientation": None},
    {"page_size": "A4", "orientation": "landscape", "aspect_ratio": "16:9"},
    {"page_size": "Letter", "orientation": "landscape"},
    {"page_size": "A4", "orientation": "square"},
    {"page_size": "A4", "orientation": []},
])
def test_selector_conflicts_are_checked_before_defaults(selectors):
    from models.workspace import WorkspaceBootstrapRequest

    with pytest.raises(ValidationError):
        GeneratePresentationRequest(content="fixture", **selectors)
    with pytest.raises(ValidationError):
        WorkspaceBootstrapRequest(
            idempotency_key="canvas-selector-fixture", prompt="fixture",
            generation_settings=selectors,
        )


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_request_serialization_resume_has_no_phantom_aspect_ratio(orientation):
    request = GeneratePresentationRequest(content="fixture", page_size="A4", orientation=orientation)
    wire = request.model_dump(mode="json")
    assert "aspect_ratio" not in wire
    restored = GeneratePresentationRequest.model_validate(wire)
    canvas = request_canvas_metadata(request)
    restore_request_canvas_metadata(restored, canvas)
    assert request_canvas_metadata(restored) == canvas
    with pytest.raises(ValueError):
        restore_request_canvas_metadata(restored, None, present=False)


def test_legacy_request_and_canvas_are_unchanged():
    request = GeneratePresentationRequest(content="fixture")
    payload = request.model_dump(mode="json")
    assert payload["aspect_ratio"] == "16:9"
    assert "page_size" not in payload and "orientation" not in payload
    assert canvas_physical_size(request_canvas_metadata(request)) == (12192000, 6858000)


def test_facade_stores_root_contract_and_fails_closed():
    wire = FIXTURES["a4"][0]["canvas"]
    theme = merge_presentation_canvas({"mode": "svg"}, wire)
    assert theme == {"mode": "svg", "documentCanvas": wire}
    assert presentation_canvas_metadata(theme) == wire
    with pytest.raises(ValueError):
        presentation_canvas_metadata({"documentCanvas": None})
    with pytest.raises(ValueError):
        presentation_canvas_metadata({**theme, "authored_vnext": {"canvas": {}}})


def test_theme_preservation_and_reader_capability():
    wire = FIXTURES["a4"][0]["canvas"]
    theme = {"mode": "svg", "documentCanvas": wire}
    assert preserve_document_canvas(theme, {"name": "Updated"})["documentCanvas"] == wire
    with pytest.raises(ValueError):
        preserve_document_canvas(theme, {"documentCanvas": None})
    with pytest.raises(ValueError):
        preserve_document_canvas({}, theme)
    with pytest.raises(HTTPException) as caught:
        require_canvas_capability(theme, None)
    assert caught.value.status_code == 409
    require_canvas_capability(theme, "authored-canvas/v1, document-canvas/v1")
    require_canvas_capability({}, None)


def test_a4_writer_stays_closed_until_release_verified(monkeypatch):
    import services.document_canvas as module

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", False)
    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED", False)
    request = GeneratePresentationRequest(content="fixture", page_size="A4", orientation="portrait")
    with pytest.raises(ValueError, match="document_canvas_a4_not_available"):
        validate_authored_canvas_admission(request, style_id="default")


@pytest.mark.parametrize("change,expected", [
    ({"generation_engine": "deck-ir"}, "document_canvas_generation_combination_not_supported"),
    ({"generation_mode": "fidelity-first"}, "document_canvas_generation_combination_not_supported"),
    ({"_authored_master_pin": object()}, "document_canvas_product_readback_pending"),
    ({"_authored_exact_snapshot_id": "fixture"}, "document_canvas_fixed_source_not_supported"),
    ({"source_mode": "source_content_restyle", "strategy": "svg"}, "document_canvas_generation_combination_not_supported"),
])
def test_a4_admission_rejects_unsupported_paths(monkeypatch, change, expected):
    from types import SimpleNamespace
    import services.document_canvas as module

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    request = GeneratePresentationRequest(content="fixture", page_size="A4", orientation="portrait")
    request._authored_style_snapshot = SimpleNamespace(reference_mode="style_reference")
    for key, value in change.items():
        setattr(request, key, value)
    with pytest.raises(ValueError, match=expected):
        validate_authored_canvas_admission(request, style_id="default")


@pytest.mark.parametrize("strategy,source_mode", [("authored", None), ("svg", None), ("authored", "source_content_restyle")])
def test_a4_admission_accepts_only_reflow_capable_paths(monkeypatch, strategy, source_mode):
    from dataclasses import replace
    from types import SimpleNamespace
    import services.document_canvas as module
    import services.document_canvas_capabilities as capabilities

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    route_id = (
        "source-content-composer-restyle"
        if source_mode is not None
        else "authored-reflow-svg" if strategy == "svg" else "authored-reflow-html"
    )
    monkeypatch.setattr(
        capabilities,
        "A4_ROUTE_POLICIES",
        tuple(
            replace(policy, implemented=True, verified=True)
            if policy.route_id == route_id
            else policy
            for policy in capabilities.A4_ROUTE_POLICIES
        ),
    )
    request = GeneratePresentationRequest(content="fixture", page_size="A4", orientation="portrait")
    request._authored_style_snapshot = SimpleNamespace(reference_mode="style_reference")
    request.strategy = strategy
    request.source_mode = source_mode
    validate_authored_canvas_admission(request, style_id="default")
    assert request._document_canvas.orientation == "portrait"


def test_release_switch_does_not_open_unverified_a4_route(monkeypatch):
    from types import SimpleNamespace
    import services.document_canvas as module

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    request = GeneratePresentationRequest(
        content="fixture", page_size="A4", orientation="portrait"
    )
    request._authored_style_snapshot = SimpleNamespace(reference_mode="style_reference")
    with pytest.raises(ValueError, match="document_canvas_product_readback_pending"):
        validate_authored_canvas_admission(request, style_id="default")


def test_canvas_capability_selection_uses_shared_route_policy(monkeypatch):
    import services.document_canvas as module
    from api.v1.ppt.endpoints.authored import get_canvas_capabilities

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    response = get_canvas_capabilities(
        generation_strategy="authored",
        authored_style="default",
        source_content_mode=False,
        has_company_standard=False,
    )
    selection = response["a4"]["selection"]
    assert selection == {
        "routeId": "authored-reflow-html",
        "enabled": False,
        "reason": "document_canvas_product_readback_pending",
    }
    assert response["a4"]["enabled"] is False


def test_canvas_capability_missing_selectors_fails_closed():
    from api.v1.ppt.endpoints.authored import get_canvas_capabilities

    selection = get_canvas_capabilities()["a4"]["selection"]
    assert selection == {
        "routeId": None,
        "enabled": False,
        "reason": "document_canvas_selection_required",
    }


def test_export_only_readiness_does_not_enable_a4_writer(monkeypatch):
    from dataclasses import replace
    import services.document_canvas as module
    import services.document_canvas_capabilities as capabilities

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    monkeypatch.setattr(
        capabilities,
        "A4_ROUTE_POLICIES",
        tuple(
            replace(
                policy,
                implemented=policy.route_id == "authored-vnext-native-export",
                verified=policy.route_id == "authored-vnext-native-export",
            )
            for policy in capabilities.A4_ROUTE_POLICIES
        ),
    )
    response = capabilities.a4_canvas_capabilities()
    assert response["enabled"] is False
    export_route = next(
        route
        for route in response["routes"]
        if route["routeId"] == "authored-vnext-native-export"
    )
    assert export_route["enabled"] is True


def test_public_v2_request_pair_roundtrip():
    from api.v2.contracts import PresentationGenerationRequestV2

    request = PresentationGenerationRequestV2(content="fixture", pageSize="A4", orientation="portrait")
    wire = request.model_dump(mode="json", by_alias=True)
    assert "aspectRatio" not in wire
    assert PresentationGenerationRequestV2.model_validate(wire).orientation == "portrait"


def test_candidate_stage_admits_implemented_routes_without_claiming_verified(monkeypatch):
    import services.document_canvas as module
    from services.document_canvas_capabilities import (
        DocumentCanvasRouteSelection, a4_canvas_capabilities, evaluate_a4_selection,
    )

    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", False)
    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED", True)
    response = a4_canvas_capabilities()
    assert response["stage"] == "candidate"
    html = next(route for route in response["routes"] if route["routeId"] == "authored-reflow-html")
    assert html["enabled"] is True
    assert html["verified"] is False
    assert evaluate_a4_selection(DocumentCanvasRouteSelection("fixed-source-exact-reconstruction"))[1] is False
    monkeypatch.setattr(module, "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED", True)
    assert evaluate_a4_selection(DocumentCanvasRouteSelection("authored-reflow-html"))[1] is False


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_scene_revision_layout_uses_target_safe_area_and_preserves_master(orientation):
    from models.authored_vnext.master.contract import DeckMasterContract
    from tests.support.authored_vnext_master_fixture import master_contract_payload
    from services.document_canvas_scene_layout import (
        document_scene_master, safe_title_bounds, safe_body_bounds,
    )

    master = DeckMasterContract.model_validate(master_contract_payload())
    original = master.model_dump(mode="json", by_alias=True)
    canvas = document_canvas_for_orientation(orientation)
    target = document_scene_master(master, canvas)
    assert target.canvas == canvas.canvas_contract()
    slots = target.layouts.standard.slots
    title = safe_title_bounds(next(slot.bounds for slot in slots if slot.role == "title"),
                              target, slide_role="standard")
    body = safe_body_bounds(next(slot.bounds for slot in slots if slot.role == "body"),
                            title_bounds=title, contract=target)
    assert body.y > title.bottom
    assert body.right <= canvas.safe_area.x + canvas.safe_area.width
    assert body.bottom <= canvas.safe_area.y + canvas.safe_area.height
    assert master.model_dump(mode="json", by_alias=True) == original


def test_revision_snapshot_pairs_server_canvas_not_caller_metadata(monkeypatch):
    import asyncio

    asyncio.run(_assert_revision_snapshot_pairs_server_canvas(monkeypatch))


async def _assert_revision_snapshot_pairs_server_canvas(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from services import presentation_version_service as versions

    wire = FIXTURES["a4"][1]["canvas"]
    presentation = SimpleNamespace(theme={"documentCanvas": wire})

    class Session:
        async def get(self, model, identity):
            return presentation

        def add(self, value):
            self.added = value

    async def no_prune(*args):
        pass

    monkeypatch.setattr(versions, "_prune", no_prune)
    snapshot = await versions.snapshot_slides(
        Session(), uuid.uuid4(),
        [{"index": 0, "html_content": "<div>fixture</div>", "_documentCanvas": {"bad": True}}],
        force=True,
    )
    assert snapshot.slides[0]["_documentCanvas"] == wire
    assert snapshot.slides[0]["_canvasDigest"] == FIXTURES["a4"][1]["digest"]
    live_slide = versions._slide_from_dump(uuid.uuid4(), snapshot.slides[0])
    assert "_documentCanvas" not in live_slide.model_dump()
