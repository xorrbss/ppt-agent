"""Server-owned routing and durable placeholder policy for public generation."""


from collections.abc import Mapping
from typing import Any

from models.public_v2_contracts import GenerationRouteReceiptV1, PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_rollout_profile import RolloutProfile
from services.generation_core.admission.strategy import resolve_generation_strategy
from utils.hashing import canonical_json_digest

#: Durable key the withdrawn server-owned restyle composer wrote into staged
#: job payloads. Nothing writes it any more; staging only strips it so a
#: payload staged before the removal still validates on replay.
LEGACY_RESTYLE_FIT_POLICY_KEY = "_internal_composer_fit_policy"


class GenerationRouteError(ValueError):
    """A deterministic route-resolution rejection (a client-facing 422)."""

    code = "generation_route_invalid"

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


#: Generation modes a public request may still ask for. ``registered-template``
#: ran the design-pack (source-native) route, removed on 2026-11-03.
ROUTABLE_GENERATION_MODES = frozenset({"auto", "brand-style", "free-design", "fidelity-first", "editable-first"})


def resolve_authored_generation_route(
    *,
    generation_mode: str | None = None,
    source_mode: str | None = None,
    authored_style: str | None = None,
    **_: Any,
) -> dict[str, object]:
    """Resolve an authored-strategy public request to its route, once, at admission.

    Every authored request now runs the authored pipeline; the receipt keeps
    its ``designPackPin``/``narrativeIrDigest`` fields (always null) because
    stored receipts of earlier operations are digest-sealed with them.
    """

    mode = (generation_mode or "auto").strip()
    if mode not in ROUTABLE_GENERATION_MODES:
        raise GenerationRouteError("generation_mode_invalid")
    if mode == "editable-first":
        raise GenerationRouteError("capability_not_supported")
    if source_mode == "source_content_restyle":
        if not str(authored_style or "").strip():
            raise GenerationRouteError("source_content_template_required")
        fallback_reason = "source-content-authored-reflow"
    else:
        fallback_reason = "authored-route"
    return {
        "selectedRoute": "authored-fallback",
        "designPackPin": None,
        "narrativeIrDigest": None,
        "fallbackReason": fallback_reason,
    }


def effective_generation_engine(
    request: PresentationGenerationRequestV2,
) -> str:
    # The strategy resolver decides svg-vs-authored first (svg is the
    # default). Only when it resolves to authored does the pre-svg-default
    # engine table apply -- an omitted strategy no longer falls through to
    # the deck-ir auto-route just because generationMode was also set.
    if resolve_generation_strategy(request) == "svg":
        return "authored"
    if request.source_mode == "source_content_restyle":
        return "authored"
    if "generation_mode" in request.model_fields_set:
        return "deck-ir"
    return request.generation_engine or "authored"


def pin_source_restyle_authoring(
    request: GeneratePresentationRequest,
) -> dict[str, object] | None:
    """Pin new source restyles to the authored reflow before staging."""
    route_receipt: dict[str, object] | None = None
    if resolve_generation_strategy(request) == "svg":
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
        digest = canonical_json_digest(unsigned_receipt, ensure_ascii=True, allow_nan=True)
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
    if resolve_generation_strategy(request) == "svg":
        return
    raw_receipt = payload.get("generation_route_receipt")
    if request.source_mode == "source_content_restyle" and raw_receipt is not None:
        GenerationRouteReceiptV1.model_validate(raw_receipt)
