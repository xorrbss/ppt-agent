"""Resolve exact public provider pricing from one immutable runtime snapshot."""

from __future__ import annotations

import json
from typing import Any

from services.provider_pricing import ProviderModelPricing, ProviderPricingCatalog
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings


class PublicProviderPricingUnavailable(RuntimeError):
    code = "public_provider_pricing_unavailable"
    retryable = False


def _entry(value: object, *, revision: str) -> ProviderModelPricing:
    if not isinstance(value, dict):
        raise PublicProviderPricingUnavailable("public_provider_pricing_catalog_invalid")
    try:
        return ProviderModelPricing(
            revision=revision,
            provider_id=str(value["providerId"]),
            model_id=str(value["modelId"]),
            input_microunits_per_million_tokens=int(
                value["inputMicrounitsPerMillionTokens"]
            ),
            output_microunits_per_million_tokens=int(
                value["outputMicrounitsPerMillionTokens"]
            ),
            cached_input_microunits_per_million_tokens=(
                int(value["cachedInputMicrounitsPerMillionTokens"])
                if value.get("cachedInputMicrounitsPerMillionTokens") is not None
                else None
            ),
            currency=str(value.get("currency") or "USD"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PublicProviderPricingUnavailable(
            "public_provider_pricing_catalog_invalid"
        ) from error


def resolve_public_provider_pricing(
    *,
    provider_id: str,
    model_id: str,
    settings: RuntimeSettings | None = None,
) -> ProviderModelPricing:
    """Fail closed unless the active revision has an exact provider/model rate."""

    snapshot = resolve_runtime_settings(settings)
    revision = str(snapshot.get("PUBLIC_PROVIDER_PRICING_REVISION") or "").strip()
    raw_catalog = snapshot.get("PUBLIC_PROVIDER_PRICING_CATALOG")
    if not revision or not isinstance(raw_catalog, str) or not raw_catalog.strip():
        raise PublicProviderPricingUnavailable("public_provider_pricing_required")
    try:
        payload: Any = json.loads(raw_catalog)
    except json.JSONDecodeError as error:
        raise PublicProviderPricingUnavailable(
            "public_provider_pricing_catalog_invalid"
        ) from error
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != "public-provider-pricing-catalog/v1"
        or payload.get("revision") != revision
        or not isinstance(payload.get("entries"), list)
    ):
        raise PublicProviderPricingUnavailable(
            "public_provider_pricing_catalog_invalid"
        )
    catalog = ProviderPricingCatalog(
        tuple(_entry(value, revision=revision) for value in payload["entries"])
    )
    try:
        return catalog.resolve(
            revision=revision,
            provider_id=provider_id,
            model_id=model_id,
        )
    except LookupError as error:
        raise PublicProviderPricingUnavailable(
            "public_provider_pricing_exact_match_required"
        ) from error


__all__ = [
    "PublicProviderPricingUnavailable",
    "resolve_public_provider_pricing",
]
