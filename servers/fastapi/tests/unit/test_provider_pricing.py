import pytest

from services.provider_pricing import (
    ProviderModelPricing,
    ProviderPricingCatalog,
    calculate_actual_usage_cost,
)
from services.provider_pricing_registry import (
    PublicProviderPricingUnavailable,
    resolve_public_provider_pricing,
)
from utils.runtime_settings import RuntimeSettings


def test_actual_usage_cost_is_revision_pinned_and_rounds_each_class_up() -> None:
    pricing = ProviderModelPricing(
        revision="pricing-2026-08-30",
        provider_id="fixture-provider",
        model_id="fixture-model",
        input_microunits_per_million_tokens=2_000_000,
        output_microunits_per_million_tokens=8_000_000,
        cached_input_microunits_per_million_tokens=500_000,
    )

    cost = calculate_actual_usage_cost(
        pricing,
        input_tokens=1_001,
        output_tokens=501,
        cached_input_tokens=1,
    )

    assert cost.pricing_revision == "pricing-2026-08-30"
    assert cost.input_cost_microunits == 2_000
    assert cost.output_cost_microunits == 4_008
    assert cost.cached_input_cost_microunits == 1
    assert cost.total_cost_microunits == 6_009


def test_catalog_requires_exact_provider_model_revision() -> None:
    pricing = ProviderModelPricing(
        revision="pricing-r1",
        provider_id="provider-a",
        model_id="model-a",
        input_microunits_per_million_tokens=1,
        output_microunits_per_million_tokens=2,
    )
    catalog = ProviderPricingCatalog((pricing,))

    assert (
        catalog.resolve(
            revision="pricing-r1", provider_id="provider-a", model_id="model-a"
        )
        is pricing
    )
    with pytest.raises(LookupError, match="provider_model_pricing_unavailable"):
        catalog.resolve(
            revision="pricing-r2", provider_id="provider-a", model_id="model-a"
        )


def test_public_runtime_pricing_resolves_exact_revision_and_fails_closed() -> None:
    settings = RuntimeSettings(
        {
            "PUBLIC_PROVIDER_PRICING_REVISION": "pricing-2026-08-30",
            "PUBLIC_PROVIDER_PRICING_CATALOG": (
                '{"schemaVersion":"public-provider-pricing-catalog/v1",'
                '"revision":"pricing-2026-08-30","entries":[{'
                '"providerId":"openai","modelId":"gpt-test",'
                '"inputMicrounitsPerMillionTokens":100,'
                '"outputMicrounitsPerMillionTokens":200}]}'
            ),
        }
    )

    pricing = resolve_public_provider_pricing(
        provider_id="openai",
        model_id="gpt-test",
        settings=settings,
    )
    assert pricing.revision == "pricing-2026-08-30"

    with pytest.raises(
        PublicProviderPricingUnavailable,
        match="public_provider_pricing_exact_match_required",
    ):
        resolve_public_provider_pricing(
            provider_id="openai",
            model_id="gpt-missing",
            settings=settings,
        )
