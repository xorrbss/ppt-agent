"""Server-owned routing and durable placeholder policy for public generation."""

from collections.abc import Mapping
from typing import Any

from api.v2.contracts import GenerationRouteReceiptV1, PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_rollout_profile import RolloutProfile


def effective_generation_engine(
    request: PresentationGenerationRequestV2,
) -> str:
    if request.source_mode == "source_content_restyle":
        return "authored"
    if request.generation_mode == "registered-template":
        # The design-pack route receipt and source-native handler own this
        # operation.  Sending it through bounded Deck IR preflight would reject
        # valid packs with more than ten requested output pages before the
        # provider-free capacity admission can run.
        return "authored"
    if "generation_mode" in request.model_fields_set:
        return "deck-ir"
    return request.generation_engine or "authored"


def pin_source_restyle_authoring(request: GeneratePresentationRequest) -> None:
    """Pin new verbatim jobs before staging; resumed jobs keep their stored policy."""
    if request.source_mode == "source_content_restyle":
        object.__setattr__(request, "_source_content_sealed_authored_route", True)
        request._composer_restyle = False
    if (
        request.source_mode == "source_content_restyle"
        and request.content_policy is not None
        and request.content_policy.text == "verbatim"
    ):
        request._authored_rollout_profile = RolloutProfile(
            gate_mode="enforce",
            html_normalization="tree",
            placeholder_authoring=True,
            single_repair_loop=True,
            repair_loop_model_calls=1,
        ).as_dict()


def restore_source_restyle_route(
    request: GeneratePresentationRequest, payload: Mapping[str, Any],
) -> None:
    """Restore only the explicit Authored route; historical jobs retain their route."""
    sealed_authored = False
    raw_receipt = payload.get("generation_route_receipt")
    if request.source_mode == "source_content_restyle" and raw_receipt is not None:
        receipt = GenerationRouteReceiptV1.model_validate(raw_receipt)
        sealed_authored = (
            receipt.selected_route == "authored-fallback"
            and receipt.fallback_reason == "source-content-authored-reflow"
        )
    object.__setattr__(request, "_source_content_sealed_authored_route", sealed_authored)
    if sealed_authored:
        request._composer_restyle = False
