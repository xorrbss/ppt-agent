import pytest
from types import SimpleNamespace

from api.v1.ppt.endpoints.presentation_generate import (
    _durable_request_payload,
    _request_from_durable_payload,
)
from api.v2.contracts import PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from models.authored_vnext.master.contract import CanvasContract, Rect
from services.authored_canvas import (
    merge_authored_vnext_projection,
    request_canvas_contract,
    request_canvas_metadata,
    validate_authored_canvas_admission,
)
from services.authored_rollout_profile import RolloutProfile
from services.public_v2_orchestrator import _generation_request_hash_payload


def test_exact_snapshot_identity_round_trips_only_in_durable_payload():
    request = GeneratePresentationRequest(
        content="Private exact deck",
        template="authored",
        strategy="authored",
        authored_style="exact-private",
    )
    request._authored_exact_snapshot_id = "a" * 32

    public_payload = request.model_dump(mode="json")
    durable_payload = _durable_request_payload(request)
    restored = _request_from_durable_payload(durable_payload)

    assert "_internal_authored_exact_snapshot_id" not in public_payload
    assert durable_payload["_internal_authored_exact_snapshot_id"] == "a" * 32
    assert restored._authored_exact_snapshot_id == "a" * 32
    assert restored.model_dump(mode="json") == public_payload

    legacy_payload = dict(durable_payload)
    legacy_payload.pop("_internal_authored_canvas")
    legacy_restored = _request_from_durable_payload(legacy_payload)
    assert request_canvas_contract(legacy_restored).width_px == 1280


def test_style_reference_durable_payload_adds_only_the_authored_pins():
    """An authored job carries its pinned policies and nothing else extra.

    The durable payload is the public request plus the two server-owned pins an
    authored deck is fenced to for its whole life: the local-fit policy revision
    and the rollout profile (the five switches, frozen so a resumed attempt
    cannot re-represent a half-built deck).  Both round-trip.
    """

    request = GeneratePresentationRequest(
        content="Style deck", template="authored", strategy="authored", aspect_ratio="9:16"
    )

    durable = _durable_request_payload(request)
    public = request.model_dump(mode="json")
    pins = {key: durable[key] for key in durable.keys() - public.keys()}
    assert set(pins) == {
        "_internal_authored_local_fit_policy_revision",
        "_internal_authored_rollout_profile",
        "_internal_authored_canvas",
    }
    assert {key: durable[key] for key in public} == public
    assert pins["_internal_authored_rollout_profile"] == RolloutProfile().as_dict()
    assert pins["_internal_authored_canvas"] == {
        "contractVersion": "authored-canvas/v1",
        "aspectRatio": "9:16",
        "widthPx": 720,
        "heightPx": 1280,
        "locale": "ko-KR",
        "safeArea": {"x": 64.0, "y": 64.0, "width": 592.0, "height": 1152.0},
    }

    restored = _request_from_durable_payload(durable)
    assert restored._authored_rollout_profile == RolloutProfile().as_dict()
    assert restored._authored_canvas.width_px == 720
    assert restored._authored_canvas.height_px == 1280
    assert restored.model_dump(mode="json") == public

    missing_version = dict(durable)
    missing_version["_internal_authored_canvas"] = dict(
        durable["_internal_authored_canvas"]
    )
    missing_version["_internal_authored_canvas"].pop("contractVersion")
    with pytest.raises(ValueError, match="authored_canvas_contract_invalid"):
        _request_from_durable_payload(missing_version)

    explicit_null = dict(durable)
    explicit_null["_internal_authored_canvas"] = None
    with pytest.raises(ValueError, match="authored_canvas_contract_invalid"):
        _request_from_durable_payload(explicit_null)

    master_request = GeneratePresentationRequest(
        content="Master deck", template="authored"
    )
    master_request._authored_master_contract = SimpleNamespace(
        canvas=CanvasContract(
            widthPx=1280,
            heightPx=720,
            locale="en-US",
            safeArea=Rect(x=60, y=80, width=1160, height=560),
        )
    )
    master_metadata = request_canvas_metadata(master_request)
    assert master_metadata["locale"] == "en-US"
    assert master_metadata["safeArea"] == {
        "x": 60.0,
        "y": 80.0,
        "width": 1160.0,
        "height": 560.0,
    }

    omitted_landscape = PresentationGenerationRequestV2(content="Deck")
    explicit_landscape = PresentationGenerationRequestV2(
        content="Deck", aspectRatio="16:9"
    )
    portrait = PresentationGenerationRequestV2(content="Deck", aspectRatio="9:16")
    assert _generation_request_hash_payload(
        omitted_landscape
    ) == _generation_request_hash_payload(explicit_landscape)
    assert "aspectRatio" not in _generation_request_hash_payload(explicit_landscape)
    assert _generation_request_hash_payload(portrait)["aspectRatio"] == "9:16"

    unsupported_template = GeneratePresentationRequest(
        content="Deck", template="custom", aspect_ratio="9:16"
    )
    with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
        validate_authored_canvas_admission(
            unsupported_template,
            style_id="default",
        )

    unsupported_engine = GeneratePresentationRequest(
        content="Deck",
        template="authored",
        aspect_ratio="9:16",
        generationEngine="deck-ir",
    )
    with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
        validate_authored_canvas_admission(
            unsupported_engine,
            style_id="default",
        )

    published_theme = merge_authored_vnext_projection(
        {
            "existingThemeKey": "preserved",
            "authored_vnext": {
                "canvas": pins["_internal_authored_canvas"],
                "existingSnapshotKey": "preserved",
                "schemaVersion": "older-projection/v1",
            },
        },
        {
            "schemaVersion": "authored-editable-hybrid-scene-projection/v1",
            "deckRevision": 1,
        },
    )
    assert published_theme["existingThemeKey"] == "preserved"
    assert published_theme["authored_vnext"]["canvas"] == pins[
        "_internal_authored_canvas"
    ]
    assert published_theme["authored_vnext"]["existingSnapshotKey"] == "preserved"
    assert (
        published_theme["authored_vnext"]["schemaVersion"]
        == "authored-editable-hybrid-scene-projection/v1"
    )
