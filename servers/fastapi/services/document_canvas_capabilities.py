"""Single policy source for document-canvas writer admission and UI capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


RouteClassification = Literal["essential", "optional", "preservation"]


@dataclass(frozen=True)
class DocumentCanvasRoutePolicy:
    route_id: str
    classification: RouteClassification
    writer: bool
    implemented: bool
    verified: bool
    required_consumers: tuple[str, ...]
    reason: str | None = None

    @property
    def ready(self) -> bool:
        return self.implemented and self.verified


@dataclass(frozen=True)
class DocumentCanvasRouteSelection:
    route_id: str
    error_code: str | None = None


# Verification is deliberately independent from implementation.  A route is
# changed to verified only after its product-path fixture/readback has passed.
A4_ROUTE_POLICIES: tuple[DocumentCanvasRoutePolicy, ...] = (
    DocumentCanvasRoutePolicy(
        "authored-reflow-html",
        "essential",
        True,
        True,
        False,
        ("authored-html-generation", "public-html-export"),
        "document_canvas_product_readback_pending",
    ),
    DocumentCanvasRoutePolicy(
        "authored-reflow-svg",
        "essential",
        True,
        True,
        False,
        ("svg-generation", "public-svg-export"),
        "document_canvas_product_readback_pending",
    ),
    DocumentCanvasRoutePolicy(
        "source-content-authored-restyle",
        "essential",
        True,
        True,
        False,
        ("source-content-authored-reflow", "authored-html-generation", "public-html-export"),
        "document_canvas_product_readback_pending",
    ),
    DocumentCanvasRoutePolicy(
        "fixed-master-source-native",
        "essential",
        True,
        True,
        False,
        ("source-native-generation", "document-canvas-contain"),
        "document_canvas_product_readback_pending",
    ),
    DocumentCanvasRoutePolicy(
        "fixed-source-exact-reconstruction",
        "optional",
        True,
        False,
        False,
        ("exact-reconstruction",),
        "document_canvas_fixed_source_not_supported",
    ),
    DocumentCanvasRoutePolicy(
        "authored-vnext-native-export",
        "optional",
        False,
        True,
        False,
        ("authored-vnext-export-worker", "native-scene-export"),
        "document_canvas_product_readback_pending",
    ),
    DocumentCanvasRoutePolicy(
        "scene-lifecycle",
        "optional",
        False,
        True,
        False,
        ("scene-regeneration", "scene-rollback"),
        "document_canvas_product_readback_pending",
    ),
    DocumentCanvasRoutePolicy(
        "legacy-master-bridge",
        "preservation",
        False,
        False,
        False,
        ("legacy-reader",),
        "document_canvas_production_route_unconfirmed",
    ),
    DocumentCanvasRoutePolicy(
        "source-content-semantic-native-publication",
        "preservation",
        False,
        False,
        False,
        ("source-content-publication", "native-scene-export"),
        "document_canvas_production_route_unconfirmed",
    ),
)


def _policy_map() -> dict[str, DocumentCanvasRoutePolicy]:
    return {policy.route_id: policy for policy in A4_ROUTE_POLICIES}


def _release_switch_enabled() -> bool:
    from services.document_canvas import DOCUMENT_CANVAS_A4_RELEASE_VERIFIED

    return DOCUMENT_CANVAS_A4_RELEASE_VERIFIED


def a4_writer_stage() -> Literal["disabled", "candidate", "verified"]:
    from services.document_canvas import DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED

    if _release_switch_enabled():
        return "verified"
    return "candidate" if DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED else "disabled"


def a4_writer_admission_enabled() -> bool:
    return a4_writer_stage() != "disabled"


def _route_admitted(policy: DocumentCanvasRoutePolicy) -> bool:
    stage = a4_writer_stage()
    return policy.ready if stage == "verified" else stage == "candidate" and policy.implemented


def select_a4_route(
    *,
    strategy: str | None,
    source_content: bool,
    has_fixed_master: bool,
    exact_source: bool,
    template_authored: bool = True,
    generation_engine_supported: bool = True,
    generation_mode_supported: bool = True,
) -> DocumentCanvasRouteSelection:
    if (
        not template_authored
        or not generation_engine_supported
        or not generation_mode_supported
        or (source_content and strategy == "svg")
    ):
        route_id = (
            "source-content-authored-restyle"
            if source_content
            else "authored-reflow-svg" if strategy == "svg" else "authored-reflow-html"
        )
        return DocumentCanvasRouteSelection(
            route_id, "document_canvas_generation_combination_not_supported"
        )
    if has_fixed_master:
        return DocumentCanvasRouteSelection("fixed-master-source-native")
    if exact_source:
        return DocumentCanvasRouteSelection("fixed-source-exact-reconstruction")
    if source_content:
        return DocumentCanvasRouteSelection("source-content-authored-restyle")
    if strategy == "svg":
        return DocumentCanvasRouteSelection("authored-reflow-svg")
    return DocumentCanvasRouteSelection("authored-reflow-html")


def evaluate_a4_selection(
    selection: DocumentCanvasRouteSelection,
) -> tuple[DocumentCanvasRoutePolicy, bool, str | None]:
    policy = _policy_map()[selection.route_id]
    if not a4_writer_admission_enabled():
        return policy, False, "document_canvas_a4_not_available"
    if selection.error_code is not None:
        return policy, False, selection.error_code
    if not _route_admitted(policy):
        return policy, False, policy.reason or "document_canvas_route_not_verified"
    return policy, True, None


def evaluate_a4_request(
    request: Any, *, reference_mode: str | None
) -> tuple[DocumentCanvasRoutePolicy, bool, str | None]:
    selection = select_a4_route(
        strategy=getattr(request, "strategy", None),
        source_content=getattr(request, "source_mode", None) == "source_content_restyle",
        has_fixed_master=(
            getattr(request, "master_pin", None) is not None
            or getattr(request, "design_pack_pin", None) is not None
            or getattr(request, "_authored_master_pin", None) is not None
        ),
        exact_source=(
            reference_mode == "exact_reconstruction"
            or getattr(request, "_authored_exact_snapshot_id", None) is not None
        ),
        template_authored=str(getattr(request, "template", "") or "").strip().lower() == "authored",
        generation_engine_supported=getattr(request, "generation_engine", None) in {None, "authored"},
        generation_mode_supported=getattr(request, "generation_mode", None) in {"auto", "free-design"},
    )
    return evaluate_a4_selection(selection)


def _route_wire(policy: DocumentCanvasRoutePolicy) -> dict[str, Any]:
    enabled = (
        policy.classification != "preservation"
        and _route_admitted(policy)
    )
    return {
        "routeId": policy.route_id,
        "classification": policy.classification,
        "writer": policy.writer,
        "implemented": policy.implemented,
        "verified": policy.verified,
        "enabled": enabled,
        "stage": a4_writer_stage(),
        "requiredConsumers": list(policy.required_consumers),
        "reason": (
            None
            if enabled
            else policy.reason or "document_canvas_route_not_verified"
            if not policy.ready
            else "document_canvas_a4_not_available"
        ),
    }


def a4_canvas_capabilities(
    selection: DocumentCanvasRouteSelection | None = None,
) -> dict[str, Any]:
    routes = [_route_wire(policy) for policy in A4_ROUTE_POLICIES]
    writer_enabled = any(route["enabled"] for route in routes if route["writer"])
    result: dict[str, Any] = {
        "enabled": writer_enabled,
        "stage": a4_writer_stage(),
        "reason": (
            None
            if writer_enabled
            else "document_canvas_a4_not_available"
            if not a4_writer_admission_enabled()
            else "document_canvas_no_verified_writer_route"
        ),
        "routes": routes,
    }
    if selection is not None:
        policy, enabled, reason = evaluate_a4_selection(selection)
        result["selection"] = {
            "routeId": policy.route_id,
            "enabled": enabled,
            "reason": reason,
        }
    return result


__all__ = [
    "A4_ROUTE_POLICIES",
    "DocumentCanvasRoutePolicy",
    "DocumentCanvasRouteSelection",
    "a4_canvas_capabilities",
    "a4_writer_admission_enabled",
    "a4_writer_stage",
    "evaluate_a4_request",
    "evaluate_a4_selection",
    "select_a4_route",
]
