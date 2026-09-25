"""Server-owned routing and durable placeholder policy for public generation."""

from hashlib import sha256
import json

from collections.abc import Mapping
from typing import Any

from api.v2.contracts import GenerationRouteReceiptV1, PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_rollout_profile import RolloutProfile

#: Durable key the withdrawn server-owned restyle composer wrote into staged
#: job payloads. Nothing writes it any more; staging only strips it so a
#: payload staged before the removal still validates on replay.
LEGACY_RESTYLE_FIT_POLICY_KEY = "_internal_composer_fit_policy"


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
    """Pin new source restyles to the authored reflow before staging."""
    route_receipt: dict[str, object] | None = None
    if request.strategy == "svg":
        return None
    if request.source_mode == "source_content_restyle":
        from utils.authored_styles import resolve_authored_style

        if getattr(request, "_authored_style_snapshot", None) is None:
            request._authored_style_snapshot = resolve_authored_style(request.authored_style)
        unsigned_receipt: dict[str, object] = {
            "schemaVersion": "generation-route-receipt/v1",
            "requestedMode": request.generation_mode,
            "selectedRoute": "authored-fallback",
            "designPackPin": None,
            "narrativeIrDigest": None,
            "fallbackReason": "source-content-authored-reflow",
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
    """Validate the durable source-restyle route receipt before replay.

    Every source restyle runs the ordinary authored reflow.  Receipts sealed
    by the withdrawn server-owned composer (``source-content-composer-reflow``)
    replay through that same reflow, which was also its disabled-flag path.
    """
    if request.strategy == "svg":
        return
    raw_receipt = payload.get("generation_route_receipt")
    if request.source_mode == "source_content_restyle" and raw_receipt is not None:
        GenerationRouteReceiptV1.model_validate(raw_receipt)
