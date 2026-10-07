"""Whole-operation Deck IR routing and capability preflight.

This module deliberately owns no provider or publication side effects.  The
decision is made during durable admission, before a provider reservation can
be created, then its digest is carried by the operation until it is terminal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import unicodedata
from typing import Literal
from utils.coerce import as_mapping

from utils.env_settings.authored_vnext import is_deck_ir_generation_enabled
from utils.hashing import sha256_prefixed


GenerationStrategy = Literal["deck-ir", "legacy-authored", "reject"]
_STRATEGIES = frozenset(("deck-ir", "legacy-authored", "reject"))


class GenerationStrategyCapabilityError(ValueError):
    """Stable, content-free rejection raised before provider reservation."""

    code = "generation_strategy_capability_missing"


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Use WP1 canonicalization when it is available without startup coupling."""

    try:
        from services.authored_vnext.deck_ir.canonical import canonical_json

        encoded = canonical_json(payload)
        if isinstance(encoded, bytes):
            return encoded.decode("utf-8")
        if isinstance(encoded, str):
            return encoded
    except (ImportError, TypeError, ValueError):
        # During rolling upgrades WP1 can be deployed after this integration.
        # The fallback is intentionally deterministic and is only used to
        # construct a receipt, never to accept an unvalidated Deck IR.
        pass
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(payload: Mapping[str, object]) -> str:
    try:
        from services.authored_vnext.deck_ir.canonical import canonical_digest

        value = canonical_digest(payload)
        if isinstance(value, str) and value:
            return value
    except (ImportError, TypeError, ValueError):
        pass
    return sha256_prefixed(_canonical_json(payload).encode("utf-8"))


def deck_ir_generation_enabled() -> bool:
    """Return the admission kill switch; default is safely legacy authored."""

    return is_deck_ir_generation_enabled()


def _requested_capabilities(request: object) -> tuple[str, ...]:
    """Extract only public, non-content capability declarations."""

    values: list[object] = []
    output = as_mapping(getattr(request, "output", None))
    values.extend(as_mapping(getattr(request, "model_dump", lambda **_: {})()).get("required_capabilities", ()) if callable(getattr(request, "model_dump", None)) else ())
    values.extend(output.get("required_capabilities", ()))
    values.extend(getattr(request, "required_capabilities", ()) or ())
    return tuple(
        sorted(
            {
                str(value).strip()
                for value in values
                if isinstance(value, str) and value.strip()
            }
        )
    )


def capability_preflight(
    *,
    request: object,
    available_capabilities: Sequence[str] = (),
    grammar_capability_digest: str | None = None,
    compiler_capability_digest: str | None = None,
) -> dict[str, object]:
    """Evaluate all route-critical capabilities without inspecting provider data."""

    if grammar_capability_digest is None or compiler_capability_digest is None:
        try:
            from services.authored_vnext.deck_ir.grammar import (
                COMPILER_VERSION,
                RENDERER_ABI_VERSION,
                SOLVER_ABI_VERSION,
                default_design_grammar,
                digest,
            )

            grammar_capability_digest = (
                grammar_capability_digest or default_design_grammar().manifest_digest
            )
            compiler_capability_digest = compiler_capability_digest or digest(
                {
                    "compilerVersion": COMPILER_VERSION,
                    "rendererAbiVersion": RENDERER_ABI_VERSION,
                    "solverAbiVersion": SOLVER_ABI_VERSION,
                }
            )
        except ImportError as error:
            raise GenerationStrategyCapabilityError(
                "deck_ir_compiler_runtime_unavailable"
            ) from error
    available = {str(item).strip() for item in available_capabilities if str(item).strip()}
    requested = _requested_capabilities(request)
    # These are server capabilities, not model claims.  Requiring them here
    # prevents a feature flag from routing an operation into an uninstalled
    # compiler/provider bridge after a reservation has already been created.
    required_engine = {
        "structured-deck-ir", "constraint-compiler", "fidelity-html-semantic"
    }
    missing = (set(requested) | required_engine) - available
    slide_count = getattr(request, "n_slides", None)
    if not isinstance(slide_count, int) or isinstance(slide_count, bool):
        missing.add("slide-count")
    # The first real product adapter uses the bounded global + 3/2 batch plan.
    # Do not admit a larger operation only to fail after durable assignment.
    elif slide_count < 1 or slide_count > 10:
        missing.add("slide-count")
    template = str(getattr(request, "template", "")).strip().lower()
    if template != "authored":
        missing.add("authored-fidelity")
    reference_snapshots = getattr(request, "reference_snapshot_ids", ()) or ()
    if reference_snapshots:
        # The first product adapter does not yet materialize owner-bound
        # SourceBinding documents from reference snapshots.  Route the whole
        # operation to legacy until that capability is installed; never omit
        # source coverage merely to make a Deck IR request fit.
        missing.add("source-binding")
    # The current Deck IR compiler is Fidelity-only.  Never represent an
    # editable/native request as a raster success.
    output = as_mapping(getattr(request, "output", None))
    if str(output.get("mode", "")).strip().lower() == "editable-hybrid":
        missing.add("editable-hybrid")
    style_profile_digest: str | None = None
    retained_reference_digest: str | None = None
    try:
        from services.authored_vnext.deck_ir.style_profiles import (
            design_grammar_for_style,
            resolve_style_profile,
        )

        style_profile = resolve_style_profile(getattr(request, "authored_style", None))
        style_profile_digest = style_profile.profile_digest
        retained_reference_digest = style_profile.retained_reference_digest
        grammar_capability_digest = design_grammar_for_style(style_profile).manifest_digest
    except (ImportError, ValueError):
        missing.add("style-profile")
    payload: dict[str, object] = {
        "schemaVersion": "generation-strategy-preflight/v1",
        "grammarCapabilityDigest": grammar_capability_digest,
        "compilerCapabilityDigest": compiler_capability_digest,
        "styleProfileDigest": style_profile_digest,
        "retainedReferenceDigest": retained_reference_digest,
        "requestedCapabilities": list(requested),
        "availableCapabilities": sorted(available),
        "missingCapabilities": sorted(missing),
        "status": "passed" if not missing else "failed",
    }
    payload["preflightDigest"] = _digest(payload)
    return payload


def server_owned_requirement_graph(brief: str) -> dict[str, object]:
    """Normalize the source-free public brief without retaining its text.

    The returned graph contains only the fixed required claim identity and a
    digest of the brief.  Callers persist only the graph digest/coverage, never
    the provider prompt or the normalized input body.
    """

    try:
        from services.authored_vnext.deck_ir.canonical import canonical_digest, seal_contract
        from services.authored_vnext.deck_ir.contracts import RequirementGraphV1

        normalized_intent = " ".join(
            unicodedata.normalize("NFC", brief).split()
        )[:2000]
        if not normalized_intent:
            normalized_intent = "empty-public-brief"
        graph = seal_contract(
            {
                "schemaVersion": "requirement-graph/v1",
                "requestDigest": canonical_digest({"brief": brief}),
                "requirements": [{
                    "requirementId": "req-001",
                    "kind": "claim",
                    "priority": "required",
                    "normalizedIntent": normalized_intent,
                    "sourceBindingIds": [],
                    "minimumOccurrences": 1,
                    "maximumOccurrences": 30,
                    "allowedSlideRoles": [],
                }],
            },
            model_type=RequirementGraphV1,
        )
        return dict(graph.model_dump(mode="json", by_alias=True))
    except ImportError as error:
        raise GenerationStrategyCapabilityError(
            "deck_ir_contract_runtime_unavailable"
        ) from error


def seal_generation_strategy_decision(
    *,
    operation_id: str,
    requirement_graph_digest: str,
    requested_profile: str,
    preflight: Mapping[str, object],
    rollout_phase: str = "D0",
    force_strategy: GenerationStrategy | None = None,
    requested_generation_engine: str = "auto",
) -> dict[str, object]:
    """Seal a single route choice.  The receipt is safe to put in operation JSON."""

    missing = preflight.get("missingCapabilities")
    missing_list = (
        sorted(str(item) for item in missing if isinstance(item, str))
        if isinstance(missing, Sequence) and not isinstance(missing, (str, bytes))
        else []
    )
    phase = rollout_phase.strip().upper() or "D0"
    if force_strategy is not None:
        selected = force_strategy
        reason = (
            "explicit-capability-missing"
            if force_strategy == "reject" and missing_list
            else "explicit-admission-policy"
        )
    elif not deck_ir_generation_enabled():
        selected = "legacy-authored"
        reason = "deck-ir-generation-disabled"
    elif missing_list:
        selected = "reject" if phase >= "D6" else "legacy-authored"
        reason = "required-capability-missing"
    else:
        selected = "deck-ir"
        reason = "all-required-capabilities-supported"
    if selected not in _STRATEGIES:
        raise ValueError("generation_strategy_invalid")
    receipt: dict[str, object] = {
        "schemaVersion": "generation-strategy-decision-receipt/v1",
        "operationId": operation_id,
        "requirementGraphDigest": requirement_graph_digest,
        "requestedProfile": requested_profile,
        "requestedGenerationEngine": requested_generation_engine,
        "grammarCapabilityDigest": preflight.get("grammarCapabilityDigest"),
        "compilerCapabilityDigest": preflight.get("compilerCapabilityDigest"),
        "styleProfileDigest": preflight.get("styleProfileDigest"),
        "retainedReferenceDigest": preflight.get("retainedReferenceDigest"),
        "providerCallPlan": {
            "globalPlanCalls": 1,
            "maximumSlideBatchCalls": 3,
            "maximumLogicalCalls": 4,
            "candidateProviderCallsAdded": 0,
        },
        "rolloutPhase": phase,
        "selectedStrategy": selected,
        "missingCapabilities": missing_list,
        "decisionReason": reason,
        "preflightDigest": preflight.get("preflightDigest"),
    }
    receipt["receiptDigest"] = _digest(receipt)
    return receipt


def validate_sealed_generation_strategy(
    receipt: Mapping[str, object],
    *,
    operation_id: str | None = None,
) -> GenerationStrategy:
    """Refuse unsealed, mixed, or operation-mismatched decisions."""

    strategy = receipt.get("selectedStrategy")
    if strategy not in _STRATEGIES:
        raise GenerationStrategyCapabilityError("generation_strategy_invalid")
    if operation_id is not None and receipt.get("operationId") != operation_id:
        raise GenerationStrategyCapabilityError("generation_strategy_operation_mismatch")
    supplied = receipt.get("receiptDigest")
    unsigned = {key: value for key, value in receipt.items() if key != "receiptDigest"}
    if not isinstance(supplied, str) or supplied != _digest(unsigned):
        raise GenerationStrategyCapabilityError("generation_strategy_receipt_invalid")
    return strategy


def rebind_sealed_generation_strategy(receipt: Mapping[str, object], *,
                                      source_operation_id: str, operation_id: str) -> dict[str, object]:
    """Preserve an admitted decision while binding a verified retry identity."""
    validate_sealed_generation_strategy(receipt, operation_id=source_operation_id)
    rebound = {**receipt, "operationId": operation_id}
    rebound["receiptDigest"] = _digest({key: value for key, value in rebound.items() if key != "receiptDigest"})
    validate_sealed_generation_strategy(rebound, operation_id=operation_id)
    return rebound


__all__ = [
    "GenerationStrategyCapabilityError",
    "capability_preflight",
    "deck_ir_generation_enabled",
    "seal_generation_strategy_decision",
    "server_owned_requirement_graph",
    "validate_sealed_generation_strategy",
    "rebind_sealed_generation_strategy",
]
