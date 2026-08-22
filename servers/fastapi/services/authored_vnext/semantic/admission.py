"""Server-owned reconciliation and thin text/image/shape admission registry."""

from __future__ import annotations

from dataclasses import dataclass

from models.authored_vnext.semantic.contracts import (
    AdmissionElementV1,
    AuthoredSemanticSourceV1,
    BrowserObservationReceiptV1,
    ImageElementV1,
    NativeAdmissionReceiptV1,
    ShapeElementV1,
    TextElementV1,
)

from .canonical import SemanticContractError, canonical_digest, seal_derived_receipt


@dataclass(frozen=True)
class SerializerRegistryV1:
    serializer_id: str = "authored-hybrid-native"
    serializer_version: str = "1"
    reconciliation_policy_version: str = "reconciliation-policy/v1"
    text_features: frozenset[str] = frozenset({"plain-text", "mixed-runs", "explicit-breaks"})
    image_features: frozenset[str] = frozenset({"crop", "object-fit", "object-position"})
    shape_features: frozenset[str] = frozenset({"solid-fill", "solid-stroke", "radius"})


def reconcile_and_issue_native_admission(
    *,
    input_bundle_digest: str,
    source: AuthoredSemanticSourceV1,
    observation: BrowserObservationReceiptV1,
    registry: SerializerRegistryV1 = SerializerRegistryV1(),
) -> NativeAdmissionReceiptV1:
    """Fail publication integrity mismatches; rasterize expression mismatches."""

    if observation.input_bundle_digest != input_bundle_digest:
        raise SemanticContractError("authored_semantic_observation_bundle_mismatch")
    if observation.receipt_digest is None:
        observation = seal_derived_receipt(observation)  # type: ignore[assignment]
    if not observation.fonts_ready or not observation.images_ready:
        raise SemanticContractError("authored_semantic_observation_resources_unready")
    if observation.unresolved_resource_digests:
        raise SemanticContractError("authored_semantic_observation_resource_unresolved")
    declared = {element.element_id: element for element in source.elements}
    observed = {element.element_id: element for element in observation.elements}
    if len(observed) != len(observation.elements) or set(observed) != set(declared):
        raise SemanticContractError("authored_semantic_observation_identity_mismatch")
    results: list[AdmissionElementV1] = []
    for element_id, element in declared.items():
        fact = observed[element_id]
        if fact.parent_id != element.parent_id:
            raise SemanticContractError("authored_semantic_observation_ownership_mismatch")
        expected_source = _element_source_digest(element)
        if fact.source_digest != expected_source:
            raise SemanticContractError("authored_semantic_observation_source_mismatch")
        features = sorted(set(element.required_features).union(fact.observed_features))
        supported = _supported_features(element, registry)
        unsupported = (
            features if supported is None else sorted(set(features).difference(supported))
        )
        if supported is None or unsupported:
            results.append(
                AdmissionElementV1(
                    element_id=element_id,
                    kind=element.kind,
                    state="rasterFallback",
                    fallback_reason=(
                        "serializer_kind_unsupported"
                        if supported is None
                        else "serializer_feature_unsupported"
                    ),
                )
            )
        else:
            results.append(
                AdmissionElementV1(
                    element_id=element_id,
                    kind=element.kind,
                    state="admitted",
                    admitted_features=features,
                )
            )
    receipt = NativeAdmissionReceiptV1(
        input_bundle_digest=input_bundle_digest,
        observation_receipt_digest=observation.receipt_digest,
        reconciliation_policy_version=registry.reconciliation_policy_version,
        serializer_id=registry.serializer_id,
        serializer_version=registry.serializer_version,
        elements=results,
    )
    return seal_derived_receipt(receipt)  # type: ignore[return-value]


def _element_source_digest(element) -> str:
    if isinstance(element, ImageElementV1):
        return element.payload.asset_digest
    if isinstance(element, (TextElementV1, ShapeElementV1)):
        return canonical_digest(element.payload)
    return element.source_ref


def _supported_features(element, registry: SerializerRegistryV1) -> frozenset[str] | None:
    if isinstance(element, TextElementV1):
        return registry.text_features
    if isinstance(element, ImageElementV1):
        return registry.image_features
    if isinstance(element, ShapeElementV1):
        return registry.shape_features
    return None


__all__ = ["SerializerRegistryV1", "reconcile_and_issue_native_admission"]
