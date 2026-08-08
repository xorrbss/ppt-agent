from api.v1.ppt.endpoints.presentation_generate import (
    _durable_request_payload,
    _request_from_durable_payload,
)
from models.generate_presentation_request import GeneratePresentationRequest


def test_exact_snapshot_identity_round_trips_only_in_durable_payload():
    request = GeneratePresentationRequest(
        content="Private exact deck",
        template="authored",
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


def test_style_reference_durable_payload_shape_is_unchanged():
    request = GeneratePresentationRequest(content="Style deck", template="authored")

    assert _durable_request_payload(request) == request.model_dump(mode="json")
