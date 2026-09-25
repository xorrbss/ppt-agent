"""Server-owned routing and durable placeholder policy for public generation."""

from hashlib import sha256
import json

from collections.abc import Mapping
from typing import Any

from api.v2.contracts import GenerationRouteReceiptV1, PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_rollout_profile import RolloutProfile

COMPOSER_FIT_POLICY_KEY = "_internal_composer_fit_policy"
COMPOSER_FIT_POLICY = {"revision": "composer-source-fit/v2", "font_floor_px": 7.0}


def effective_generation_engine(
    request: PresentationGenerationRequestV2,
) -> str:
    if request.strategy == "svg":
        return "authored"
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


def pin_source_restyle_authoring(
    request: GeneratePresentationRequest,
) -> dict[str, object] | None:
    """Pin new source restyles to Composer; resumed jobs keep their stored policy."""
    route_receipt: dict[str, object] | None = None
    if request.strategy == "svg":
        request._composer_restyle = False
        return None
    if request.source_mode == "source_content_restyle":
        from utils.authored_styles import resolve_authored_style

        if getattr(request, "_authored_style_snapshot", None) is None:
            request._authored_style_snapshot = resolve_authored_style(request.authored_style)
        object.__setattr__(request, "_source_content_sealed_authored_route", False)
        request._composer_restyle = True
        object.__setattr__(request, "_composer_fit_policy", dict(COMPOSER_FIT_POLICY))
        unsigned_receipt: dict[str, object] = {
            "schemaVersion": "generation-route-receipt/v1",
            "requestedMode": request.generation_mode,
            "selectedRoute": "authored-fallback",
            "designPackPin": None,
            "narrativeIrDigest": None,
            "fallbackReason": "source-content-composer-reflow",
            "outputCapability": "fidelity",
        }
        digest = "sha256:" + sha256(
            json.dumps(
                unsigned_receipt,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        route_receipt = GenerationRouteReceiptV1.model_validate(
            {**unsigned_receipt, "receiptDigest": digest}
        ).model_dump(mode="json", by_alias=True)
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
    return route_receipt


def restore_source_restyle_route(
    request: GeneratePresentationRequest, payload: Mapping[str, Any],
) -> None:
    """Restore the durable source-restyle route before live admission switches."""
    sealed_authored = False
    sealed_composer = False
    if request.strategy == "svg":
        request._composer_restyle = False
        return
    raw_receipt = payload.get("generation_route_receipt")
    if request.source_mode == "source_content_restyle" and raw_receipt is not None:
        receipt = GenerationRouteReceiptV1.model_validate(raw_receipt)
        sealed_authored = (
            receipt.selected_route == "authored-fallback"
            and receipt.fallback_reason == "source-content-authored-reflow"
        )
        sealed_composer = (
            receipt.selected_route == "authored-fallback"
            and receipt.fallback_reason == "source-content-composer-reflow"
        )
    object.__setattr__(request, "_source_content_sealed_authored_route", sealed_authored)
    # Absence is the historical policy, never permission to upgrade a replay.
    policy = payload.get(COMPOSER_FIT_POLICY_KEY)
    object.__setattr__(request, "_composer_fit_policy", None)
    if sealed_composer and policy is not None:
        if not isinstance(policy, Mapping) or dict(policy) != COMPOSER_FIT_POLICY:
            from services.composer.receipt import ComposerPipelineError

            raise ComposerPipelineError("composer_fit_policy_invalid")
        object.__setattr__(request, "_composer_fit_policy", dict(COMPOSER_FIT_POLICY))
    if sealed_authored:
        request._composer_restyle = False
    elif sealed_composer:
        request._composer_restyle = True
