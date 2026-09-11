from api.v1.ppt.endpoints.presentation_generate import (
    _durable_request_payload,
    _request_from_durable_payload,
)
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_rollout_profile import RolloutProfile


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


def test_style_reference_durable_payload_adds_only_the_authored_pins():
    """An authored job carries its pinned policies and nothing else extra.

    The durable payload is the public request plus the two server-owned pins an
    authored deck is fenced to for its whole life: the local-fit policy revision
    and the rollout profile (the five switches, frozen so a resumed attempt
    cannot re-represent a half-built deck).  Both round-trip.
    """

    request = GeneratePresentationRequest(content="Style deck", template="authored")

    durable = _durable_request_payload(request)
    public = request.model_dump(mode="json")
    pins = {key: durable[key] for key in durable.keys() - public.keys()}
    assert set(pins) == {
        "_internal_authored_local_fit_policy_revision",
        "_internal_authored_rollout_profile",
    }
    assert {key: durable[key] for key in public} == public
    assert pins["_internal_authored_rollout_profile"] == RolloutProfile().as_dict()

    restored = _request_from_durable_payload(durable)
    assert restored._authored_rollout_profile == RolloutProfile().as_dict()
    assert restored.model_dump(mode="json") == public
